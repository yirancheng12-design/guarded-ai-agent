"""Explicit coordinate spaces. UIA physical rectangles are never DPI-scaled."""
from dataclasses import dataclass
import math
from .model import Rect


def normalized_point(rect: Rect, u: float, v: float):
    if any(not math.isfinite(x) or not 0 <= x <= 1 for x in (u, v)):
        raise ValueError("Point is outside normalized content")
    return rect.left + round(u * (rect.width - 1)), rect.top + round(v * (rect.height - 1))


@dataclass(frozen=True)
class ImageMapping:
    physical_roi: Rect
    model_content: Rect  # Non-padding pixels in the image shown to the model.
    observation_id: str

    def to_screen(self, x: float, y: float, current_observation_id: str):
        if current_observation_id != self.observation_id:
            raise ValueError("Stale screenshot mapping; recapture before acting")
        c = self.model_content
        if not (c.left <= x <= c.left + c.width - 1 and c.top <= y <= c.top + c.height - 1):
            raise ValueError("Point falls in padding or outside the image")
        if c.width < 2 or c.height < 2:
            raise ValueError("Insufficient image resolution for coordinate mapping")
        return normalized_point(self.physical_roi, (x - c.left) / (c.width - 1), (y - c.top) / (c.height - 1))


def sendinput_absolute(x: int, y: int, desktop: Rect):
    if desktop.width < 2 or desktop.height < 2:
        raise ValueError("Invalid virtual desktop")
    if not (desktop.left <= x < desktop.left + desktop.width and desktop.top <= y < desktop.top + desktop.height):
        raise ValueError("Input coordinate outside virtual desktop")
    return round((x - desktop.left) * 65535 / (desktop.width - 1)), round((y - desktop.top) * 65535 / (desktop.height - 1))
