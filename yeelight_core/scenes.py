"""Yeelight named scene catalog.

Yeelight bulbs have no fixed numeric scene table (unlike WiZ's 32). We
expose a small curated set of named scenes; each resolves to a concrete
driver action (set an RGB colour, a colour temperature, or a brightness),
so the route layer never imports the yeelight library directly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SceneAction:
    """A scene resolved to one concrete bulb call.

    `kind` selects which driver method runs:
      - "rgb"        -> set_color(r, g, b) then optionally set_brightness
      - "temp"       -> set_temp(kelvin) then optionally set_brightness
      - "brightness" -> set_brightness(brightness) only
    """

    kind: str
    r: int | None = None
    g: int | None = None
    b: int | None = None
    kelvin: int | None = None
    brightness: int | None = None


# Curated native Yeelight scenes. Brightness is 1-100; kelvin within the
# daemon's clamped 2200-6500 band (see driver._clamp).
SCENES: dict[str, SceneAction] = {
    "movie": SceneAction(kind="temp", kelvin=2700, brightness=30),
    "night": SceneAction(kind="temp", kelvin=2200, brightness=10),
    "tv": SceneAction(kind="rgb", r=30, g=60, b=120, brightness=40),
    "reading": SceneAction(kind="temp", kelvin=4500, brightness=100),
    "relax": SceneAction(kind="rgb", r=255, g=120, b=40, brightness=50),
    "daylight": SceneAction(kind="temp", kelvin=6500, brightness=100),
    "sunset": SceneAction(kind="rgb", r=255, g=80, b=20, brightness=60),
    "focus": SceneAction(kind="temp", kelvin=5000, brightness=100),
}


def resolve_scene(scene: str | int) -> SceneAction:
    """Resolve a scene by its canonical name (case-insensitive)."""
    s = str(scene).strip().lower()
    if s in SCENES:
        return SCENES[s]
    raise ValueError(f"unknown scene: {scene!r}")
