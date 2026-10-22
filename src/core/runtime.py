"""Edge runtime settings: remote services, LLM source and segmentation.

Precedence: RT_EDGE_* environment > config.yml > defaults. Values that come
from the environment are listed in `EdgeSettings.managed` (read-only in the UI).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from typing import Any, Literal, Mapping, Optional

from src.segmentation import SegmenterConfig

DEFAULT_ASR_URL = "http://127.0.0.1:8091"
MODES = ("auto", "transcribe", "translate")
SOURCE_LANGUAGES = ("auto", "pl", "uk", "ru", "en")


@dataclass(frozen=True)
class Timeouts:
    connect_s: float = 3.0
    asr_s: float = 30.0
    tts_s: float = 30.0


@dataclass(frozen=True)
class LlmSettings:
    django_urls: tuple[str, ...] = ()
    django_token_file: Optional[str] = None
    service: Optional[str] = None
    lease_ttl_s: int = 60
    direct_url: Optional[str] = None
    direct_token_file: Optional[str] = None
    model: str = "local"
    max_tokens_cap: int = 512
    temperature: float = 0.1
    context_pairs: int = 3
    timeout_s: float = 15.0

    def source(self) -> Literal["lease", "direct", "none"]:
        if self.django_urls:
            return "lease"
        if self.direct_url:
            return "direct"
        return "none"


@dataclass(frozen=True)
class SegmentationSettings:
    translate: SegmenterConfig = SegmenterConfig.translate()
    transcribe: SegmenterConfig = SegmenterConfig.transcribe()


@dataclass(frozen=True)
class EdgeSettings:
    mode: Literal["auto", "transcribe", "translate"] = "auto"
    asr_urls: tuple[str, ...] = (DEFAULT_ASR_URL,)
    tts_urls: tuple[str, ...] = ()
    llm: LlmSettings = LlmSettings()
    api_token_file: Optional[str] = None
    source_language: Literal["auto", "pl", "uk", "ru", "en"] = "auto"
    timeouts: Timeouts = Timeouts(connect_s=3.0, asr_s=30.0, tts_s=30.0)
    segmentation: SegmentationSettings = SegmentationSettings()
    managed: frozenset[str] = frozenset()

    def effective_mode(self) -> Literal["transcribe", "translate"]:
        if self.mode == "auto":
            if self.llm.source() != "none" and self.tts_urls:
                return "translate"
            return "transcribe"
        return self.mode

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.mode not in MODES:
            errors.append(f"unknown mode {self.mode!r}")
        if self.source_language not in SOURCE_LANGUAGES:
            errors.append(f"unknown source_language {self.source_language!r}")
        if not self.asr_urls:
            errors.append("asr_urls must not be empty")
        if self.mode == "translate":
            if self.llm.source() == "none":
                errors.append(
                    "translate mode requires llm.django_urls or llm.direct_url"
                )
            if not self.tts_urls:
                errors.append("translate mode requires tts_urls")
        if self.llm.source() == "lease" and not self.llm.django_token_file:
            errors.append("lease mode requires llm.django_token_file")
        if not 15 <= self.llm.lease_ttl_s <= 300:
            errors.append("llm.lease_ttl_s must be in 15..300")
        for name in ("translate", "transcribe"):
            try:
                getattr(self.segmentation, name).validate()
            except ValueError as e:
                errors.append(f"segmentation.{name}: {e}")
        if self.segmentation.transcribe.hard_max_ms > 900_000:
            errors.append("segmentation.transcribe chunk_max_s must be <= 900")
        return errors


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _url(value: Any) -> Optional[str]:
    text = _clean(value)
    return text.rstrip("/") if text else None


def _urls(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    items = value.split(",") if isinstance(value, str) else list(value)
    return tuple(u for u in (_url(i) for i in items) if u)


def _seg_translate(cfg: Mapping) -> SegmenterConfig:
    names = {f.name for f in fields(SegmenterConfig)}
    return replace(
        SegmenterConfig.translate(), **{k: v for k, v in cfg.items() if k in names}
    )


def _seg_transcribe(cfg: Mapping) -> SegmenterConfig:
    base = SegmenterConfig.transcribe(
        float(cfg.get("chunk_min_s", 300)), float(cfg.get("chunk_max_s", 600))
    )
    extra = {
        k: cfg[k]
        for k in ("soft_silence_ms", "lookback_ms", "keep_silence_ms")
        if k in cfg
    }
    return replace(base, **extra)


def resolve_edge_settings(cfg: Mapping, environ: Mapping) -> EdgeSettings:
    cfg = cfg or {}
    svc = cfg.get("services") or {}
    llm_cfg = svc.get("llm") or {}
    seg_cfg = cfg.get("segmentation") or {}
    managed: set[str] = set()

    def pick(env_key: str, name: str, value: Any) -> Any:
        if _clean(environ.get(env_key)):
            managed.add(name)
            return environ[env_key]
        return value

    ld = LlmSettings()
    llm = LlmSettings(
        django_urls=_urls(
            pick("RT_EDGE_DJANGO_URLS", "llm.django_urls", llm_cfg.get("django_urls"))
        ),
        django_token_file=_clean(
            pick(
                "RT_EDGE_DJANGO_TOKEN_FILE",
                "llm.django_token_file",
                llm_cfg.get("django_token_file"),
            )
        ),
        service=_clean(
            pick("RT_EDGE_LLM_SERVICE", "llm.service", llm_cfg.get("service"))
        ),
        lease_ttl_s=int(llm_cfg.get("lease_ttl_s", ld.lease_ttl_s)),
        direct_url=_url(
            pick("RT_EDGE_LLM_DIRECT_URL", "llm.direct_url", llm_cfg.get("direct_url"))
        ),
        direct_token_file=_clean(
            pick(
                "RT_EDGE_LLM_DIRECT_TOKEN_FILE",
                "llm.direct_token_file",
                llm_cfg.get("direct_token_file"),
            )
        ),
        model=_clean(pick("RT_EDGE_LLM_MODEL", "llm.model", llm_cfg.get("model")))
        or ld.model,
        max_tokens_cap=int(llm_cfg.get("max_tokens_cap", ld.max_tokens_cap)),
        temperature=float(llm_cfg.get("temperature", ld.temperature)),
        context_pairs=int(llm_cfg.get("context_pairs", ld.context_pairs)),
        timeout_s=float(llm_cfg.get("timeout_s", ld.timeout_s)),
    )
    asr_urls = _urls(pick("RT_EDGE_ASR_URLS", "asr_urls", svc.get("asr_urls"))) or (
        DEFAULT_ASR_URL,
    )
    tcfg = svc.get("timeouts") or {}
    td = Timeouts()
    return EdgeSettings(
        mode=_clean(pick("RT_EDGE_MODE", "mode", svc.get("mode"))) or "auto",
        asr_urls=asr_urls,
        tts_urls=_urls(pick("RT_EDGE_TTS_URLS", "tts_urls", svc.get("tts_urls"))),
        llm=llm,
        api_token_file=_clean(
            pick("RT_EDGE_API_TOKEN_FILE", "api_token_file", svc.get("api_token_file"))
        ),
        source_language=_clean(svc.get("source_language")) or "auto",
        timeouts=Timeouts(
            connect_s=float(tcfg.get("connect_s", td.connect_s)),
            asr_s=float(tcfg.get("asr_s", td.asr_s)),
            tts_s=float(tcfg.get("tts_s", td.tts_s)),
        ),
        segmentation=SegmentationSettings(
            translate=_seg_translate(seg_cfg.get("translate") or {}),
            transcribe=_seg_transcribe(seg_cfg.get("transcribe") or {}),
        ),
        managed=frozenset(managed),
    )


_runtime_config: Optional[EdgeSettings] = None


def get_runtime_config() -> EdgeSettings:
    global _runtime_config
    if _runtime_config is None:
        from .config import get_config_manager

        _runtime_config = resolve_edge_settings(
            get_config_manager().get_all(), os.environ
        )
    return _runtime_config


def reset_runtime_config() -> None:
    global _runtime_config
    _runtime_config = None
