"""LAN discovery for Yeelight bulbs via M-SEARCH multicast.

Yeelight uses an SSDP-like discovery on its own multicast group
239.255.255.250:1982 (NOT standard UPnP port 1900). Each bulb unicasts
back an HTTP-style response; we parse the headers into a registry dict.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Any

log = logging.getLogger(__name__)

# The exact M-SEARCH datagram Yeelight bulbs answer (per the Yeelight
# third-party control protocol spec). MAN must be the quoted discover verb;
# ST must be "wifi_bulb".
_MSEARCH_PAYLOAD = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1982\r\n"
    'MAN: "ssdp:discover"\r\n'
    "ST: wifi_bulb\r\n"
    "\r\n"
).encode()


def _normalise_mac(s: str) -> str:
    return "".join(c for c in s.lower() if c in "0123456789abcdef")


def parse_msearch_response(raw: str) -> dict[str, Any]:
    """Parse one bulb's M-SEARCH reply into a registry-shaped dict.

    Raises ValueError if the mandatory `id` or `Location` header is absent.
    """
    headers: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()

    raw_id = headers.get("id")
    if not raw_id:
        raise ValueError(f"M-SEARCH reply missing id header: {raw!r}")
    # Strip the leading "0x" prefix that Yeelight bulbs include in the id.
    id_stripped = raw_id.strip()
    if id_stripped.lower().startswith("0x"):
        id_stripped = id_stripped[2:]
    mac = _normalise_mac(id_stripped)
    if not mac:
        raise ValueError(f"M-SEARCH reply has unparseable id {raw_id!r}")

    location = headers.get("location")
    if not location:
        raise ValueError(f"M-SEARCH reply missing Location header: {raw!r}")
    # Location: yeelight://<ip>:<port>
    hostport = location.split("//", 1)[-1]
    ip, _, port_s = hostport.partition(":")
    try:
        port = int(port_s) if port_s else 55443
    except ValueError:
        port = 55443

    return {
        "mac": mac,
        "ip": ip,
        "port": port,
        "rssi": None,  # Yeelight M-SEARCH carries no RSSI
        "model": headers.get("model"),
        "fw_ver": headers.get("fw_ver"),
        "support": headers.get("support"),
    }


async def _msearch_collect(
    multicast_addr: str, multicast_port: int, timeout_s: float
) -> list[str]:
    """Send one M-SEARCH datagram, collect raw reply strings for timeout_s."""
    loop = asyncio.get_running_loop()
    received: list[str] = []

    class _Collector(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            received.append(data.decode("utf-8", errors="replace"))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # TTL 2: cross at most one router hop; bulbs are on the local segment.
    sock.setsockopt(
        socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 2)
    )
    sock.setblocking(False)
    sock.bind(("0.0.0.0", 0))
    transport, _ = await loop.create_datagram_endpoint(_Collector, sock=sock)
    try:
        transport.sendto(_MSEARCH_PAYLOAD, (multicast_addr, multicast_port))
        await asyncio.sleep(timeout_s)
    finally:
        transport.close()
    return received


async def discover(
    *,
    multicast_addr: str = "239.255.255.250",
    multicast_port: int = 1982,
    timeout_s: float = 2.0,
) -> list[dict[str, Any]]:
    """Discover Yeelight bulbs via M-SEARCH; dedup by MAC (first reply wins).

    A single multicast datagram with no retries — the speculative-discovery
    path never retries (per [[speculative-no-retry]]); the retrying path
    lives only in the per-bulb driver for known-good targets.
    """
    raws = await _msearch_collect(multicast_addr, multicast_port, timeout_s)
    seen: dict[str, dict[str, Any]] = {}
    for raw in raws:
        try:
            parsed = parse_msearch_response(raw)
        except ValueError as exc:
            log.debug("dropping unparseable M-SEARCH reply: %r", exc)
            continue
        seen.setdefault(parsed["mac"], parsed)
    return list(seen.values())
