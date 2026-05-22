"""Pytest fixtures shared across the test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test config dir, isolated from the host's real ~/.config."""
    state = tmp_path / "yeelight"
    state.mkdir()
    monkeypatch.setenv("YEELIGHT_STATE_DIR", str(state))
    return state
