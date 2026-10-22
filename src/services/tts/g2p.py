# src/services/tts/g2p.py
"""Kokoro G2P via KPipeline(model=False): misaki phonemes, no audio (spec §4.4)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator

REPO_ID = "hexgrad/Kokoro-82M"
VOICE_RE = re.compile(r"^([ab])[fm]_[a-z0-9]+$")


class UnknownVoice(ValueError):
    """Voice is not available (only English a*/b* voices are supported)."""


@dataclass(frozen=True)
class G2PChunk:
    graphemes: str
    phonemes: str


def lang_code_for_voice(voice: str) -> str:
    match = VOICE_RE.match(voice)
    if not match:
        raise UnknownVoice(
            f"unsupported voice {voice!r}; expected English voices like af_heart"
        )
    return match.group(1)


class KokoroG2P:
    def __init__(
        self, lang_code: str = "a", *, pipeline_factory: Callable | None = None
    ):
        if lang_code not in ("a", "b"):
            raise ValueError(f"lang_code must be 'a' or 'b', got {lang_code!r}")
        if pipeline_factory is None:

            def pipeline_factory(code: str):
                from kokoro import KPipeline

                return KPipeline(lang_code=code, repo_id=REPO_ID, model=False)

        self.lang_code = lang_code
        self._pipeline = pipeline_factory(lang_code)

    def __call__(self, text: str) -> Iterator[G2PChunk]:
        for result in self._pipeline(text):
            if result.phonemes:
                yield G2PChunk(result.graphemes, result.phonemes)
