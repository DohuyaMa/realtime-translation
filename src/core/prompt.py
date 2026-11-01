"""LLM translation prompt templates and word-limited chunking.

Placeholders use double braces: ``{{text}}`` (required in the phrase
template: where the words of one request go), ``{{source_lang}}``,
``{{target_lang}}``; system prompts also take ``{{terms}}`` (the user's
glossary as one sentence, or nothing when the glossary is empty). The
single-brace ``{lang}`` / ``{text}`` syntax of older configs is not
supported: such a value is reported and the default template is used
instead.
"""

from __future__ import annotations

import re
from typing import Mapping, Sequence

PLACEHOLDERS = ("text", "source_lang", "target_lang")
# sound-mode templates also get the phonetic (CTC letter) transcript
SOUND_TEXT = "sound_text"
SOUND_PLACEHOLDERS = PLACEHOLDERS + (SOUND_TEXT,)
# placeholders of removed features -> what replaced them (explicit error)
REMOVED_PLACEHOLDERS = {"vosk_text": "{{sound_text}} (Vosk was removed)"}
# system prompts only: the glossary sentence (empty glossary -> "")
TERMS = "terms"
TERMS_SENTENCE = "Known terms (always written in English): {}."
# Both default system prompts: the owner speaks about IT; whisper writes IT
# terms in Cyrillic or mishears them (live test 2026-10-31 §12).
IT_DOMAIN = (
    " The speech is about IT and software development. IT terms often arrive"
    " as Cyrillic transliterations or misrecognitions of English terms (e.g."
    " «церен» = CRM, «плейврайд» = Playwright, «SAS» = SaaS, «медиабарієри» ="
    " media buyers, «деплой» = deploy): translate each one as the correct"
    " English term."
)

# «Словник термінів» (services.llm.terms): given to the LLM as {{terms}}
# (whisper's prompt stays as it was - owner 2026-10-31). Common IT terms:
# the owner's speech is about IT (live test §12).
DEFAULT_TERMS = (
    "API",
    "backend",
    "frontend",
    "full-stack",
    "DevOps",
    "CI/CD",
    "Git",
    "GitHub",
    "pull request",
    "merge",
    "deploy",
    "release",
    "production",
    "staging",
    "Docker",
    "Kubernetes",
    "cloud",
    "AWS",
    "Linux",
    "NixOS",
    "database",
    "PostgreSQL",
    "Redis",
    "SQL",
    "Python",
    "TypeScript",
    "JavaScript",
    "React",
    "Next.js",
    "Node.js",
    "Django",
    "REST",
    "JSON",
    "LLM",
    "AI",
    "ML",
    "SaaS",
    "CRM",
    "MVP",
    "UI",
    "UX",
    "QA",
    "Playwright",
    "Cypress",
    "Jira",
    "Scrum",
    "sprint",
    "тікет",
    "баг",
    "фіча",
    "реліз",
    "деплой",
    "бекенд",
    "фронтенд",
    "стек",
    "фреймворк",
)


def parse_terms(value) -> tuple[str, ...]:
    """Glossary from config/UI: a list, or text with terms separated by
    commas or new lines; blanks and repeats dropped (first spelling kept)."""
    items = re.split(r"[,\n]", value) if isinstance(value, str) else list(value)
    seen: dict[str, str] = {}
    for item in items:
        term = " ".join(str(item).split())
        if term and term.lower() not in seen:
            seen[term.lower()] = term
    return tuple(seen.values())


def terms_sentence(terms: Sequence[str]) -> str:
    """{{terms}}: one sentence naming the glossary, "" when it is empty."""
    return TERMS_SENTENCE.format(", ".join(terms)) if terms else ""


TARGET_LANG = "English"
LANGUAGE_NAMES = {"pl": "Polish", "uk": "Ukrainian", "ru": "Russian", "en": "English"}

