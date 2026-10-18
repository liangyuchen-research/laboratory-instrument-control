"""FaaS style function registry.

Each function module(motor / valve / syringe pump / weight)Only do one thing:
use @function Register several of your own actions. they don’t know each other,
Don’t know either HTTP, serial port work - what you need is provided by Context injection.

Benefits:
  • Add a new device = Add a new one backend/functions/xxx.py,Other files are completely unchanged
  • GET /fn will automatically generate a list of ”what functions are there and what parameters are required”,
    The front end can verify the input according to the list,files will not be separated from the program code
  • Every function is pure (input) → (output),Good test
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class FunctionError(RuntimeError):
    """The parameters or preconditions are incorrect - will return HTTP 400,It’s not the server’s fault."""


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
                raise FunctionError(f"parameter {self.name} Must be a number")
            if self.type == "integer":
                if value != int(value):
                    raise FunctionError(f"parameter {self.name} Must be an integer")
                value = int(value)
            if self.minimum is not None and value < self.minimum:
                raise FunctionError(f"parameter {self.name} not less than {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise FunctionError(f"parameter {self.name} cannot be greater than {self.maximum}")
        elif self.type == "boolean":
            value = raw in (True, 1, "1", "true", "True", "on")
        else:
            value = raw
        if self.choices is not None and value not in self.choices:
            raise FunctionError(f"parameter {self.name} can only be {self.choices} one of them")
        return value


@dataclass
class Context:
    """is something injected into the function during execution."""
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
                raise FunctionError(f"Unrecognized parameter:{key}")
        kwargs: Dict[str, Any] = {}
        for param in self.params:
            if param.name in payload and payload[param.name] is not None:
                kwargs[param.name] = param.coerce(payload[param.name])
            elif param.default is not None:
                kwargs[param.name] = param.default
            elif param.required:
                raise FunctionError(f"Missing necessary parameters:{param.name}")
            else:
                kwargs[param.name] = None
        return self.handler(ctx, **kwargs)


_REGISTRY: Dict[str, Function] = {}


def function(name: str, summary: str, group: str,
             params: Optional[List[Param]] = None, danger: bool = False):
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise RuntimeError(f"Duplicate registration of function name End by itself in:{name}")
        _REGISTRY[name] = Function(name=name, handler=fn, summary=summary,
                                   group=group, params=params or [], danger=danger)
        return fn
    return decorator


def get(name: str) -> Function:
    if name not in _REGISTRY:
        raise FunctionError(f"does not have this function:{name}")
    return _REGISTRY[name]


def manifest() -> List[Dict[str, Any]]:
    return [f.manifest() for f in sorted(_REGISTRY.values(), key=lambda f: f.name)]


def clear() -> None:          # for testing
    _REGISTRY.clear()
