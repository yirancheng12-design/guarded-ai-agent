from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import math


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Rectangle must have positive dimensions")

    @property
    def bbox(self):
        return self.left, self.top, self.left + self.width, self.top + self.height


@dataclass(frozen=True)
class WindowIdentity:
    hwnd: int
    pid: int
    created: int  # Windows process creation FILETIME; guards PID/HWND reuse.


@dataclass(frozen=True)
class Selector:
    automation_id: str | None = None
    name: str | None = None
    control_type: int | None = None

    def __post_init__(self):
        if all(x is None for x in (self.automation_id, self.name, self.control_type)):
            raise ValueError("A selector must constrain at least one property")


@dataclass(frozen=True)
class VisualSpec:
    template: str
    roi: Rect | None = None  # Otherwise use the RESULT element's rectangle.
    pixel_tolerance: int = 24
    max_changed_ratio: float = 0.01
    masks: tuple[Rect, ...] = ()  # Relative to ROI, not desktop coordinates.

    def __post_init__(self):
        if not 0 <= self.pixel_tolerance <= 255:
            raise ValueError("Invalid pixel tolerance")
        if not 0 <= self.max_changed_ratio < 1:
            raise ValueError("Invalid template difference threshold")


@dataclass(frozen=True)
class Condition:
    selector: Selector
    property: str
    expected: Any
    mode: str = "uia"  # uia, visual, both, uia_or_visual
    visual: VisualSpec | None = None
    failure_values: tuple[Any, ...] = ()  # Explicit terminal UIA states only.

    def __post_init__(self):
        if self.property not in {"value", "text", "name", "toggle", "exists"}:
            raise ValueError("Unsupported postcondition property")
        if self.mode not in {"uia", "visual", "both", "uia_or_visual"}:
            raise ValueError("Unknown evidence mode")
        if self.mode != "uia" and self.visual is None:
            raise ValueError("Visual verification requires an explicit expected template")
        if self.property == "exists" and type(self.expected) is not bool:
            raise ValueError("exists expects a boolean")
        if self.property == "toggle" and (type(self.expected) is not int or self.expected not in (0, 1, 2)):
            raise ValueError("toggle expects 0 (Off), 1 (On), or 2 (Indeterminate)")
        if self.expected in self.failure_values:
            raise ValueError("Success and terminal failure states must be disjoint")


@dataclass(frozen=True)
class Action:
    operation_id: str
    window: WindowIdentity
    target: Selector
    kind: str
    condition: Condition
    intent: str = "ensure"  # command requires a new false -> true result.
    text: str = ""
    point: tuple[float, float] = (0.5, 0.5)  # Relative to target's current rectangle.
    allow_owned_result: bool = False

    def __post_init__(self):
        if not self.operation_id.strip():
            raise ValueError("operation_id must be stable and nonempty")
        if self.kind not in {"click", "type_text", "invoke", "toggle"}:
            raise ValueError("Unsupported action kind")
        if self.intent not in {"ensure", "command"}:
            raise ValueError("Unsupported intent")
        if len(self.point) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.point):
            raise ValueError("Point must be normalized to the target rectangle")
        if self.kind == "type_text" and not self.text:
            raise ValueError("type_text requires literal text")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        d = dict(data)
        d["window"] = WindowIdentity(**d["window"])
        d["target"] = Selector(**d["target"])
        c = dict(d["condition"])
        c["selector"] = Selector(**c["selector"])
        c["failure_values"] = tuple(c.get("failure_values", ()))
        if c.get("visual"):
            v = dict(c["visual"])
            if v.get("roi"):
                v["roi"] = Rect(**v["roi"])
            v["masks"] = tuple(Rect(**r) for r in v.get("masks", []))
            c["visual"] = VisualSpec(**v)
        d["condition"] = Condition(**c)
        if "point" in d:
            d["point"] = tuple(d["point"])
        return cls(**d)


@dataclass(frozen=True)
class Policy:
    consecutive: int = 3
    interval: float = 0.2
    observation_timeout: float = 10.0
    call_timeout: float = 5.0

    def __post_init__(self):
        if type(self.consecutive) is not int or self.consecutive < 1:
            raise ValueError("consecutive must be a positive integer")
        if any(not math.isfinite(v) or v <= 0 for v in (self.interval, self.observation_timeout, self.call_timeout)):
            raise ValueError("Timeouts and intervals must be finite and positive")
        if (self.consecutive - 1) * self.interval >= self.observation_timeout:
            raise ValueError("Observation budget cannot accommodate the required samples")


@dataclass(frozen=True)
class Evidence:
    status: str  # valid, unsupported, error
    value: Any = None
    detail: str = ""


@dataclass(frozen=True)
class Observation:
    sequence: int
    started: float
    finished: float
    uia: Evidence
    visual: Evidence = field(default_factory=lambda: Evidence("unsupported"))
    region_hash: str | None = None
    changed_ratio: float | None = None
    image_path: str | None = None


@dataclass(frozen=True)
class Context:
    ok: bool
    token: tuple = ()
    reason: str = ""


@dataclass(frozen=True)
class Receipt:
    status: str  # not_sent, sent, uncertain: dispatch facts, never validation facts.
    detail: str = ""
    accepted: int | None = None
    requested: int | None = None


@dataclass(frozen=True)
class Result:
    operation_id: str
    execution_status: str
    outcome: str
    reason: str
    samples: int = 0
    needs_review: bool = False

    def to_dict(self):
        return asdict(self)


class BackendTimeout(TimeoutError):
    pass


class ContextLost(RuntimeError):
    pass
