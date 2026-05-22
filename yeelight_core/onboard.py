"""Yeelight Wi-Fi onboarding: connect to the bulb's setup AP, POST the home
Wi-Fi credentials, restore Wi-Fi, then poll discovery for the new bulb.

Every external boundary (nmcli, the credential POST, discovery, sleep) is
injected through `OnboardDeps` so the whole flow is unit-testable with no
network. Per spec §4.2, a setup-AP connect failure or a credential-POST
failure both still restore the home Wi-Fi before returning an error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Yeelight setup-mode bulbs serve a tiny HTTP API at this gateway address.
SETUP_GATEWAY = "192.168.4.1"
# Firmware varies; the daemon tries these paths in order (spec §4.2 step 3).
KNOWN_WIFI_SET_PATHS = ("/api/v1/wifi/set", "/api/v1/config/wifi")
# Seconds to wait for the bulb to ACK + disconnect before restoring Wi-Fi.
ACK_WAIT_S = 5.0
# Discovery poll cadence while waiting for the bulb to rejoin the LAN.
POLL_INTERVAL_S = 5.0
# The wireless interface nmcli operates on (matches the homelab; see CLAUDE.md).
WIFI_IFACE = "wlo1"

# Injectable boundary signatures.
NmcliFn = Callable[..., Awaitable[tuple[int, str]]]
HttpPostFn = Callable[[str, dict[str, Any]], Awaitable[tuple[int, str]]]
DiscoverFn = Callable[[], Awaitable[list[dict[str, Any]]]]
SleepFn = Callable[[float], Awaitable[None]]


@dataclass
class OnboardDeps:
    """Injected side-effecting boundaries (real impls wired in main.py)."""

    nmcli: NmcliFn
    http_post: HttpPostFn
    discover: DiscoverFn
    sleep: SleepFn = field(default=asyncio.sleep)


@dataclass
class OnboardResult:
    """Outcome of an onboarding attempt.

    status is one of: "ok" | "timeout" | "error" | "validation".
    """

    status: str
    onboarded: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    attempted_seconds: int = 0


async def _save_current_connection(deps: OnboardDeps) -> str | None:
    """Return the name of the currently-active Wi-Fi connection, or None."""
    rc, out = await deps.nmcli(
        "-t", "-f", "GENERAL.CONNECTION", "device", "show", WIFI_IFACE
    )
    if rc != 0:
        return None
    line = out.strip().splitlines()[0] if out.strip() else ""
    name = line.split(":", 1)[-1].strip() if ":" in line else line.strip()
    return name or None


async def _restore_connection(deps: OnboardDeps, name: str | None) -> None:
    """Best-effort: bring the saved home Wi-Fi connection back up."""
    if not name:
        return
    try:
        rc, out = await deps.nmcli("connection", "up", name)
        if rc != 0:
            log.warning("nmcli connection up %s failed: %s", name, out)
    except Exception as exc:
        log.warning("restoring Wi-Fi %r raised: %r", name, exc)


async def _post_credentials(
    deps: OnboardDeps, ssid: str, password: str
) -> tuple[bool, str]:
    """Try each known firmware path; True on the first 2xx response."""
    body = {"ssid": ssid, "passwd": password}
    last_detail = "no firmware path responded"
    for path in KNOWN_WIFI_SET_PATHS:
        url = f"http://{SETUP_GATEWAY}{path}"
        status, text = await deps.http_post(url, body)
        if 200 <= status < 300:
            return True, text
        last_detail = f"{url} -> HTTP {status}: {text}"
        log.debug("wifi-set path %s rejected: %s", path, last_detail)
    return False, last_detail


async def run(
    *,
    ssid: str,
    password: str,
    setup_ssid: str,
    timeout_s: int,
    known_macs: set[str],
    deps: OnboardDeps,
) -> OnboardResult:
    """Execute the full onboarding flow. Never raises; always returns a result."""
    if not ssid or not setup_ssid:
        return OnboardResult(status="validation", error="ssid and setup_ssid required")

    saved = await _save_current_connection(deps)

    # 1. Connect to the bulb's open setup AP.
    rc, out = await deps.nmcli(
        "device", "wifi", "connect", setup_ssid, "password", ""
    )
    if rc != 0:
        await _restore_connection(deps, saved)
        return OnboardResult(
            status="error", error=f"could not join setup AP {setup_ssid!r}: {out}"
        )

    # 2. POST the home Wi-Fi credentials to the bulb. Restore Wi-Fi no
    #    matter what happens here.
    try:
        ok, detail = await _post_credentials(deps, ssid, password)
    except Exception as exc:
        await _restore_connection(deps, saved)
        return OnboardResult(
            status="error", error=f"credential POST failed: {exc!r}"
        )
    if not ok:
        await _restore_connection(deps, saved)
        return OnboardResult(
            status="error", error=f"credential POST rejected: {detail}"
        )

    # 3. Give the bulb a moment to ACK + disconnect, then restore Wi-Fi.
    await deps.sleep(ACK_WAIT_S)
    await _restore_connection(deps, saved)

    # 4. Poll discovery until a new MAC (not in known_macs) appears.
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            found = await deps.discover()
        except Exception as exc:
            log.debug("discovery during onboard poll failed: %r", exc)
            found = []
        fresh = [b for b in found if b["mac"] not in known_macs]
        if fresh:
            return OnboardResult(
                status="ok",
                onboarded=[
                    {
                        "mac": b["mac"],
                        "ip": b["ip"],
                        "name": None,
                        "rssi": b.get("rssi"),
                    }
                    for b in fresh
                ],
            )
        await deps.sleep(POLL_INTERVAL_S)

    return OnboardResult(status="timeout", attempted_seconds=timeout_s)


def parse_jsonrpc_ack(text: str) -> bool:
    """True if `text` is a JSON-RPC reply carrying a result (not an error)."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(obj, dict) and "result" in obj and "error" not in obj
