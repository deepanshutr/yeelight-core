"""Yeelight named scene catalog + resolver."""

from __future__ import annotations

import pytest

from yeelight_core.scenes import SCENES, SceneAction, resolve_scene


def test_catalog_is_non_empty_and_named() -> None:
    assert len(SCENES) >= 6
    assert "movie" in SCENES
    assert "night" in SCENES
    assert all(isinstance(v, SceneAction) for v in SCENES.values())


def test_resolve_by_name_case_insensitive() -> None:
    a = resolve_scene("movie")
    assert a == SCENES["movie"]
    assert resolve_scene("MOVIE") == SCENES["movie"]
    assert resolve_scene("  Movie ") == SCENES["movie"]


def test_resolve_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown scene"):
        resolve_scene("nope")


def test_scene_action_kinds_are_valid() -> None:
    for action in SCENES.values():
        assert action.kind in {"rgb", "temp", "brightness"}


def test_movie_is_a_warm_dim_temp_scene() -> None:
    a = SCENES["movie"]
    assert a.kind == "temp"
    assert a.kelvin is not None and 2200 <= a.kelvin <= 3200
    assert a.brightness is not None and a.brightness <= 40
