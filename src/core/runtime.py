"""Edge runtime settings: service discovery, local endpoints and segmentation.

The app gets the list of available services (llm workers, rt-asr, rt-tts) and
their tokens from Django (``django_urls``, own host first; DRF token in
``django_token_file``), keeps only ``allowed_services`` and puts the local
endpoints (``local.*``) first. Without Django it uses the cached list.

Precedence: RT_EDGE_* environment > config.yml > defaults. Values that come
from the environment are listed in `EdgeSettings.managed` (read-only in the UI).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from typing import Any, Literal, Mapping, Optional

from src.segmentation import SegmenterConfig

from .prompt import (
    DEFAULT_ASR_PROMPT,
    DEFAULT_CHECK_SYSTEM_PROMPT,
    DEFAULT_CHECK_USER_TEMPLATE,
    DEFAULT_PREVIOUS_WORDS,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_TEMPLATE,
    DEFAULT_WORDS_PER_REQUEST,
    asr_prompt_errors,
    check_template_errors,
    is_legacy,
    template_errors,
)

DEFAULT_ASR_URL = "http://127.0.0.1:8091"
DEFAULT_LOCAL_TOKEN_FILE = "/run/llama/local-token"
# vosk: rt-vosk, the second (non-generative) ASR that cross-checks whisper
# in translate mode (TASK-SYS-12)
KINDS = ("llm", "asr", "tts", "vosk")
# services.use.<kind> value that switches a kind off (only OPTIONAL_KINDS)
OFF = "off"
OPTIONAL_KINDS = ("vosk",)
MODES = ("auto", "transcribe", "translate")
SOURCE_LANGUAGES = ("auto", "pl", "uk", "ru", "en")


@dataclass(frozen=True)
class Timeouts:
    connect_s: float = 3.0
    asr_s: float = 30.0
    tts_s: float = 30.0


@dataclass(frozen=True)
class LocalUrls:
    """Services on this host; always tried first.

    ``node``: this host's node name in discovery (None = hostname). A
    discovered service of this node is reached at ``local.<kind>``, or at
    127.0.0.1 on its discovered port when ``local.<kind>`` is not set; never
    at the cluster (tailscale) URL Django lists for it.
    """

    llm: Optional[str] = None
    asr: Optional[str] = DEFAULT_ASR_URL
    tts: Optional[str] = None
    vosk: Optional[str] = None
    node: Optional[str] = None


@dataclass(frozen=True)
class LlmSettings:
    session_ttl_s: int = 30
    model: str = "local"
    max_tokens_cap: int = 512
    temperature: float = 0.1
    context_pairs: int = 3
    timeout_s: float = 15.0
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    user_template: str = DEFAULT_USER_TEMPLATE
    # two-text mode (whisper + Vosk cross-check, TASK-SYS-12): {{text}} =
    # whisper, {{vosk_text}} = Vosk; used for every phrase Vosk recognized
    check_system_prompt: str = DEFAULT_CHECK_SYSTEM_PROMPT
    check_user_template: str = DEFAULT_CHECK_USER_TEMPLATE
    # words of recognized text per LLM call; longer text is split (sentence
    # ends preferred) and every chunk goes through user_template
    words_per_request: int = DEFAULT_WORDS_PER_REQUEST


@dataclass(frozen=True)
class ServiceUse:
    """Which instance serves each kind: None = auto (local first, failover
    over the allowed cluster services); a node name = only that node, no
    failover (a down pinned service is an explicit error); ``OFF`` = not
    used at all (only OPTIONAL_KINDS: vosk off = whisper-only translation)."""

    llm: Optional[str] = None
    asr: Optional[str] = None
    tts: Optional[str] = None
    vosk: Optional[str] = None


@dataclass(frozen=True)
class AsrRequestSettings:
    """Sent with every rt-asr request (per user, not per service node)."""

    # Whisper prompt template (vocabulary / names / style). Optional
    # variables {{previous_text}} (last ``previous_words`` recognized words)
    # and {{source_lang}}; without them the text is sent as-is.
    prompt: str = DEFAULT_ASR_PROMPT
    previous_words: int = DEFAULT_PREVIOUS_WORDS


@dataclass(frozen=True)
class TtsRequestSettings:
    """Sent with every rt-tts request; None = the service's default."""

    voice: Optional[str] = None
    speed: Optional[float] = None


@dataclass(frozen=True)
class SegmentationSettings:
    translate: SegmenterConfig = SegmenterConfig.translate()
    transcribe: SegmenterConfig = SegmenterConfig.transcribe()


