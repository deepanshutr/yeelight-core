"""Yeelight driver: wraps the yeelight lib, mocked at the Bulb boundary."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from yeelight_core.driver import (
    YeelightDriver,
    YeelightError,
    YeelightLanDisabledError,
)


def _fake_bulb() -> MagicMock:
    b = MagicMock()
    b.get_properties.return_value = {
        "power": "on", "bright": "100", "ct": "2700",
        "rgb": "16711680", "name": "bulb-1",
    }
    return b


async def test_get_state_returns_properties() -> None:
    fake = _fake_bulb()
    with patch("yeelight_core.driver.Bulb", return_value=fake) as ctor:
        d = YeelightDriver()
        out = await d.get_state("192.168.1.10", 55443)
    ctor.assert_called_once_with("192.168.1.10", port=55443, auto_on=False)
    assert out["power"] == "on"
    assert out["ct"] == "2700"


async def test_set_power_on_calls_turn_on() -> None:
    fake = _fake_bulb()
    with patch("yeelight_core.driver.Bulb", return_value=fake):
        d = YeelightDriver()
        await d.set_power("192.168.1.10", 55443, on=True)
    fake.turn_on.assert_called_once()


async def test_set_power_off_calls_turn_off() -> None:
    fake = _fake_bulb()
    with patch("yeelight_core.driver.Bulb", return_value=fake):
        d = YeelightDriver()
        await d.set_power("192.168.1.10", 55443, on=False)
    fake.turn_off.assert_called_once()


async def test_set_color_brightness_temp_map_to_lib_calls() -> None:
    fake = _fake_bulb()
    with patch("yeelight_core.driver.Bulb", return_value=fake):
        d = YeelightDriver()
        await d.set_color("192.168.1.10", 55443, 255, 0, 100)
        await d.set_brightness("192.168.1.10", 55443, 50)
        # Our 4000K request is inside Yeelight's 1700-6500 band -> passes through.
        await d.set_temp("192.168.1.10", 55443, 4000)
    fake.set_rgb.assert_called_once_with(255, 0, 100)
    fake.set_brightness.assert_called_once_with(50)
    fake.set_color_temp.assert_called_once_with(4000)


async def test_bulb_exception_becomes_yeelight_error() -> None:
    import yeelight

    fake = MagicMock()
    fake.turn_on.side_effect = yeelight.BulbException("boom")
    with patch("yeelight_core.driver.Bulb", return_value=fake):
        d = YeelightDriver()
        with pytest.raises(YeelightError, match="boom"):
            await d.set_power("192.168.1.10", 55443, on=True)


async def test_connection_refused_becomes_lan_disabled_error() -> None:
    import yeelight

    fake = MagicMock()
    # yeelight raises BulbException; the socket error is in the cause chain.
    exc = yeelight.BulbException("A socket error occurred sending the command.")
    exc.__cause__ = ConnectionRefusedError(111, "refused")
    fake.get_properties.side_effect = exc
    with patch("yeelight_core.driver.Bulb", return_value=fake):
        d = YeelightDriver()
        with pytest.raises(YeelightLanDisabledError) as ei:
            await d.get_state("192.168.1.10", 55443)
        assert "LAN control disabled" in str(ei.value)
        assert "Mi Home" in str(ei.value)
