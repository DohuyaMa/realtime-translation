"""Static English voice catalogue of Kokoro-82M v1.0 (a* = en-US, b* = en-GB)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

KOKORO_VOICES = (
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_heart",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "am_puck",
    "am_santa",
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
)

LANGUAGE_BY_PREFIX = {"a": "en-us", "b": "en-gb"}
GENDER_BY_LETTER = {"f": "female", "m": "male"}


@dataclass(frozen=True)
class Voice:
    id: str
    language: str
    gender: str


def catalog() -> List[Voice]:
    return [
        Voice(v, LANGUAGE_BY_PREFIX[v[0]], GENDER_BY_LETTER[v[1]])
        for v in KOKORO_VOICES
    ]