@dataclass(frozen=True)
class EdgeSettings:
    mode: Literal["auto", "transcribe", "translate"] = "auto"
    django_urls: tuple[str, ...] = ()
    django_token_file: Optional[str] = None
    local: LocalUrls = LocalUrls()
    local_token_file: Optional[str] = DEFAULT_LOCAL_TOKEN_FILE
    # "kind:node" entries of the discovered services the app may use; None = all
    allowed_services: Optional[tuple[str, ...]] = None
    use: ServiceUse = ServiceUse()
    llm: LlmSettings = LlmSettings()
    asr: AsrRequestSettings = AsrRequestSettings()
    tts: TtsRequestSettings = TtsRequestSettings()
    source_language: Literal["auto", "pl", "uk", "ru", "en"] = "auto"
    timeouts: Timeouts = Timeouts(connect_s=3.0, asr_s=30.0, tts_s=30.0)
    segmentation: SegmentationSettings = SegmentationSettings()
    # «Записувати сесію для аналізу»: per-phrase JSONL + segment WAVs
    # (core/session_record.py); explicit opt-in, applied live
    record_session: bool = False
    managed: frozenset[str] = frozenset()
    # config values replaced on load (e.g. old template syntax), for the UI
    notices: tuple[str, ...] = ()

    def has_source(self, kind: str) -> bool:
        """A local endpoint or a Django that can list cluster ones."""
        return bool(getattr(self.local, kind) or self.django_urls)

    def effective_mode(self) -> Literal["transcribe", "translate"]:
        if self.mode == "auto":
            if self.has_source("llm") and self.has_source("tts"):
                return "translate"
            return "transcribe"
        return self.mode

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.mode not in MODES:
            errors.append(f"unknown mode {self.mode!r}")
        if self.source_language not in SOURCE_LANGUAGES:
            errors.append(f"unknown source_language {self.source_language!r}")
        if not self.has_source("asr"):
            errors.append("asr needs local.asr or django_urls")
        if self.mode == "translate":
            for kind in ("llm", "tts"):
                if not self.has_source(kind):
                    errors.append(
                        f"translate mode requires local.{kind} or django_urls"
                    )
        if self.django_urls and not self.django_token_file:
            errors.append("django_urls requires django_token_file")
        for item in self.allowed_services or ():
            kind, _, node = item.partition(":")
            if kind not in KINDS or not node:
                errors.append(f"allowed_services: {item!r} is not <kind>:<node>")
        for kind in KINDS:
            node = getattr(self.use, kind)
            if node == OFF:
                if kind not in OPTIONAL_KINDS:
                    errors.append(f"use.{kind}: {kind} cannot be switched off")
                continue
            if (
                node
                and self.allowed_services is not None
                and f"{kind}:{node}" not in self.allowed_services
            ):
                errors.append(f"use.{kind}: {kind}:{node} is not an allowed service")
        if not 6 <= self.llm.session_ttl_s <= 300:
            errors.append("llm.session_ttl_s must be in 6..300")
        errors += template_errors(
            "llm.system_prompt", self.llm.system_prompt, require_text=False
        )
        errors += template_errors(
            "llm.user_template", self.llm.user_template, require_text=True
        )
        errors += check_template_errors(
            "llm.check_system_prompt", self.llm.check_system_prompt, user=False
        )
        errors += check_template_errors(
            "llm.check_user_template", self.llm.check_user_template, user=True
        )
        errors += asr_prompt_errors("asr.prompt", self.asr.prompt)
        if self.asr.previous_words < 0:
            errors.append("asr.previous_words must be >= 0")
        if self.llm.words_per_request < 1:
            errors.append("llm.words_per_request must be >= 1")
        if self.tts.speed is not None and not 0.25 <= self.tts.speed <= 4.0:
            errors.append("tts.speed must be in 0.25..4.0")
        for name in ("translate", "transcribe"):
            try:
                getattr(self.segmentation, name).validate()
            except ValueError as e:
                errors.append(f"segmentation.{name}: {e}")
        pause = self.segmentation.translate.end_silence_ms
        if pause is not None and not 200 <= pause <= 3000:
            errors.append("segmentation.translate.end_silence_ms must be in 200..3000")
        if self.segmentation.transcribe.hard_max_ms > 900_000:
            errors.append("segmentation.transcribe chunk_max_s must be <= 900")
        return errors


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text(value: Any) -> Optional[str]:
    """Multi-line text (prompts): surrounding whitespace dropped, "" = None."""
    if value is None:
        return None
    return str(value).strip() or None


