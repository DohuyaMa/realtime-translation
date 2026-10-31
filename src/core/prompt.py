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
CHECK_PLACEHOLDERS = PLACEHOLDERS + ("vosk_text",)
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
# TEXT: the PRIMARY transcript, translated whole) and Vosk ({{vosk_text}},
# HINT: only fixes misrecognized TEXT words). The earlier "B3" prompt (Vosk
# decides which words were spoken) cut live translations down to the few
# words a sparse Vosk-nano transcript had (live test 2026-10-31 §10); this
# one won the real-LLM re-test there. Hallucinated phrases are vetoed by
# code before the LLM (``core.crosscheck.gate_reason``), never by the model.
DEFAULT_CHECK_SYSTEM_PROMPT = (
    "You are a live {{source_lang}}-to-{{target_lang}} interpreter. The user"
    " message has two independent speech-recognition transcripts of the same"
    " audio. TEXT is the main one: fluent, but some words may be misheard."
    " HINT is rougher: often garbled, with words missing. Translate everything"
    " TEXT says - every clause, never shortened or summarized; a word missing"
    " from HINT is still translated. TEXT words that are misheard - not real"
    " words, or out of place - are corrected from HINT, which often has the"
    " right word where TEXT is wrong. HINT may run past the end of TEXT: never"
    " translate HINT words that have no counterpart in TEXT. Never"
    " transliterate. Short fragments are translated as-is. Output only the"
    " {{target_lang}} translation - no comments, no {{source_lang}} words."
)
DEFAULT_CHECK_USER_TEMPLATE = "TEXT: {{text}}\nHINT: {{vosk_text}}"
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
    """Two-text (Vosk cross-check) template: may use {{vosk_text}}; the user
    template must carry both transcripts."""
    return template_errors(
        name,
        template,
        require_text=user,
        allowed=CHECK_PLACEHOLDERS,
        required=("vosk_text",) if user else (),
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

    ``check``: the Vosk transcript of the chunk ({{vosk_text}}, two-text
    mode). Context items are ``(source, translation)`` or, in two-text mode,
    ``(source, translation, check)``."""

    def values(text: str, vosk_text: str = "") -> dict:
        return {
            "text": text,
            "vosk_text": vosk_text,
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
