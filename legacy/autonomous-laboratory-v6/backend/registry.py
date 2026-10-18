"""Registry of device operations and their parameter schemas.

Device modules register operations with @function. A Context supplies their
configuration and transport dependencies, keeping HTTP handling separate from
device control. The /fn manifest exposes the same schemas used for validation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class FunctionError(RuntimeError):
    """Invalid input or unmet precondition, reported as HTTP 400."""


@dataclass
class Param:
    name: str
    type: str = "number"                 # number | integer | string | boolean | enum
    required: bool = True
    default: Any = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Optional[List[Any]] = None
    label: str = ""
    unit: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = {"name": self.name, "type": self.type, "required": self.required,
               "label": self.label or self.name, "unit": self.unit}
        for key in ("default", "minimum", "maximum", "choices"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    def coerce(self, raw: Any) -> Any:
        if self.type in ("number", "integer"):
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise FunctionError(f"Parameter {self.name} must be numeric")
            if not math.isfinite(value):
                raise FunctionError(f"Parameter {self.name} must be finite")
            if self.type == "integer":
                if value != int(value):
                    raise FunctionError(f"Parameter {self.name} must be an integer")
                value = int(value)
            if self.minimum is not None and value < self.minimum:
                raise FunctionError(f"Parameter {self.name} must be at least {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise FunctionError(f"Parameter {self.name} must not exceed {self.maximum}")
        elif self.type == "boolean":
            value = raw in (True, 1, "1", "true", "True", "on")
        else:
            value = raw
        if self.choices is not None and value not in self.choices:
            raise FunctionError(f"Parameter {self.name} must be one of {self.choices}")
        return value


@dataclass
class Context:
    """Dependencies supplied to an operation at invocation time."""
    link: Any
    config: Dict[str, Any]


@dataclass
class Function:
    name: str
    handler: Callable[..., Any]
    summary: str
    group: str
    params: List[Param] = field(default_factory=list)
    danger: bool = False

    def manifest(self) -> Dict[str, Any]:
        return {"name": self.name, "summary": self.summary, "group": self.group,
                "danger": self.danger, "params": [p.to_dict() for p in self.params]}

    def invoke(self, ctx: Context, payload: Dict[str, Any]) -> Any:
        payload = payload or {}
        known = {p.name for p in self.params}
        for key in payload:
            if key not in known:
                raise FunctionError(f"Unknown parameter: {key}")
        kwargs: Dict[str, Any] = {}
        for param in self.params:
            if param.name in payload and payload[param.name] is not None:
                kwargs[param.name] = param.coerce(payload[param.name])
            elif param.default is not None:
                kwargs[param.name] = param.default
            elif param.required:
                raise FunctionError(f"Missing required parameter: {param.name}")
            else:
                kwargs[param.name] = None
        return self.handler(ctx, **kwargs)


_REGISTRY: Dict[str, Function] = {}


def function(name: str, summary: str, group: str,
             params: Optional[List[Param]] = None, danger: bool = False):
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise RuntimeError(f"Operation is already registered: {name}")
        _REGISTRY[name] = Function(name=name, handler=fn, summary=summary,
                                   group=group, params=params or [], danger=danger)
        return fn
    return decorator


def get(name: str) -> Function:
    if name not in _REGISTRY:
        raise FunctionError(f"Unknown operation: {name}")
    return _REGISTRY[name]


def manifest() -> List[Dict[str, Any]]:
    return [f.manifest() for f in sorted(_REGISTRY.values(), key=lambda f: f.name)]


def clear() -> None:          # Used by tests.
    _REGISTRY.clear()
