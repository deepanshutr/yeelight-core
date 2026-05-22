"""POST /onboard route: maps OnboardResult to the A1 HTTP envelope."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.test_api import StubDriver, _fake_discover
from yeelight_core.api import create_app
from yeelight_core.onboard import OnboardDeps
from yeelight_core.registry import Registry


def _client_with_onboard(
    tmp_path: Path, *, status_seq: list[tuple[int, str]],
    discover_result: list[dict[str, Any]],
) -> TestClient:
    reg = Registry(tmp_path / "state.json")

    class _Nmcli:
        async def __call__(self, *args: str) -> tuple[int, str]:
            if args[:3] == ("-t", "-f", "GENERAL.CONNECTION"):
                return 0, "GENERAL.CONNECTION:HomeNet\n"
            return 0, ""

    seq = list(status_seq)

    async def fake_post(url: str, json: dict[str, Any]) -> tuple[int, str]:
        return seq.pop(0) if seq else (200, '{"id":1,"result":["ok"]}')

    async def fake_discover_fn() -> list[dict[str, Any]]:
        return discover_result

    async def no_sleep(_s: float) -> None:
        return None

    deps = OnboardDeps(
        nmcli=_Nmcli(), http_post=fake_post,
        discover=fake_discover_fn, sleep=no_sleep,
    )
    app = create_app(
        registry=reg, driver=StubDriver(),
        run_discovery=_fake_discover, onboard_deps=deps,
    )
    return TestClient(app)


def test_onboard_success_returns_200(tmp_path: Path) -> None:
    c = _client_with_onboard(
        tmp_path,
        status_seq=[(200, '{"id":1,"result":["ok"]}')],
        discover_result=[{"mac": "aabbccddeeff", "ip": "192.168.1.40",
                          "port": 55443, "rssi": None, "model": "color3"}],
    )
    r = c.post("/onboard", json={"ssid": "HomeNet", "password": "pw", "timeout_s": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["onboarded"][0]["mac"] == "aabbccddeeff"


def test_onboard_timeout_returns_408(tmp_path: Path) -> None:
    c = _client_with_onboard(
        tmp_path,
        status_seq=[(200, '{"id":1,"result":["ok"]}')],
        discover_result=[],  # bulb never appears
    )
    r = c.post("/onboard", json={"ssid": "HomeNet", "password": "pw", "timeout_s": 11})
    assert r.status_code == 408
    body = r.json()["detail"]
    assert body["error"] == "timeout"
    assert body["attempted_seconds"] == 11


def test_onboard_missing_ssid_returns_422(tmp_path: Path) -> None:
    c = _client_with_onboard(tmp_path, status_seq=[], discover_result=[])
    r = c.post("/onboard", json={"password": "pw"})
    assert r.status_code == 422


def test_onboard_internal_error_returns_500(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "state.json")

    class _Nmcli:
        async def __call__(self, *args: str) -> tuple[int, str]:
            if args[:3] == ("-t", "-f", "GENERAL.CONNECTION"):
                return 0, "GENERAL.CONNECTION:HomeNet\n"
            # the connect-to-setup-AP `device` verb fails
            return (1, "device error") if args[0] == "device" else (0, "")

    async def fake_post(url: str, json: dict[str, Any]) -> tuple[int, str]:
        return 200, "ok"

    async def fake_discover_fn() -> list[dict[str, Any]]:
        return []

    async def no_sleep(_s: float) -> None:
        return None

    deps = OnboardDeps(
        nmcli=_Nmcli(), http_post=fake_post,
        discover=fake_discover_fn, sleep=no_sleep,
    )
    app = create_app(
        registry=reg, driver=StubDriver(),
        run_discovery=_fake_discover, onboard_deps=deps,
    )
    c = TestClient(app)
    r = c.post("/onboard", json={"ssid": "HomeNet", "password": "pw"})
    assert r.status_code == 500
    assert r.json()["detail"]["error"] == "yeelight_onboard_internal"


def test_onboard_unconfigured_returns_501(tmp_path: Path) -> None:
    """If create_app got no onboard_deps, /onboard reports not-configured."""
    reg = Registry(tmp_path / "state.json")
    app = create_app(
        registry=reg, driver=StubDriver(), run_discovery=_fake_discover,
    )
    c = TestClient(app)
    r = c.post("/onboard", json={"ssid": "HomeNet", "password": "pw"})
    assert r.status_code == 501
    assert r.json()["detail"]["error"] == "yeelight_onboard_unconfigured"
