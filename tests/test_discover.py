"""Discovery: M-SEARCH response parsing + collect loop."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from yeelight_core.discover import discover, parse_msearch_response

_SAMPLE = (
    "HTTP/1.1 200 OK\r\n"
    "Cache-Control: max-age=3600\r\n"
    "Location: yeelight://192.168.1.20:55443\r\n"
    "Server: POSIX UPnP/1.0 YGLC/1\r\n"
    "id: 0x0000000007e2c5b1\r\n"
    "model: color3\r\n"
    "fw_ver: 53\r\n"
    "support: get_prop set_default set_power toggle set_bright set_rgb set_ct_abx\r\n"
    "power: on\r\n"
    "bright: 100\r\n"
    "rgb: 16711680\r\n"
)


def test_parse_msearch_extracts_id_ip_port_model() -> None:
    parsed = parse_msearch_response(_SAMPLE)
    assert parsed["mac"] == "0000000007e2c5b1"
    assert parsed["ip"] == "192.168.1.20"
    assert parsed["port"] == 55443
    assert parsed["model"] == "color3"
    assert parsed["fw_ver"] == "53"


def test_parse_msearch_missing_id_raises() -> None:
    bad = "HTTP/1.1 200 OK\r\nLocation: yeelight://192.168.1.20:55443\r\n"
    with pytest.raises(ValueError, match="missing id"):
        parse_msearch_response(bad)


def test_parse_msearch_missing_location_raises() -> None:
    bad = "HTTP/1.1 200 OK\r\nid: 0x01\r\nmodel: color3\r\n"
    with pytest.raises(ValueError, match="missing Location"):
        parse_msearch_response(bad)


async def test_discover_collects_and_dedups_by_mac() -> None:
    # Two raw responses; the same bulb answers twice (multicast echoes).
    raws = [_SAMPLE, _SAMPLE]

    async def fake_collect(addr: str, port: int, timeout_s: float) -> list[str]:
        assert addr == "239.255.255.250"
        assert port == 1982
        return raws

    with patch(
        "yeelight_core.discover._msearch_collect", AsyncMock(side_effect=fake_collect)
    ):
        bulbs = await discover(
            multicast_addr="239.255.255.250",
            multicast_port=1982,
            timeout_s=0.0,
        )

    assert len(bulbs) == 1
    assert bulbs[0]["mac"] == "0000000007e2c5b1"
    assert bulbs[0]["ip"] == "192.168.1.20"
    assert bulbs[0]["port"] == 55443
