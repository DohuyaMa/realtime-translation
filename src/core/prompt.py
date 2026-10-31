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
# two-text (Vosk cross-check) templates also get the Vosk transcript
CHECK_PLACEHOLDERS = PLACEHOLDERS + ("vosk_text",)
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
    " translate HINT words that have no counterpart in TEXT. NEXT, when given,"
    " is the speech right after TEXT, only for understanding: never translate"
    " NEXT or any part of it. TEXT is the next fragment of a live speech:"
    " continue your previous translations consistently. Never transliterate."
    " Short fragments are translated as-is. Output only the {{target_lang}}"
    " translation of TEXT - no comments, no {{source_lang}} words."
    + IT_DOMAIN
    + " {{terms}}"
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


def system_prompt_errors(name: str, template: str) -> list[str]:
    """Plain system prompt: the phrase placeholders plus {{terms}}."""
    return template_errors(name, template, False, PLACEHOLDERS + (TERMS,))


def check_template_errors(name: str, template: str, user: bool) -> list[str]:
    """Two-text (Vosk cross-check) template: may use {{vosk_text}}; the user
    template must carry both transcripts; the system prompt may use
    {{terms}}."""
    return template_errors(
        name,
        template,
        require_text=user,
        allowed=CHECK_PLACEHOLDERS + (() if user else (TERMS,)),
        required=("vosk_text",) if user else (),
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
    check: str | None = None,
    terms: Sequence[str] = (),
    next_text: str = "",
) -> list[dict]:
    """Chat messages for one request (``chunk`` = up to words_per_request).

    ``check``: the Vosk transcript of the chunk ({{vosk_text}}, two-text
    mode). Context items are ``(source, translation)`` or, in two-text mode,
    ``(source, translation, check)``. ``terms``: the glossary ({{terms}} of
    the system prompt). ``next_text``: speech recognized after the chunk -
    appended as the ``NEXT_LABEL`` line of the last message only."""

    def values(text: str, vosk_text: str = "") -> dict:
        return {
            "text": text,
            "vosk_text": vosk_text,
            "source_lang": LANGUAGE_NAMES.get(source_lang, source_lang),
            "target_lang": TARGET_LANG,
        }

    system = render_system(system_prompt, values(""), terms)
    messages = [{"role": "system", "content": system}]
    for item in context:
        src, tr = item[0], item[1]
        chk = item[2] if len(item) > 2 else ""
        messages.append(
            {"role": "user", "content": render(user_template, values(src, chk))}
        )
        messages.append({"role": "assistant", "content": tr})
    content = render(user_template, values(chunk, check or ""))
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
