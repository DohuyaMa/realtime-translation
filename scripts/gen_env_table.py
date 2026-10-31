"""Render the rt-asr / rt-vosk / rt-tts env tables for docs/USAGE.md.

Usage: python -m scripts.gen_env_table
"""

from __future__ import annotations

from typing import Any

from src.services.asr.settings import AsrSettings
from src.services.common.settings import render_env_value
from src.services.tts.settings import TtsSettings
from src.services.vosk.settings import VoskSettings

SERVICES = (
    ("rt-asr", AsrSettings),
    ("rt-vosk", VoskSettings),
    ("rt-tts", TtsSettings),
)


def _defaults(cls: type) -> dict[str, Any]:
    """Field defaults: parse an empty environment (CPU)."""
    settings = cls.from_env({})  # type: ignore[attr-defined]
    keys = cls.env_keys()  # type: ignore[attr-defined]
    return {f: getattr(settings, f) for f in keys}


def _klass(cls: Any, field: str) -> str:
    if field in cls.RELOAD_FIELDS:
        return "RELOAD"
    if field in cls.TUNABLE_FIELDS:
        return "TUNABLE"
    return "BOOTSTRAP"


def render_table(cls: Any) -> str:
    defaults = _defaults(cls)
    rows = ["| Ключ | Default | Клас |", "|---|---|---|"]
    for field, key in sorted(cls.env_keys().items(), key=lambda kv: kv[1]):
        value = render_env_value(defaults[field]) or "(не задано)"
        rows.append(f"| `{key}` | `{value}` | {_klass(cls, field)} |")
    return "\n".join(rows)


def render_all() -> str:
    parts = []
    for name, cls in SERVICES:
        parts.append(f"**{name}**\n\n{render_table(cls)}")
    return "\n\n".join(parts) + "\n"


if __name__ == "__main__":
    print(render_all(), end="")