def _flag(value: Any) -> bool:
    """YAML bool or env string; anything else is an explicit error."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("", "0", "false", "no", "off"):
        return False
    raise ValueError(f"not a boolean: {value!r}")


def _url(value: Any) -> Optional[str]:
    text = _clean(value)
    return text.rstrip("/") if text else None


def _urls(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    items = value.split(",") if isinstance(value, str) else list(value)
    return tuple(u for u in (_url(i) for i in items) if u)


def _items(value: Any) -> tuple[str, ...]:
    items = value.split(",") if isinstance(value, str) else list(value)
    return tuple(t for t in (_clean(i) for i in items) if t)


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


_OBSOLETE = ("asr_urls", "tts_urls", "api_token_file")
_OBSOLETE_LLM = (
    "urls",
    "django_urls",
    "django_token_file",
    "service",
    "lease_ttl_s",
    "direct_url",
    "direct_token_file",
)


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

    obsolete = [k for k in _OBSOLETE if k in svc] + [
        f"llm.{k}" for k in _OBSOLETE_LLM if k in llm_cfg
    ]
    if obsolete:
        from loguru import logger

        logger.warning(
            "config: obsolete keys ignored (services discovered via Django now): {}",
            ", ".join(obsolete),
        )
    ld = LlmSettings()
    notices: list[str] = []

    def template(name: str, default: str) -> str:
        value = _text(llm_cfg.get(name))
        if value is None:
            return default
        if is_legacy(value):
            from loguru import logger

            msg = (
                f"llm.{name}: old {{lang}}/{{text}} syntax - using the default"
                " template; rewrite it with {{source_lang}} / {{text}}"
            )
            logger.warning("config: {}", msg)
            notices.append(msg)
            return default
        return value

    llm = LlmSettings(
        session_ttl_s=int(llm_cfg.get("session_ttl_s", ld.session_ttl_s)),
        model=_clean(pick("RT_EDGE_LLM_MODEL", "llm.model", llm_cfg.get("model")))
        or ld.model,
        max_tokens_cap=int(llm_cfg.get("max_tokens_cap", ld.max_tokens_cap)),
        temperature=float(llm_cfg.get("temperature", ld.temperature)),
        context_pairs=int(llm_cfg.get("context_pairs", ld.context_pairs)),
        timeout_s=float(llm_cfg.get("timeout_s", ld.timeout_s)),
        system_prompt=template("system_prompt", ld.system_prompt),
        user_template=template("user_template", ld.user_template),
        check_system_prompt=template("check_system_prompt", ld.check_system_prompt),
        check_user_template=template("check_user_template", ld.check_user_template),
        words_per_request=int(llm_cfg.get("words_per_request", ld.words_per_request)),
    )
    asr_cfg = svc.get("asr") or {}
    tts_cfg = svc.get("tts") or {}
    speed = tts_cfg.get("speed")
    local_cfg = svc.get("local") or {}
    ld_local = LocalUrls()

    def local(kind: str) -> Optional[str]:
        value = local_cfg.get(kind, getattr(ld_local, kind))
        return _url(pick(f"RT_EDGE_LOCAL_{kind.upper()}_URL", f"local.{kind}", value))

    allowed = pick(
        "RT_EDGE_ALLOWED_SERVICES", "allowed_services", svc.get("allowed_services")
    )
    token_file = pick(
        "RT_EDGE_LOCAL_TOKEN_FILE",
        "local_token_file",
        svc.get("local_token_file", DEFAULT_LOCAL_TOKEN_FILE),
    )
    use_cfg = svc.get("use") or {}

    def use(kind: str) -> Optional[str]:
        node = _clean(use_cfg.get(kind))
        return None if node in (None, "auto") else node

    tcfg = svc.get("timeouts") or {}
    td = Timeouts()
    return EdgeSettings(
        mode=_clean(pick("RT_EDGE_MODE", "mode", svc.get("mode"))) or "auto",
        django_urls=_urls(
            pick("RT_EDGE_DJANGO_URLS", "django_urls", svc.get("django_urls"))
        ),
        django_token_file=_clean(
            pick(
                "RT_EDGE_DJANGO_TOKEN_FILE",
                "django_token_file",
                svc.get("django_token_file"),
            )
        ),
        local=LocalUrls(
            llm=local("llm"),
            asr=local("asr"),
            tts=local("tts"),
            vosk=local("vosk"),
            node=_clean(
                pick("RT_EDGE_LOCAL_NODE", "local.node", local_cfg.get("node"))
            ),
        ),
        local_token_file=_clean(token_file),
        allowed_services=None if allowed is None else _items(allowed),
        use=ServiceUse(**{kind: use(kind) for kind in KINDS}),
        llm=llm,
        asr=AsrRequestSettings(
            # null/absent = default template; "" = no prompt at all
            prompt=(
                DEFAULT_ASR_PROMPT
                if asr_cfg.get("prompt") is None
                else str(asr_cfg["prompt"]).strip()
            ),
            previous_words=int(asr_cfg.get("previous_words", DEFAULT_PREVIOUS_WORDS)),
        ),
        tts=TtsRequestSettings(
            voice=_clean(tts_cfg.get("voice")),
            speed=None if speed in (None, "") else float(speed),
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
        record_session=_flag(
            pick(
                "RT_EDGE_RECORD_SESSION",
                "record_session",
                svc.get("record_session", False),
            )
        ),
        managed=frozenset(managed),
        notices=tuple(notices),
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
