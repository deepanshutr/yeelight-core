"""POST /bulb/all/{op} broadcast family — amendment A2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.test_api import StubDriver, _fake_discover
from yeelight_core.api import create_app
from yeelight_core.driver import YeelightError
from yeelight_core.registry import Registry


def _three_bulb_client(
    tmp_path: Path, driver: Any, concurrency: int = 16
) -> tuple[TestClient, Registry]:
    reg = Registry(tmp_path / "state.json")
    for i, mac in enumerate(("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc")):
        reg.upsert_discovered({
            "mac": mac, "ip": f"192.168.1.{20 + i}", "port": 55443, "rssi": -50,
        })
    app = create_app(
        registry=reg, driver=driver, run_discovery=_fake_discover,
        all_concurrency=concurrency,
    )
    return TestClient(app), reg


def test_all_on_flips_every_bulb(tmp_path: Path) -> None:
    stub = StubDriver()
    c, _ = _three_bulb_client(tmp_path, stub)
    r = c.post("/bulb/all/on")
    assert r.status_code == 200
    body = r.json()
    assert body["op"] == "on"
    assert body["total"] == 3
    assert body["ok"] == 3
    assert body["failed"] == 0
    assert {res["mac"] for res in body["results"]} == {
        "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"
    }
    assert all(res["ok"] for res in body["results"])
    # every bulb got a set_power(on=True)
    assert sum(1 for call in stub.calls if call[0] == "set_power") == 3


def test_all_off_brightness_temp_color_scene(tmp_path: Path) -> None:
    stub = StubDriver()
    c, _ = _three_bulb_client(tmp_path, stub)
    assert c.post("/bulb/all/off").json()["ok"] == 3
    assert c.post("/bulb/all/brightness", json={"level": 40}).json()["ok"] == 3
    assert c.post("/bulb/all/temp", json={"kelvin": 3000}).json()["ok"] == 3
    assert c.post("/bulb/all/color", json={"r": 10, "g": 20, "b": 30}).json()["ok"] == 3
    r = c.post("/bulb/all/scene", json={"scene": "movie"})
    assert r.status_code == 200
    assert r.json()["ok"] == 3


def test_all_partial_failure_one_bulb_times_out(tmp_path: Path) -> None:
    class FlakyDriver(StubDriver):
        async def set_power(self, ip: str, port: int, *, on: bool) -> dict:
            if ip == "192.168.1.21":  # the second bulb
                raise YeelightError("udp_timeout")
            return await super().set_power(ip, port, on=on)

    c, _ = _three_bulb_client(tmp_path, FlakyDriver())
    r = c.post("/bulb/all/on")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["ok"] == 2
    assert body["failed"] == 1
    failed = [res for res in body["results"] if not res["ok"]]
    assert len(failed) == 1
    assert failed[0]["mac"] == "bbbbbbbbbbbb"
    assert "udp_timeout" in failed[0]["error"]


def test_all_every_bulb_fails(tmp_path: Path) -> None:
    class DeadDriver(StubDriver):
        async def set_power(self, ip: str, port: int, *, on: bool) -> dict:
            raise YeelightError("all dead")

    c, _ = _three_bulb_client(tmp_path, DeadDriver())
    body = c.post("/bulb/all/on").json()
    assert body["total"] == 3
    assert body["ok"] == 0
    assert body["failed"] == 3
    assert all(not res["ok"] for res in body["results"])


def test_all_empty_registry_returns_zeroes(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "state.json")
    app = create_app(
        registry=reg, driver=StubDriver(), run_discovery=_fake_discover,
    )
    c = TestClient(app)
    r = c.post("/bulb/all/on")
    assert r.status_code == 200
    body = r.json()
    assert body["op"] == "on"
    assert body["total"] == 0
    assert body["ok"] == 0
    assert body["failed"] == 0
    assert body["results"] == []
    assert "duration_ms" in body


def test_all_concurrency_cap_is_respected(tmp_path: Path) -> None:
    """With concurrency=1 the fan-out never runs two bulbs at once."""
    import asyncio

    in_flight = 0
    peak = 0

    class CountingDriver(StubDriver):
        async def set_power(self, ip: str, port: int, *, on: bool) -> dict:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return {"success": True}

    c, _ = _three_bulb_client(tmp_path, CountingDriver(), concurrency=1)
    r = c.post("/bulb/all/on")
    assert r.status_code == 200
    assert r.json()["ok"] == 3
    assert peak == 1  # never exceeded the cap


def test_all_unknown_op_returns_404(tmp_path: Path) -> None:
    stub = StubDriver()
    c, _ = _three_bulb_client(tmp_path, stub)
    assert c.post("/bulb/all/explode").status_code == 404
