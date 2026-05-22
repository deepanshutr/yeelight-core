"""FastAPI HTTP surface — identical contract to wiz-core (spec §1)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .driver import YeelightError
from .onboard import OnboardDeps, OnboardResult
from .onboard import run as run_onboard
from .registry import Bulb, Registry
from .scenes import SCENES, SceneAction, resolve_scene


class _Driver(Protocol):
    async def get_state(self, ip: str, port: int = ...) -> dict[str, Any]: ...
    async def set_power(self, ip: str, port: int, *, on: bool) -> dict[str, Any]: ...
    async def set_brightness(
        self, ip: str, port: int, level: int
    ) -> dict[str, Any]: ...
    async def set_temp(self, ip: str, port: int, kelvin: int) -> dict[str, Any]: ...
    async def set_color(
        self, ip: str, port: int, r: int, g: int, b: int
    ) -> dict[str, Any]: ...
    async def set_name(self, ip: str, port: int, name: str) -> dict[str, Any]: ...


class BrightnessIn(BaseModel):
    level: Annotated[int, Field(ge=10, le=100)]


class TempIn(BaseModel):
    kelvin: Annotated[int, Field(ge=2200, le=6500)]


class ColorIn(BaseModel):
    r: Annotated[int, Field(ge=0, le=255)]
    g: Annotated[int, Field(ge=0, le=255)]
    b: Annotated[int, Field(ge=0, le=255)]


class SceneIn(BaseModel):
    scene: str | int
    speed: int | None = Field(None, ge=10, le=200)


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class DiscoverIn(BaseModel):
    passive: bool = False


class OnboardIn(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    setup_ssid: str | None = Field(None, max_length=64)
    timeout_s: int = Field(60, ge=10, le=300)


def _bulb_payload(b: Bulb) -> dict[str, Any]:
    return {
        "protocol": "yeelight",
        "mac": b.mac,
        "name": b.name,
        "ip": b.last_ip,
        "port": b.port,
        "rssi": b.last_rssi,
        "module": b.module,
        "fw_version": b.fw_version,
        "cct_range": list(b.cct_range) if b.cct_range else None,
        "discovered_at": b.discovered_at,
        "last_seen": b.last_seen,
    }


async def _apply_scene(driver: _Driver, b: Bulb, action: SceneAction) -> None:
    """Dispatch a resolved SceneAction to the right driver call(s)."""
    if action.kind == "rgb":
        assert action.r is not None and action.g is not None and action.b is not None
        await driver.set_color(b.last_ip, b.port, action.r, action.g, action.b)
    elif action.kind == "temp":
        assert action.kelvin is not None
        await driver.set_temp(b.last_ip, b.port, action.kelvin)
    if action.brightness is not None:
        await driver.set_brightness(b.last_ip, b.port, action.brightness)


def create_app(
    *,
    registry: Registry,
    driver: _Driver,
    run_discovery: Callable[[], Coroutine[Any, Any, int]],
    onboard_deps: OnboardDeps | None = None,
) -> FastAPI:
    app = FastAPI(title="yeelight-core")

    def resolve_or_404(target: str) -> Bulb:
        b = registry.resolve(target)
        if b is None:
            raise HTTPException(status_code=404, detail=f"no bulb matches {target!r}")
        return b

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/bulbs")
    async def list_bulbs() -> dict[str, Any]:
        return {"bulbs": [_bulb_payload(b) for b in registry.all()]}

    @app.get("/bulbs/default")
    async def default_bulb() -> dict[str, Any]:
        b = registry.default()
        if b is None:
            raise HTTPException(409, "no bulbs known; POST /discover first")
        try:
            state = await driver.get_state(b.last_ip, b.port)
        except YeelightError as e:
            raise HTTPException(504, str(e)) from e
        return {**_bulb_payload(b), **state}

    @app.post("/discover")
    async def discover(body: DiscoverIn) -> dict[str, Any]:
        n = await run_discovery()
        registry.flush()
        return {"discovered": n, "total": len(registry.all())}

    @app.get("/bulb/{target}")
    async def get_bulb(target: str) -> dict[str, Any]:
        b = resolve_or_404(target)
        try:
            state = await driver.get_state(b.last_ip, b.port)
        except YeelightError as e:
            raise HTTPException(504, str(e)) from e
        return {**_bulb_payload(b), **state}

    @app.post("/bulb/{target}/on")
    async def on(target: str) -> dict[str, Any]:
        b = resolve_or_404(target)
        try:
            return await driver.set_power(b.last_ip, b.port, on=True)
        except YeelightError as e:
            raise HTTPException(504, str(e)) from e

    @app.post("/bulb/{target}/off")
    async def off(target: str) -> dict[str, Any]:
        b = resolve_or_404(target)
        try:
            return await driver.set_power(b.last_ip, b.port, on=False)
        except YeelightError as e:
            raise HTTPException(504, str(e)) from e

    @app.post("/bulb/{target}/brightness")
    async def brightness(target: str, body: BrightnessIn) -> dict[str, Any]:
        b = resolve_or_404(target)
        try:
            return await driver.set_brightness(b.last_ip, b.port, int(body.level))
        except YeelightError as e:
            raise HTTPException(504, str(e)) from e

    @app.post("/bulb/{target}/temp")
    async def temp(target: str, body: TempIn) -> dict[str, Any]:
        b = resolve_or_404(target)
        try:
            return await driver.set_temp(b.last_ip, b.port, int(body.kelvin))
        except YeelightError as e:
            raise HTTPException(504, str(e)) from e

    @app.post("/bulb/{target}/color")
    async def color(target: str, body: ColorIn) -> dict[str, Any]:
        b = resolve_or_404(target)
        try:
            return await driver.set_color(
                b.last_ip, b.port, int(body.r), int(body.g), int(body.b)
            )
        except YeelightError as e:
            raise HTTPException(504, str(e)) from e

    @app.post("/bulb/{target}/scene")
    async def scene(target: str, body: SceneIn) -> dict[str, Any]:
        b = resolve_or_404(target)
        try:
            action = resolve_scene(body.scene)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        try:
            await _apply_scene(driver, b, action)
        except YeelightError as e:
            raise HTTPException(504, str(e)) from e
        return {"success": True, "scene": str(body.scene)}

    @app.post("/bulb/{target}/name")
    async def name(target: str, body: NameIn) -> dict[str, Any]:
        b = resolve_or_404(target)
        registry.rename(b.mac, body.name)
        registry.flush()
        # Best-effort: also push the name onto the bulb itself.
        try:
            await driver.set_name(b.last_ip, b.port, body.name)
        except YeelightError:
            pass
        return _bulb_payload(b)

    @app.get("/scenes")
    async def scenes() -> dict[str, Any]:
        return {"scenes": [{"name": nm} for nm in sorted(SCENES)]}

    @app.post("/onboard")
    async def onboard_route(body: OnboardIn) -> dict[str, Any]:
        if onboard_deps is None:
            raise HTTPException(
                status_code=501,
                detail={
                    "error": "yeelight_onboard_unconfigured",
                    "message": (
                        "onboarding deps were not wired into this app "
                        "(nmcli/httpx/discover); start via the daemon, "
                        "not a bare create_app()"
                    ),
                },
            )
        # Yeelight setup APs match yeelink-*; honour an explicit override.
        setup_ssid = body.setup_ssid or "yeelink-light"
        known = {b.mac for b in registry.all()}
        result: OnboardResult = await run_onboard(
            ssid=body.ssid,
            password=body.password,
            setup_ssid=setup_ssid,
            timeout_s=body.timeout_s,
            known_macs=known,
            deps=onboard_deps,
        )
        if result.status == "ok":
            # Fold any freshly-onboarded bulbs into the registry.
            for entry in result.onboarded:
                registry.upsert_discovered(
                    {
                        "mac": entry["mac"],
                        "ip": entry["ip"],
                        "port": 55443,
                        "rssi": entry.get("rssi"),
                    }
                )
            registry.flush()
            return {"onboarded": result.onboarded}
        if result.status == "timeout":
            raise HTTPException(
                status_code=408,
                detail={
                    "error": "timeout",
                    "attempted_seconds": result.attempted_seconds,
                },
            )
        if result.status == "validation":
            raise HTTPException(
                status_code=422,
                detail={"error": "validation", "message": result.error},
            )
        # status == "error"
        raise HTTPException(
            status_code=500,
            detail={"error": "yeelight_onboard_internal", "detail": result.error},
        )

    return app
