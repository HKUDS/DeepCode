"""Shared transcript-detail modes for interactive and headless CLI surfaces."""

from __future__ import annotations

from enum import StrEnum


class TranscriptMode(StrEnum):
    NORMAL = "normal"
    VERBOSE = "verbose"
    SUMMARY = "summary"

    def next(self) -> "TranscriptMode":
        modes = tuple(type(self))
        return modes[(modes.index(self) + 1) % len(modes)]

    @classmethod
    def parse(cls, value: str) -> "TranscriptMode":
        clean = value.strip().lower()
        try:
            return cls(clean)
        except ValueError as exc:
            allowed = ", ".join(mode.value for mode in cls)
            raise ValueError(f"transcript mode must be one of: {allowed}") from exc


__all__ = ["TranscriptMode"]
