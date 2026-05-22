"""Yeelight onboarding flow: nmcli + credential POST + discovery poll."""

from __future__ import annotations

from typing import Any

from yeelight_core.onboard import OnboardDeps, OnboardResult, run


class _FakeNmcli:
    """Records nmcli verbs; programmable per-verb success/failure."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_on = fail_on or set()

    async def __call__(self, *args: str) -> tuple[int, str]:
        self.calls.append(args)
        verb = args[0] if args else ""
        if verb in self.fail_on:
            return 1, f"nmcli {verb} failed"
        if args[:3] == ("-t", "-f", "GENERAL.CONNECTION"):
            return 0, "GENERAL.CONNECTION:HomeNet\n"
        return 0, ""


async def _no_sleep(_seconds: float) -> None:
    return None


async def test_onboard_happy_path() -> None:
    nmcli = _FakeNmcli()
    posted: list[tuple[str, dict[str, Any]]] = []

    async def fake_post(url: str, json: dict[str, Any]) -> tuple[int, str]:
        posted.append((url, json))
        return 200, '{"id":1,"result":["ok"]}'

    async def fake_discover() -> list[dict[str, Any]]:
        return [{"mac": "aabbccddeeff", "ip": "192.168.1.30", "port": 55443,
                 "rssi": None, "model": "color3"}]

    deps = OnboardDeps(
        nmcli=nmcli, http_post=fake_post, discover=fake_discover, sleep=_no_sleep
    )
    result = await run(
        ssid="HomeNet", password="s3cret",
        setup_ssid="yeelink-light-color3_miap2F2C", timeout_s=30,
        known_macs=set(), deps=deps,
    )
    assert isinstance(result, OnboardResult)
    assert result.status == "ok"
    assert result.onboarded == [
        {"mac": "aabbccddeeff", "ip": "192.168.1.30", "name": None, "rssi": None}
    ]
    # credentials POSTed with the home ssid/password
    assert posted and posted[0][1]["ssid"] == "HomeNet"
    assert posted[0][1]["passwd"] == "s3cret"
    # Wi-Fi restored: a `connection up` for the saved profile was issued.
    assert any(c[0] == "connection" and c[1] == "up" for c in nmcli.calls)


async def test_onboard_setup_ap_connect_fails_reverts_and_errors() -> None:
    # nmcli `device` verb fails -> the connect-to-setup-AP step fails.
    nmcli = _FakeNmcli(fail_on={"device"})

    async def fake_post(url: str, json: dict[str, Any]) -> tuple[int, str]:
        raise AssertionError("must not POST when setup-AP connect failed")

    async def fake_discover() -> list[dict[str, Any]]:
        return []

    deps = OnboardDeps(
        nmcli=nmcli, http_post=fake_post, discover=fake_discover, sleep=_no_sleep
    )
    result = await run(
        ssid="HomeNet", password="s3cret",
        setup_ssid="yeelink-light-color3_miap2F2C", timeout_s=30,
        known_macs=set(), deps=deps,
    )
    assert result.status == "error"
    assert "setup AP" in result.error
    # Home Wi-Fi still restored despite the failure.
    assert any(c[0] == "connection" and c[1] == "up" for c in nmcli.calls)


async def test_onboard_credential_post_fails_reverts_and_errors() -> None:
    nmcli = _FakeNmcli()

    async def fake_post(url: str, json: dict[str, Any]) -> tuple[int, str]:
        raise ConnectionError("bulb dropped the socket")

    async def fake_discover() -> list[dict[str, Any]]:
        return []

    deps = OnboardDeps(
        nmcli=nmcli, http_post=fake_post, discover=fake_discover, sleep=_no_sleep
    )
    result = await run(
        ssid="HomeNet", password="s3cret",
        setup_ssid="yeelink-light-color3_miap2F2C", timeout_s=30,
        known_macs=set(), deps=deps,
    )
    assert result.status == "error"
    assert "credential" in result.error.lower()
    assert any(c[0] == "connection" and c[1] == "up" for c in nmcli.calls)


async def test_onboard_joined_but_not_discovered_times_out() -> None:
    nmcli = _FakeNmcli()

    async def fake_post(url: str, json: dict[str, Any]) -> tuple[int, str]:
        return 200, '{"id":1,"result":["ok"]}'

    async def fake_discover() -> list[dict[str, Any]]:
        return []  # bulb joined Wi-Fi but never shows up in discovery

    deps = OnboardDeps(
        nmcli=nmcli, http_post=fake_post, discover=fake_discover, sleep=_no_sleep
    )
    result = await run(
        ssid="HomeNet", password="s3cret",
        setup_ssid="yeelink-light-color3_miap2F2C", timeout_s=12,
        known_macs=set(), deps=deps,
    )
    assert result.status == "timeout"
    assert result.attempted_seconds == 12
    assert result.onboarded == []


async def test_onboard_second_known_path_used_when_first_404s() -> None:
    nmcli = _FakeNmcli()
    posted_urls: list[str] = []

    async def fake_post(url: str, json: dict[str, Any]) -> tuple[int, str]:
        posted_urls.append(url)
        # First firmware path 404s; the daemon tries the second.
        if url.endswith("/api/v1/wifi/set"):
            return 404, "not found"
        return 200, '{"id":1,"result":["ok"]}'

    async def fake_discover() -> list[dict[str, Any]]:
        return [{"mac": "aabbccddeeff", "ip": "192.168.1.31", "port": 55443,
                 "rssi": None, "model": "color3"}]

    deps = OnboardDeps(
        nmcli=nmcli, http_post=fake_post, discover=fake_discover, sleep=_no_sleep
    )
    result = await run(
        ssid="HomeNet", password="s3cret",
        setup_ssid="yeelink-light-color3_miap5E13", timeout_s=30,
        known_macs=set(), deps=deps,
    )
    assert result.status == "ok"
    assert len(posted_urls) == 2  # fell through to the second known path
