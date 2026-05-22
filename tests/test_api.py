"""FastAPI single-bulb route tests using TestClient + a stub driver."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from yeelight_core.api import create_app
from yeelight_core.driver import YeelightError
from yeelight_core.registry import Registry


class StubDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, dict]] = []
        self.state: dict[str, Any] = {"power": "on", "bright": "100", "ct": "2700"}

    async def get_state(self, ip: str, port: int = 55443) -> dict:
        self.calls.append(("get_state", ip, port, {}))
        return self.state

    async def set_power(self, ip: str, port: int, *, on: bool) -> dict:
        self.calls.append(("set_power", ip, port, {"on": on}))
        return {"success": True}

    async def set_brightness(self, ip: str, port: int, level: int) -> dict:
        self.calls.append(("set_brightness", ip, port, {"level": level}))
        return {"success": True}

    async def set_temp(self, ip: str, port: int, kelvin: int) -> dict:
        self.calls.append(("set_temp", ip, port, {"kelvin": kelvin}))
        return {"success": True}

    async def set_color(self, ip: str, port: int, r: int, g: int, b: int) -> dict:
        self.calls.append(("set_color", ip, port, {"r": r, "g": g, "b": b}))
        return {"success": True}

    async def set_name(self, ip: str, port: int, name: str) -> dict:
        self.calls.append(("set_name", ip, port, {"name": name}))
        return {"success": True}


async def _fake_discover() -> int:
    return 0


@pytest.fixture()
def client(tmp_path: Path) -> tuple[TestClient, Registry, StubDriver]:
    reg = Registry(tmp_path / "state.json")
    reg.upsert_discovered({
        "mac": "f0b4299a1b2c", "ip": "192.168.1.20", "port": 55443, "rssi": -55,
    })
    stub = StubDriver()
    app = create_app(registry=reg, driver=stub, run_discovery=_fake_discover)
    return TestClient(app), reg, stub


def test_health(client) -> None:
    c, *_ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_list_bulbs_includes_protocol(client) -> None:
    c, *_ = client
    r = c.get("/bulbs")
    assert r.status_code == 200
    bulbs = r.json()["bulbs"]
    assert len(bulbs) == 1
    assert bulbs[0]["mac"] == "f0b4299a1b2c"
    assert all(b["protocol"] == "yeelight" for b in bulbs)


def test_get_bulb_by_mac_includes_protocol(client) -> None:
    c, _, stub = client
    r = c.get("/bulb/f0b4299a1b2c")
    assert r.status_code == 200
    assert r.json()["protocol"] == "yeelight"
    assert r.json()["power"] == "on"
    assert stub.calls[0] == ("get_state", "192.168.1.20", 55443, {})


def test_get_bulb_404(client) -> None:
    c, *_ = client
    assert c.get("/bulb/notathing").status_code == 404


def test_default_endpoint(client) -> None:
    c, *_ = client
    r = c.get("/bulbs/default")
    assert r.status_code == 200
    assert r.json()["protocol"] == "yeelight"


def test_on_off(client) -> None:
    c, _, stub = client
    assert c.post("/bulb/f0b4299a1b2c/on").status_code == 200
    assert stub.calls[-1] == ("set_power", "192.168.1.20", 55443, {"on": True})
    assert c.post("/bulb/f0b4299a1b2c/off").status_code == 200
    assert stub.calls[-1] == ("set_power", "192.168.1.20", 55443, {"on": False})


def test_brightness_temp_color_validation_and_dispatch(client) -> None:
    c, _, stub = client
    assert c.post("/bulb/f0b4299a1b2c/brightness", json={"level": 999}).status_code == 422
    assert c.post("/bulb/f0b4299a1b2c/brightness", json={"level": 50}).status_code == 200
    assert stub.calls[-1] == ("set_brightness", "192.168.1.20", 55443, {"level": 50})
    assert c.post("/bulb/f0b4299a1b2c/temp", json={"kelvin": 1000}).status_code == 422
    assert c.post("/bulb/f0b4299a1b2c/temp", json={"kelvin": 4000}).status_code == 200
    assert stub.calls[-1] == ("set_temp", "192.168.1.20", 55443, {"kelvin": 4000})
    assert c.post("/bulb/f0b4299a1b2c/color", json={"r": 255, "g": 0, "b": 100}).status_code == 200
    assert stub.calls[-1] == ("set_color", "192.168.1.20", 55443, {"r": 255, "g": 0, "b": 100})


def test_scene_by_name_dispatches_temp_action(client) -> None:
    c, _, stub = client
    # "movie" is a temp scene (kelvin 2700, brightness 30).
    r = c.post("/bulb/f0b4299a1b2c/scene", json={"scene": "movie"})
    assert r.status_code == 200
    kinds = [call[0] for call in stub.calls]
    assert "set_temp" in kinds and "set_brightness" in kinds


def test_scene_unknown_returns_400(client) -> None:
    c, *_ = client
    assert c.post("/bulb/f0b4299a1b2c/scene", json={"scene": "nope"}).status_code == 400


def test_rename(client) -> None:
    c, reg, _ = client
    r = c.post("/bulb/f0b4299a1b2c/name", json={"name": "kitchen"})
    assert r.status_code == 200
    assert reg.resolve("kitchen") is not None


def test_scenes_list(client) -> None:
    c, *_ = client
    r = c.get("/scenes")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["scenes"]]
    assert "movie" in names and "night" in names


def test_driver_error_returns_504(tmp_path: Path) -> None:
    class FailingDriver(StubDriver):
        async def set_power(self, ip: str, port: int, *, on: bool) -> dict:
            raise YeelightError("simulated TCP timeout")

        async def get_state(self, ip: str, port: int = 55443) -> dict:
            raise YeelightError("simulated TCP timeout")

    reg = Registry(tmp_path / "state.json")
    reg.upsert_discovered({
        "mac": "f0b4299a1b2c", "ip": "192.168.1.20", "port": 55443, "rssi": -55,
    })
    app = create_app(registry=reg, driver=FailingDriver(), run_discovery=_fake_discover)
    c = TestClient(app)
    r = c.post("/bulb/f0b4299a1b2c/on")
    assert r.status_code == 504
    assert "simulated TCP timeout" in r.json()["detail"]
    assert c.get("/bulb/f0b4299a1b2c").status_code == 504


def test_empty_registry_409_on_default(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "state.json")
    app = create_app(registry=reg, driver=StubDriver(), run_discovery=_fake_discover)
    c = TestClient(app)
    assert c.get("/bulbs/default").status_code == 409


def test_discover_endpoint(client) -> None:
    c, *_ = client
    r = c.post("/discover", json={"passive": False})
    assert r.status_code == 200
    body = r.json()
    assert body["discovered"] == 0
    assert body["total"] == 1
