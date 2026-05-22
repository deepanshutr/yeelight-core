"""Async wrapper over the synchronous `yeelight` PyPI library (TCP 55443).

The `yeelight` library is blocking; every call is dispatched to a worker
thread via `asyncio.to_thread` so the event loop stays free. Each call
constructs a fresh `Bulb` (cheap; the lib opens the socket per command),
keeping the driver stateless and concurrency-safe — the same design as
wiz-core's BulbClient.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from yeelight import Bulb, BulbException

log = logging.getLogger(__name__)

DEFAULT_PORT = 55443

# Daemon-facing clamps (spec §4.2). Yeelight's native colour-temp band is
# 1700-6500K; the shared HTTP contract is 2200-6500K, so the 2200 floor is
# the binding constraint. Brightness floor 10 maps onto Yeelight's 1-100.
KELVIN_MIN = 2200
KELVIN_MAX = 6500
BRIGHTNESS_MIN = 1
BRIGHTNESS_MAX = 100


class YeelightError(RuntimeError):
    """Raised when a Yeelight call fails (timeout, socket, or protocol error)."""


class YeelightLanDisabledError(YeelightError):
    """Raised when TCP 55443 is refused — LAN Control is off in the Mi Home app."""


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _is_connection_refused(exc: BaseException) -> bool:
    """True if `exc` (or its cause/context chain) is a refused TCP connection."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ConnectionRefusedError):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _lan_disabled(ip: str) -> YeelightLanDisabledError:
    return YeelightLanDisabledError(
        f"yeelight LAN control disabled for {ip}; "
        f"enable it in the Mi Home app per bulb "
        f"(Device -> Settings -> LAN Control)"
    )


class YeelightDriver:
    """Stateless Yeelight client. Safe for concurrent use."""

    def _bulb(self, ip: str, port: int) -> Bulb:
        # auto_on=False: never implicitly power a bulb on just to set a prop.
        return Bulb(ip, port=port, auto_on=False)

    async def _run(self, ip: str, port: int, fn_name: str, *args: Any) -> Any:
        """Dispatch one blocking library call to a thread, mapping errors."""

        def call() -> Any:
            bulb = self._bulb(ip, port)
            method = getattr(bulb, fn_name)
            return method(*args)

        try:
            return await asyncio.to_thread(call)
        except BulbException as exc:
            if _is_connection_refused(exc):
                raise _lan_disabled(ip) from exc
            raise YeelightError(f"{fn_name} to {ip}:{port}: {exc}") from exc
        except OSError as exc:
            if _is_connection_refused(exc):
                raise _lan_disabled(ip) from exc
            raise YeelightError(f"{fn_name} to {ip}:{port}: {exc!r}") from exc

    async def get_state(self, ip: str, port: int = DEFAULT_PORT) -> dict[str, Any]:
        props = await self._run(ip, port, "get_properties")
        return dict(props or {})

    async def set_power(self, ip: str, port: int, *, on: bool) -> dict[str, Any]:
        await self._run(ip, port, "turn_on" if on else "turn_off")
        return {"success": True}

    async def set_brightness(
        self, ip: str, port: int, level: int
    ) -> dict[str, Any]:
        await self._run(
            ip, port, "set_brightness", _clamp(level, BRIGHTNESS_MIN, BRIGHTNESS_MAX)
        )
        return {"success": True}

    async def set_temp(self, ip: str, port: int, kelvin: int) -> dict[str, Any]:
        await self._run(
            ip, port, "set_color_temp", _clamp(kelvin, KELVIN_MIN, KELVIN_MAX)
        )
        return {"success": True}

    async def set_color(
        self, ip: str, port: int, r: int, g: int, b: int
    ) -> dict[str, Any]:
        await self._run(ip, port, "set_rgb", r, g, b)
        return {"success": True}

    async def set_name(self, ip: str, port: int, name: str) -> dict[str, Any]:
        await self._run(ip, port, "set_name", name)
        return {"success": True}
