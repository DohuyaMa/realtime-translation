"""LLM translation prompt templates and word-limited chunking.

Placeholders use double braces: ``{{text}}`` (required in the phrase
template: where the words of one request go), ``{{source_lang}}``,
``{{target_lang}}``. The single-brace ``{lang}`` / ``{text}`` syntax of
older configs is not supported: such a value is reported and the default
template is used instead.
"""

from __future__ import annotations

import re
from typing import Mapping

PLACEHOLDERS = ("text", "source_lang", "target_lang")
TARGET_LANG = "English"
LANGUAGE_NAMES = {"pl": "Polish", "uk": "Ukrainian", "ru": "Russian", "en": "English"}

DEFAULT_SYSTEM_PROMPT = (
    "You are a professional interpreter. Translate the user's {{source_lang}} "
    "speech transcript into natural {{target_lang}}. Output only the "
    "{{target_lang}} translation, nothing else. Fix obvious speech-recognition "
    "errors using context."
)
DEFAULT_USER_TEMPLATE = "{{text}}"
DEFAULT_WORDS_PER_REQUEST = 40

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
_LEGACY = re.compile(r"(?<!\{)\{(lang|text)\}(?!\})")
_SENTENCE_END = re.compile(r"(?<=[.!?…;])\s+")


def is_legacy(template: str) -> bool:
    """Old single-brace ``{lang}`` / ``{text}`` placeholders."""
    return bool(_LEGACY.search(template))


def template_errors(
    name: str,
    template: str,
    require_text: bool,
    allowed: tuple[str, ...] = PLACEHOLDERS,
) -> list[str]:
    errors = []
    if is_legacy(template):
        errors.append(
            f"{name}: old placeholder syntax {{lang}}/{{text}};"
            " use {{source_lang}} / {{text}}"
        )
    names = {m.group(1) for m in _PLACEHOLDER.finditer(template)}
    unknown = sorted(names - set(allowed))
    if unknown:
        errors.append(
            f"{name}: unknown placeholder(s) {', '.join(unknown)};"
            f" allowed: {', '.join('{{' + p + '}}' for p in allowed)}"
        )
    if require_text and "text" not in names:
        errors.append(f"{name} must contain {{{{text}}}} (where the words go)")
    return errors


def render(template: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def split_words(text: str, limit: int) -> list[str]:
    """Chunks of at most ``limit`` words, cut at sentence ends when possible."""
    if limit < 1:
        raise ValueError("words per request must be >= 1")
    chunks: list[str] = []
    current: list[str] = []
    for sentence in _SENTENCE_END.split(text.strip()):
        words = sentence.split()
        if current and len(current) + len(words) > limit:
            chunks.append(" ".join(current))
            current = []
        while len(words) > limit:  # a sentence longer than the limit
            chunks.append(" ".join(words[:limit]))
            words = words[limit:]
        current.extend(words)
    if current:
        chunks.append(" ".join(current))
    return chunks


def build_messages(
    system_prompt: str,
    user_template: str,
    chunk: str,
    source_lang: str,
    context: tuple[tuple[str, str], ...] = (),
) -> list[dict]:
    """Chat messages for one request (``chunk`` = up to words_per_request)."""

    def values(text: str) -> dict:
        return {
            "text": text,
            "source_lang": LANGUAGE_NAMES.get(source_lang, source_lang),
            "target_lang": TARGET_LANG,
        }

    messages = [{"role": "system", "content": render(system_prompt, values(""))}]
    for src, tr in context:
        messages.append({"role": "user", "content": render(user_template, values(src))})
        messages.append({"role": "assistant", "content": tr})
    messages.append({"role": "user", "content": render(user_template, values(chunk))})
    return messages


# -- Whisper prompt (rt-asr ``prompt`` / initial_prompt) ----------------------

ASR_PLACEHOLDERS = ("previous_text", "source_lang")
# Optional variables: a template without them is sent as-is (nothing is
# appended). Default keeps continuity: just the previous words.
DEFAULT_ASR_PROMPT = "{{previous_text}}"
DEFAULT_PREVIOUS_WORDS = 30
# Whisper's prompt holds at most 223 tokens; Cyrillic/Polish text runs at
# ~1 token per 1.5 chars, so 300 chars stays below the limit.
ASR_PROMPT_MAX_CHARS = 300


def asr_prompt_errors(name: str, template: str) -> list[str]:
    errors = template_errors(name, template, False, ASR_PLACEHOLDERS)
    static = render(template, {"previous_text": "", "source_lang": "Ukrainian"})
    if len(static.strip()) > ASR_PROMPT_MAX_CHARS:
        errors.append(
            f"{name}: {len(static.strip())} characters without previous words;"
            f" Whisper accepts ~{ASR_PROMPT_MAX_CHARS}"
        )
    return errors


def render_asr_prompt(
    template: str,
    previous_words: list[str],
    count: int,
    source_lang: str | None,
    max_chars: int = ASR_PROMPT_MAX_CHARS,
) -> str | None:
    """Rendered Whisper prompt; ``{{previous_text}}`` = the last ``count``
    words, dropped from the oldest (whole words) until within ``max_chars``."""
    words = list(previous_words[-count:]) if count > 0 else []
    lang = LANGUAGE_NAMES.get(source_lang or "", "")
    while True:
        text = render(
            template, {"previous_text": " ".join(words), "source_lang": lang}
        ).strip()
        if len(text) <= max_chars or not words:
            return text or None
        words.pop(0)
