"""Decode the existing domain wire views without rewriting arbitrary JSON keys."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from functools import lru_cache
from typing import get_args, get_origin, get_type_hints

from pydantic import TypeAdapter
from pydantic.alias_generators import to_camel


@lru_cache(maxsize=32)
def _adapter(model):
    return TypeAdapter(model)


@lru_cache(maxsize=32)
def _fields(model):
    hints = get_type_hints(model)
    return [
        (field.name, to_camel(field.name), hints[field.name]) for field in fields(model)
    ]


def _input(model, value):
    if value is None:
        return None
    if is_dataclass(model) and isinstance(value, dict):
        return {
            name: _input(hint, value[wire])
            for name, wire, hint in _fields(model)
            if wire in value
        }
    origin, args = get_origin(model), get_args(model)
    if origin in (list, tuple) and isinstance(value, (list, tuple)) and args:
        return [_input(args[0], item) for item in value]
    # Optional dataclasses need field aliases too. Dict/JSON payloads remain
    # untouched: keys such as tool arguments are application data, not fields.
    for candidate in args:
        if is_dataclass(candidate):
            return _input(candidate, value)
    return value


def from_view(model, value):
    return _adapter(model).validate_python(_input(model, value))