# All instructions live here. The user message (``build_messages``) is
# ``TEXT: <unit>`` - the next unit of the live transcript - and, when more
# speech was already recognized after it, a last line ``NEXT (do not
# translate): <words>`` (``NEXT_LABEL``): only for understanding where TEXT
# goes (spec 2026-10-31 §4); ``core.output_guard`` catches its leakage.
DEFAULT_SYSTEM_PROMPT = (
    "You are a professional simultaneous interpreter. Translate the user's"
    " {{source_lang}} speech transcript into natural {{target_lang}}. Every"
    " user message gives TEXT: the next raw fragment of a live speech"
    " transcript - a single word or a short, unfinished phrase is a complete"
    " fragment, translate it as-is, continuing your previous translations"
    " consistently. A message may also give NEXT: the words spoken right after"
    " TEXT, only for understanding - never translate NEXT or any part of it."
    " Never answer the message, ask for more text, explain or comment. Output"
    " only the {{target_lang}} translation of TEXT, nothing else; if TEXT has"
    " no words, output nothing. Fix obvious speech-recognition errors using"
    " context. Never keep or transliterate a source word you cannot"
    " translate: restore the intended word from context or leave it out."
    " Never add content that is not in TEXT. Write only {{target_lang}} - no"
    " {{source_lang}} words or letters." + IT_DOMAIN + " {{terms}}"
)
DEFAULT_USER_TEMPLATE = "TEXT: {{text}}"
NEXT_LABEL = "NEXT (do not translate): "

# Sound mode ("condition F", owner 2026-11-01): the unit's audio recognized
# twice - rt-phonetic's character-level CTC transcript ({{sound_text}},
# SOUND: the PRIMARY source - no dictionary, so it cannot put a confident
# wrong word in) and whisper ({{text}}, TEXT: only spelling, word boundaries,
# numbers). As a mere hint the letter transcript did NOT help - whisper's
# confident errors survived - so SOUND is what gets translated
# (context/phonetic-bench-2026-11-01.md). The wording ("fluent sentences",
# "never transliterate", the name examples) was tuned by
# scripts/replay_session.py on the owner's 39 live units: a first draft
# made Qwen3.5-4B transliterate SOUND into Latin letters from unit 12 on
# (the bad pairs fed the context). Hallucinated units are dropped by
# code before the LLM (``core.crosscheck.gate_reason``), never by the model.
DEFAULT_SOUND_SYSTEM_PROMPT = (
    "You are a live {{source_lang}}-to-{{target_lang}} interpreter. Translate"
    " each fragment of live speech into natural, fluent {{target_lang}}"
    " sentences with normal capitalization and punctuation. Each user message"
    " gives two recognitions of the same fragment. SOUND is a letter-by-letter"
    " transcript of what was actually heard: no dictionary, no punctuation,"
    " some letters off, words sometimes split or merged; names and IT terms"
    " are spelled by sound in Cyrillic letters (for example «сайборг» is the"
    " server Cyborg, «ес кей топ» is SKTOP, «воск» is the Vosk recognizer,"
    " «віспер» is the Whisper recognizer). TEXT is another recognizer's"
    " output: well spelled, but it may confidently write a wrong word. Use"
    " TEXT only for spelling, word boundaries and numbers; where SOUND and"
    " TEXT disagree, trust SOUND. Translate the meaning of what SOUND says -"
    " every clause, nothing added. NEXT, when given, is the speech right"
    " after, only for understanding: never translate it. Continue your"
    " previous translations consistently; short fragments are translated"
    " as-is. Never transliterate: every {{source_lang}} word becomes an"
    " {{target_lang}} word. Never explain, comment, ask for more text or"
    " mention SOUND or TEXT. Output only the {{target_lang}} translation."
    " {{terms}}"
)
DEFAULT_SOUND_USER_TEMPLATE = "SOUND: {{sound_text}}\nTEXT: {{text}}"
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
    for old in sorted(names & set(REMOVED_PLACEHOLDERS)):
        errors.append(
            f"{name}: {{{{{old}}}}} is removed; use {REMOVED_PLACEHOLDERS[old]}"
        )
    unknown = sorted(names - set(allowed) - set(REMOVED_PLACEHOLDERS))
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


