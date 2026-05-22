"""Settings parse env-prefixed vars with sane defaults."""

from __future__ import annotations

import pytest

from yeelight_core.config import Settings


def test_defaults_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("YEELIGHT_"):
            monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.bind == "127.0.0.1:8767"
    assert s.multicast_addr == "239.255.255.250"
    assert s.multicast_port == 1982
    assert s.discover_timeout_s == 2
    assert s.refresh_interval_s == 60
    assert s.discover_interval_s == 600
    assert s.all_concurrency == 16
    assert s.log_level == "INFO"
    assert str(s.state_dir).endswith("yeelight")


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YEELIGHT_BIND", "127.0.0.1:9100")
    monkeypatch.setenv("YEELIGHT_REFRESH_INTERVAL_S", "30")
    monkeypatch.setenv("YEELIGHT_ALL_CONCURRENCY", "4")
    s = Settings(_env_file=None)
    assert s.bind == "127.0.0.1:9100"
    assert s.refresh_interval_s == 30
    assert s.all_concurrency == 4
