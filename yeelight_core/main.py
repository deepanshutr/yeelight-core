"""FastAPI app entrypoint with lifespan-managed discovery loops."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from .api import create_app
from .config import Settings
from .config import load as load_settings
from .discover import discover as discover_lan
from .driver import YeelightDriver, YeelightError
from .onboard import OnboardDeps
from .registry import Registry

log = logging.getLogger(__name__)


async def _run_nmcli(*args: str) -> tuple[int, str]:
    """Run `nmcli <args>` and return (returncode, combined stdout+stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "nmcli", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


async def _http_post(url: str, json_body: dict[str, Any]) -> tuple[int, str]:
    """POST `json_body` to `url`; return (status_code, body_text).

    Short timeout: a setup-mode bulb is one hop away on its own AP.
    """
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.post(url, json=json_body)
        return resp.status_code, resp.text


def build_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    registry = Registry(cfg.state_path)
    driver = YeelightDriver()

    async def run_discovery() -> int:
        before = len(registry.all())
        bulbs = await discover_lan(
            multicast_addr=cfg.multicast_addr,
            multicast_port=cfg.multicast_port,
            timeout_s=cfg.discover_timeout_s,
        )
        for b in bulbs:
            entry = registry.upsert_discovered(b)
            # Fold M-SEARCH-reported model/fw straight into the registry.
            registry.enrich(entry.mac, b)
        registry.flush()
        return len(registry.all()) - before

    async def discover_dicts() -> list[dict[str, Any]]:
        """Bare discovery (list of dicts) for the onboarding poll loop."""
        return await discover_lan(
            multicast_addr=cfg.multicast_addr,
            multicast_port=cfg.multicast_port,
            timeout_s=cfg.discover_timeout_s,
        )

    async def refresh_loop() -> None:
        while True:
            await asyncio.sleep(cfg.refresh_interval_s)
            for b in registry.all():
                try:
                    await driver.get_state(b.last_ip, b.port)
                    registry.upsert_discovered(
                        {"mac": b.mac, "ip": b.last_ip, "port": b.port, "rssi": None}
                    )
                except YeelightError:
                    log.debug("refresh %s missed", b.mac)
            registry.flush()

    async def rediscover_loop() -> None:
        while True:
            await asyncio.sleep(cfg.discover_interval_s)
            try:
                await run_discovery()
            except Exception as e:
                log.warning("background rediscover failed: %r", e)

    async def _boot_discover() -> None:
        try:
            await run_discovery()
        except Exception as e:
            log.warning("boot discovery failed: %r", e)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Fire boot discovery in the background so uvicorn binds the port
        # immediately; /bulbs returns [] until the M-SEARCH sweep populates
        # the registry. /health stays responsive throughout. Awaiting this
        # before `yield` would block the port bind (see wiz-bg-boot-discover).
        t_boot = asyncio.create_task(_boot_discover())
        t1 = asyncio.create_task(refresh_loop())
        t2 = asyncio.create_task(rediscover_loop())
        try:
            yield
        finally:
            for t in (t_boot, t1, t2):
                t.cancel()
            await asyncio.gather(t_boot, t1, t2, return_exceptions=True)

    onboard_deps = OnboardDeps(
        nmcli=_run_nmcli,
        http_post=_http_post,
        discover=discover_dicts,
        sleep=asyncio.sleep,
    )
    app = create_app(
        registry=registry,
        driver=driver,
        run_discovery=run_discovery,
        onboard_deps=onboard_deps,
        all_concurrency=cfg.all_concurrency,
    )
    app.router.lifespan_context = lifespan
    return app


app = build_app()
