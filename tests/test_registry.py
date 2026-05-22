"""Bulb registry: target resolution + state.json round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from yeelight_core.registry import Registry


@pytest.fixture()
def two_bulb_registry(tmp_path: Path) -> Registry:
    reg = Registry(tmp_path / "state.json")
    reg.upsert_discovered({
        "mac": "aaaaaaaaaaaa", "ip": "192.168.1.10", "port": 55443, "rssi": -50,
    })
    reg.upsert_discovered({
        "mac": "bbbbbbbbbbbb", "ip": "192.168.1.11", "port": 55443, "rssi": -60,
    })
    return reg


def test_resolve_by_mac(two_bulb_registry: Registry) -> None:
    b = two_bulb_registry.resolve("aaaaaaaaaaaa")
    assert b is not None and b.mac == "aaaaaaaaaaaa"
    b = two_bulb_registry.resolve("AA:AA:AA:AA:AA:AA")
    assert b is not None and b.mac == "aaaaaaaaaaaa"


def test_resolve_by_ip(two_bulb_registry: Registry) -> None:
    b = two_bulb_registry.resolve("192.168.1.10")
    assert b is not None and b.mac == "aaaaaaaaaaaa"


def test_resolve_by_name_case_insensitive(two_bulb_registry: Registry) -> None:
    two_bulb_registry.rename("aaaaaaaaaaaa", "Bedroom")
    b = two_bulb_registry.resolve("bedroom")
    assert b is not None and b.mac == "aaaaaaaaaaaa"


def test_resolve_default_picks_earliest_discovered(two_bulb_registry: Registry) -> None:
    b = two_bulb_registry.default()
    assert b is not None and b.mac == "aaaaaaaaaaaa"


def test_resolve_missing_returns_none(two_bulb_registry: Registry) -> None:
    assert two_bulb_registry.resolve("zz") is None
    assert two_bulb_registry.resolve("192.168.1.99") is None
    assert two_bulb_registry.resolve("nope") is None


def test_persistence_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    reg = Registry(p)
    reg.upsert_discovered({
        "mac": "abcdef012345", "ip": "10.0.0.5", "port": 55443, "rssi": -42,
    })
    reg.flush()

    reg2 = Registry(p)
    b = reg2.resolve("abcdef012345")
    assert b is not None
    assert b.last_ip == "10.0.0.5"
    assert b.port == 55443
    assert b.last_rssi == -42
    assert (p.stat().st_mode & 0o777) == 0o600


def test_friendly_name_auto_assigned(two_bulb_registry: Registry) -> None:
    names = sorted(b.name for b in two_bulb_registry.all())
    assert names == ["bulb-1", "bulb-2"]


def test_rename_updates_resolution(two_bulb_registry: Registry) -> None:
    two_bulb_registry.rename("aaaaaaaaaaaa", "Bedroom")
    b = two_bulb_registry.resolve("Bedroom")
    assert b is not None and b.name == "Bedroom"


def test_all_returns_every_bulb(two_bulb_registry: Registry) -> None:
    assert len(two_bulb_registry.all()) == 2


def test_empty_registry_default_is_none(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "state.json")
    assert reg.default() is None


def test_resolve_underscore_default_sentinel(two_bulb_registry: Registry) -> None:
    expected = two_bulb_registry.default()
    assert expected is not None
    assert two_bulb_registry.resolve("_default") == expected
    assert two_bulb_registry.resolve("") == expected
    assert two_bulb_registry.resolve(None) == expected


def test_enrich_folds_model_and_fw(two_bulb_registry: Registry) -> None:
    two_bulb_registry.enrich(
        "aaaaaaaaaaaa",
        {"model": "color3", "fw_ver": "53", "cct_range": [1700, 6500]},
    )
    b = two_bulb_registry.resolve("aaaaaaaaaaaa")
    assert b is not None
    assert b.module == "color3"
    assert b.fw_version == "53"
    assert b.cct_range == (1700, 6500)
