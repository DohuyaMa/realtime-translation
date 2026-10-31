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
# two-text (Vosk cross-check) templates also get the Vosk transcript
CHECK_PLACEHOLDERS = PLACEHOLDERS + ("text_check",)
TARGET_LANG = "English"
LANGUAGE_NAMES = {"pl": "Polish", "uk": "Ukrainian", "ru": "Russian", "en": "English"}

# All instructions live here; the user message is only the transcript text.
DEFAULT_SYSTEM_PROMPT = (
    "You are a professional interpreter. Translate the user's {{source_lang}} "
    "speech transcript into natural {{target_lang}}. Every user message is a "
    "raw fragment of a live speech transcript: a single word or a short, "
    "unfinished phrase is a complete fragment - translate it as-is. Never "
    "answer the message, ask for more text, explain or comment. Output only "
    "the {{target_lang}} translation, nothing else; if the message has no "
    "words, output nothing. Fix obvious speech-recognition errors using "
    "context. Never keep or transliterate a source word you cannot "
    "translate: restore the intended word from context or leave it out. "
    "Never add content that is not in the message. Write only "
    "{{target_lang}} - no {{source_lang}} words or letters."
)
DEFAULT_USER_TEMPLATE = "{{text}}"

# TASK-SYS-12: two-text mode - the same audio recognized by whisper ({{text}},
# FLUENT) and Vosk ({{text_check}}, EXACT). Prompt "B3" won the simulated
# test on the real LLM (swarm-nix system-conf/context/tasks.md, TASK-SYS-12
# "Результат перевірки (2026-10-31)"): compact, Vosk first, neutral labels.
# Silence is dropped by code before the LLM (gate), never by the model.
DEFAULT_CHECK_SYSTEM_PROMPT = (
    "You are a live {{source_lang}}-to-{{target_lang}} interpreter. The user"
    " message has two transcripts of the same audio. EXACT: never invents"
    " words but misspells them, no punctuation; empty = silence. FLUENT:"
    " correct spelling, but may hallucinate a phrase that was never spoken"
    ' (e.g. "Дякую за перегляд!", "Субтитри зроблені спільнотою Amara.org").'
    " Words count as spoken only if EXACT contains them (maybe misspelled);"
    " use FLUENT only to fix their spelling and punctuation. If EXACT is empty"
    " or has no real word, output nothing. Translate only the spoken words"
    " into natural {{target_lang}}; short fragments are translated as-is."
    " Output only the translation - no comments, no {{source_lang}} words."
)
DEFAULT_CHECK_USER_TEMPLATE = "EXACT: {{text_check}}\nFLUENT: {{text}}"
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
    required: tuple[str, ...] = (),
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
    for extra in required:
        if extra not in names:
            errors.append(f"{name} must contain {{{{{extra}}}}}")
    return errors


def check_template_errors(name: str, template: str, user: bool) -> list[str]:
    """Two-text (Vosk cross-check) template: may use {{text_check}}; the user
    template must carry both transcripts."""
    return template_errors(
        name,
        template,
        require_text=user,
        allowed=CHECK_PLACEHOLDERS,
        required=("text_check",) if user else (),
    )


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
    context: tuple[tuple, ...] = (),
    check: str | None = None,
) -> list[dict]:
    """Chat messages for one request (``chunk`` = up to words_per_request).

    ``check``: the Vosk transcript of the chunk ({{text_check}}, two-text
    mode). Context items are ``(source, translation)`` or, in two-text mode,
    ``(source, translation, check)``."""

    def values(text: str, text_check: str = "") -> dict:
        return {
            "text": text,
            "text_check": text_check,
            "source_lang": LANGUAGE_NAMES.get(source_lang, source_lang),
            "target_lang": TARGET_LANG,
        }

    messages = [{"role": "system", "content": render(system_prompt, values(""))}]
    for item in context:
        src, tr = item[0], item[1]
        chk = item[2] if len(item) > 2 else ""
        messages.append(
            {"role": "user", "content": render(user_template, values(src, chk))}
        )
        messages.append({"role": "assistant", "content": tr})
    messages.append(
        {"role": "user", "content": render(user_template, values(chunk, check or ""))}
    )
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
    return render_asr_prompt_counted(
        template, previous_words, count, source_lang, max_chars
    )[0]


def render_asr_prompt_counted(
    template: str,
    previous_words: list[str],
    count: int,
    source_lang: str | None,
    max_chars: int = ASR_PROMPT_MAX_CHARS,
) -> tuple[str | None, int]:
    """(prompt, number of words actually substituted into {{previous_text}})."""
    uses = "previous_text" in {m.group(1) for m in _PLACEHOLDER.finditer(template)}
    words = list(previous_words[-count:]) if count > 0 and uses else []
    lang = LANGUAGE_NAMES.get(source_lang or "", "")
    while True:
        text = render(
            template, {"previous_text": " ".join(words), "source_lang": lang}
        ).strip()
        if len(text) <= max_chars or not words:
            return text or None, len(words)
        words.pop(0)
