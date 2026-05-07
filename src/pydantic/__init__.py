"""Minimal local shim for pydantic interfaces used in this scaffold.

This is only intended for offline test environments.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field
from typing import Any


class _FieldInfo:
    def __init__(self, default: Any = MISSING, default_factory: Any = MISSING):
        self.default = default
        self.default_factory = default_factory


def Field(*, default: Any = MISSING, default_factory: Any = MISSING) -> Any:
    return _FieldInfo(default=default, default_factory=default_factory)


class _BaseModelMeta(type):
    def __new__(mcls, name: str, bases: tuple[type, ...], namespace: dict[str, Any]):
        annotations = namespace.get("__annotations__", {})
        for key in annotations:
            value = namespace.get(key, MISSING)
            if isinstance(value, _FieldInfo):
                if value.default_factory is not MISSING:
                    namespace[key] = field(default_factory=value.default_factory)
                elif value.default is not MISSING:
                    namespace[key] = value.default
                else:
                    namespace[key] = field()
        cls = super().__new__(mcls, name, bases, namespace)
        return dataclass(cls)


class BaseModel(metaclass=_BaseModelMeta):
    pass
