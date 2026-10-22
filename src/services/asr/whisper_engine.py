# src/services/asr/whisper_engine.py
"""faster-whisper engine with spec §4.3 decoding and VAD profiles."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence

import numpy as np

from ..common.device import (
    backend_device,
    default_compute_type,
    require_gpu,
    resolve_ct2_compute_type,
)

SAMPLE_RATE = 16000
FINAL_TEMPERATURES = (0.0, 0.2, 0.4)


@dataclass(frozen=True)
class VadProfile:
    threshold: float
    neg_threshold: float | None
    min_speech_duration_ms: int
    min_silence_duration_ms: int
    speech_pad_ms: int
    max_speech_duration_s: float

    def as_dict(self) -> dict:
        data = asdict(self)
        if data["neg_threshold"] is None:
            del data["neg_threshold"]
        return data


# Streaming endpointing: min_silence_duration_ms is the finalization delay.
STREAMING_VAD = VadProfile(0.5, 0.35, 250, 400, 200, 12.0)
# File transcription (vad_filter=True).
FILE_VAD = VadProfile(0.5, None, 0, 500, 300, 30.0)


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    segments: list[Segment]
    duration: float


def to_float32_mono(audio) -> np.ndarray:
    arr = np.asarray(audio)
    if arr.ndim != 1:
        raise ValueError("audio must be mono 1-D PCM at 16 kHz")
    if arr.dtype == np.int16:
        return arr.astype(np.float32) / 32768.0
    return arr.astype(np.float32, copy=False)


class WhisperEngine:
    def __init__(
        self,
        model_dir: Path,
        device: str = "cpu",
        compute_type: str = "int8",
        threads: int = 4,
        *,
        model_factory: Callable | None = None,
    ):
        if model_factory is None:
            from faster_whisper import WhisperModel

            model_factory = WhisperModel
        self.device = device
        self.compute_type = compute_type
        self._model = model_factory(
            str(model_dir),
            device=backend_device(device),
            compute_type=compute_type,
            cpu_threads=threads,
            num_workers=1,
        )

    @classmethod
    def from_settings(
        cls, model_dir: Path, device: str, compute_type: str | None, threads: int
    ) -> "WhisperEngine":
        import ctranslate2

        require_gpu(
            device, ctranslate2.get_cuda_device_count() if device != "cpu" else 0
        )
        requested = compute_type or default_compute_type("asr", device)
        supported = set(ctranslate2.get_supported_compute_types(backend_device(device)))
        return cls(
            model_dir, device, resolve_ct2_compute_type(requested, supported), threads
        )

    def _final_kwargs(self, language, beam_size, vad, initial_prompt) -> dict:
        kwargs = {
            "language": language,
            "beam_size": beam_size,
            "temperature": list(FINAL_TEMPERATURES),
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.6,
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "without_timestamps": False,
            "vad_filter": vad is not None,
        }
        if vad is not None:
            kwargs["vad_parameters"] = vad.as_dict()
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        return kwargs

    def iter_transcribe(
        self,
        audio,
        *,
        language: str,
        beam_size: int = 1,
        vad: VadProfile | None = FILE_VAD,
        initial_prompt: str | None = None,
    ) -> Iterator[Segment]:
        """Lazy final-decoding segments: nothing runs until the first next()."""
        samples = to_float32_mono(audio)
        if samples.size == 0:
            return
        kwargs = self._final_kwargs(language, beam_size, vad, initial_prompt)
        segments, _info = self._model.transcribe(samples, **kwargs)
        for s in segments:
            yield Segment(float(s.start), float(s.end), s.text.strip())

    def detect_language(self, audio, allowed: Sequence[str]) -> tuple[str, float]:
        """Most probable language among `allowed`, from the first <= 30 s of audio."""
        if not allowed:
            raise ValueError("allowed languages must not be empty")
        samples = to_float32_mono(audio)[: 30 * SAMPLE_RATE]
        _lang, _prob, all_probs = self._model.detect_language(audio=samples)
        probs = dict(all_probs)
        best, best_prob = allowed[0], probs.get(allowed[0], 0.0)
        for lang in allowed[1:]:
            if probs.get(lang, 0.0) > best_prob:
                best, best_prob = lang, probs[lang]
        return best, float(best_prob)

    def transcribe(
        self,
        audio,
        *,
        language: str | None = None,
        beam_size: int = 1,
        vad: VadProfile | None = FILE_VAD,
        initial_prompt: str | None = None,
        partial: bool = False,
    ) -> TranscriptionResult:
        samples = to_float32_mono(audio)
        duration = samples.size / SAMPLE_RATE
        if samples.size == 0:
            return TranscriptionResult("", language or "", [], 0.0)
        if language is not None and not partial:
            parsed = list(
                self.iter_transcribe(
                    samples,
                    language=language,
                    beam_size=beam_size,
                    vad=vad,
                    initial_prompt=initial_prompt,
                )
            )
            info_language = language
        else:
            kwargs = self._final_kwargs(language, beam_size, vad, initial_prompt)
            if partial:
                kwargs.update(beam_size=1, temperature=0.0, without_timestamps=True)
            segments, info = self._model.transcribe(samples, **kwargs)
            parsed = [
                Segment(float(s.start), float(s.end), s.text.strip()) for s in segments
            ]
            info_language = info.language
        text = " ".join(s.text for s in parsed if s.text)
        return TranscriptionResult(text, info_language, parsed, duration)
