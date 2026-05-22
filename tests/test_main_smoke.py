"""Smoke test: app builds without hitting the LAN."""

from __future__ import annotations

from pathlib import Path

import pytest

from yeelight_core.config import Settings
from yeelight_core.main import build_app


def test_build_app_does_not_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YEELIGHT_STATE_DIR", str(tmp_path))
    app = build_app(Settings(_env_file=None, state_dir=tmp_path))  # type: ignore[call-arg]
    # The FastAPI app instance is returned; lifespan does not run here, so no
    # M-SEARCH sweep is triggered — the build must be instant and offline.
    assert app.title == "yeelight-core"