def system_prompt_errors(name: str, template: str) -> list[str]:
    """Plain system prompt: the phrase placeholders plus {{terms}}."""
    return template_errors(name, template, False, PLACEHOLDERS + (TERMS,))


def sound_template_errors(name: str, template: str, user: bool) -> list[str]:
    """Sound-mode template: may use {{sound_text}}; the user template must
    carry both transcripts; the system prompt may use {{terms}}."""
    return template_errors(
        name,
        template,
        require_text=user,
        allowed=SOUND_PLACEHOLDERS + (() if user else (TERMS,)),
        required=(SOUND_TEXT,) if user else (),
    )


def render(template: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def render_system(
    template: str, values: Mapping[str, str], terms: Sequence[str]
) -> str:
    """System prompt: {{terms}} = ``terms_sentence``; an empty glossary
    leaves no blank behind (``... letters. {{terms}}`` -> ``... letters.``)."""
    sentence = terms_sentence(terms)
    out = render(template, {**values, TERMS: sentence})
    if not sentence:
        out = re.sub(r"[ \t]*\{\{\s*terms\s*\}\}[ \t]*", " ", template)
        out = re.sub(r" +(?=\n|$)", "", render(out, {**values, TERMS: ""}))
    return out.strip()


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
    sound: str | None = None,
    terms: Sequence[str] = (),
    next_text: str = "",
) -> list[dict]:
    """Chat messages for one request (``chunk`` = up to words_per_request).

    ``sound``: the phonetic transcript of the chunk ({{sound_text}}, sound
    mode). Context items are ``(source, translation)`` or, in sound mode,
    ``(source, translation, sound)``. ``terms``: the glossary ({{terms}} of
    the system prompt). ``next_text``: speech recognized after the chunk -
    appended as the ``NEXT_LABEL`` line of the last message only."""

    def values(text: str, sound_text: str = "") -> dict:
        return {
            "text": text,
            SOUND_TEXT: sound_text,
            "source_lang": LANGUAGE_NAMES.get(source_lang, source_lang),
            "target_lang": TARGET_LANG,
        }

    system = render_system(system_prompt, values(""), terms)
    messages = [{"role": "system", "content": system}]
    for item in context:
        src, tr = item[0], item[1]
        snd = item[2] if len(item) > 2 else ""
        messages.append(
            {"role": "user", "content": render(user_template, values(src, snd))}
        )
        messages.append({"role": "assistant", "content": tr})
    content = render(user_template, values(chunk, sound or ""))
    if next_text.strip():
        content += "\n" + NEXT_LABEL + next_text.strip()
    messages.append({"role": "user", "content": content})
    return messages


# -- Whisper prompt (rt-asr ``prompt`` / initial_prompt) ----------------------

ASR_PLACEHOLDERS = ("source_lang",)
# The user's part of the Whisper prompt (glossary, names, style); rt-asr's
# stream appends the text it has already committed itself. "" = none.
DEFAULT_ASR_PROMPT = ""
# Whisper's prompt holds at most 223 tokens; Cyrillic/Polish text runs at
# ~1 token per 1.5 chars, and rt-asr adds <= 200 chars of committed text.
ASR_PROMPT_MAX_CHARS = 120


def asr_prompt_errors(name: str, template: str) -> list[str]:
    errors = template_errors(name, template, False, ASR_PLACEHOLDERS)
    static = render(template, {"source_lang": "Ukrainian"})
    if len(static.strip()) > ASR_PROMPT_MAX_CHARS:
        errors.append(
            f"{name}: {len(static.strip())} characters; with the committed"
            f" text rt-asr adds, Whisper accepts ~{ASR_PROMPT_MAX_CHARS}"
        )
    return errors


def render_asr_prompt(template: str, source_lang: str | None) -> str | None:
    """The user's Whisper prompt of a stream (None = none)."""
    lang = LANGUAGE_NAMES.get(source_lang or "", "")
    return render(template, {"source_lang": lang}).strip() or None
