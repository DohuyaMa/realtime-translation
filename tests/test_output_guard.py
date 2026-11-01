"""LLM output guard: a chat/meta reply never reaches TTS (owner 2026-10-31)."""

import pytest

from src.core.output_guard import (
    EN_DICT_ENV,
    english_words,
    min_output_words,
    reject_reason,
)

OWNER_REPLY = (
    "The content is missing. Please provide the Polish text you would like me"
    " to translate into English, and I will give you a natural-sounding"
    " translation with any necessary context-based corrections for"
    " speech-recognition errors."
)


@pytest.mark.parametrize("source", ["медіа", "медіа контент"])
def test_owner_cases_are_rejected(source):
    assert reject_reason(source, OWNER_REPLY) == "too_long"


@pytest.mark.parametrize(
    "source, output",
    [
        ("media", "Media"),
        ("dziękuję bardzo", "Thank you very much."),
        ("no dobra", "Okay then."),
        ("jak się masz", "How are you?"),
        ("tak", "one two three four five six seven eight"),  # floor of 8
    ],
)
def test_short_translations_pass(source, output):
    assert reject_reason(source, output) is None


def test_normal_long_translation_passes():
    source = (
        "Wczoraj wieczorem poszliśmy z przyjaciółmi do nowej restauracji w"
        " centrum miasta i jedzenie było naprawdę bardzo dobre"
    )
    output = (
        "Last night we went with friends to a new restaurant in the city"
        " centre and the food was very good."
    )
    assert len(output.split()) == 20
    assert reject_reason(source, output) is None


def test_output_over_three_times_the_source_is_rejected():
    assert reject_reason("tak", "one two three four five six seven eight nine") == (
        "too_long"
    )
    source = "a b c d e"  # 5 words -> limit 15
    assert reject_reason(source, " ".join(["w"] * 15)) is None
    assert reject_reason(source, " ".join(["w"] * 16)) == "too_long"


@pytest.mark.parametrize(
    "output",
    [
        # live test 2026-10-31 §4: "миші" -> ASR "Нишки" -> left in the output
        'To move tabs, press the "Tabs" panel with the right mouse button'
        ' ("Niшки") and select "Show Tabs Vertically".',
        "Okay, привіт.",
    ],
)
def test_cyrillic_in_the_english_translation_is_rejected(output):
    source = "Щоб перемістити вкладки, натисніть панель вкладок правою кнопкою"
    assert reject_reason(source, output) == "foreign_script"


def test_latin_diacritics_and_dashes_pass():
    assert reject_reason("kawiarnia", "Café — naïve façade.") is None


def test_next_leakage_is_rejected():
    text = "Не витрачайте час на пошук інформації."  # 6 words: too_long > 18
    nxt = "Картки постійного покупця отримуйте бонуси"
    assert (
        reject_reason(text, "Don't waste time searching for information.", nxt) is None
    )
    # the NEXT part translated too: 17 words > 2 x 6 + 4
    leaked = (
        "Don't waste time searching for information. Loyalty cards from regular"
        " customers, get your bonuses right now please."
    )
    assert reject_reason(text, leaked, nxt) == "next_leak"
    # without NEXT the length rule is the plain too_long one
    assert reject_reason(text, leaked) is None
    # prompt labels echoed
    assert reject_reason(text, "TEXT: Don't waste time.", nxt) == "next_leak"
    assert reject_reason(text, "Don't waste time. NEXT", "") == "next_leak"
    # NEXT words copied (Latin source)
    out = "Don't waste time karty stałego klienta"
    assert reject_reason("Nie trać czasu.", out, "karty stałego klienta") == "next_leak"


# the 39 live translations of session 20261101-154159 (owner's voice, IT
# talk about this very pipeline: "translation", "model", "recording" are
# normal words here) - the meta check must pass every one
LIVE_2026_11_01 = [
    ("Добре.", "Okay.", ""),
    ("Прогрею, добре.", "I'll warm it up, okay.", ""),
    ("Та, ти на да, збитим.", "Yes, you're on it.", ""),
    ("Тверс.", "Tvers.", ""),
    (
        "Ціна на звичайних фразах без термінів перекладає.",
        "The price of regular phrases without terms translates.",
        "",
    ),
    (
        "Преклад стоїть трохи гіршим, але й помітно.",
        "The translation stands a bit worse, but noticeably so.",
        "Приблизно 4 віта.",
    ),
    (
        "Приблизно 4 відповідність та замість перекладу містять пояснення моделі.",
        "Approximately 4 correspondences, and instead of the translation they contain explanations from the model.",  # noqa: E501
        "І такі відповіді.",
    ),
    (
        "І такі відповіді за стосунок має відкидати.",
        "And such answers, regarding the application, must be rejected.",
        "",
    ),
    (
        "Перевірка висперстоянно тіювшою.",
        "Checking the paper's resistance to curling.",
        "Нова модель в бремі з виспі.",
    ),
    ("Нова модель в бремі з виспі.", "New model in the browser with cookies.", ""),
    ("З схожість у букві.", "With similarity in the letter.", ""),
    (
        "А не цілих слів із 66 фраз можна хипно відкинути найбільше одну.",
        "Not quite whole words; with 66 phrases, you can hypnagogically dismiss at most one.",  # noqa: E501
        "А воск зараз...",
    ),
    ("А воск зараз відповідає дві.", "And wax now answers two.", ""),
    (
        "Мені не фраза, що і мету іки.",
        'I don\'t have a phrase that is both "metu" and "iki".',
        "",
    ),
    ("Вигодки.", "Bonuses.", ""),
    ("Виспер.", "Visper.", "Голові тюсі."),
    (
        "Головне зникає випадок воск.",
        "The main thing disappears by chance.",
        "Не знаю.",
    ),
    ("Не знає слова.", "Doesn't know the word.", "А отже це"),
    ("А отже це тиша.", "And so it is silence.", ""),
    ("Швидкість.", "Speed.", ""),
    (
        "Модель працює на процесорі без видеокарти 0 .2 .28.",
        "The model runs on a CPU without a graphics card 0.2.28.",
        "",
    ),
    (
        "Секунді на секунди мови і 2 гигабайта пам 'яки.",
        "Seconds per second of speech and 2 gigabytes of memory.",
        "Зараз ввозк займає приблизно 1 гигабайт.",
    ),
    (
        "Зараз ввозк займає приблизно 1 гигабайт.",
        "Currently, ingestion takes approximately 1 gigabyte.",
        "",
    ),
    ("Застереження.", "Disclaimer.", "Тести шовна сентрична."),
    (
        "Тести шов на синтетичному голосі Пайпер.",
        "Testing speech on the Piper synthetic voice.",
        "",
    ),
    (
        "А він значно чистіший за На твоєму голосі ще не міряле.",
        "And it is significantly cleaner than on your voice; they haven't measured you yet.",  # noqa: E501
        "",
    ),
    (
        "Що пропонується писати твій голос в імплігі.",
        "What is offered to write your voice in impligy.",
        "",
    ),
    (
        "Відти в застосунку записувати сесію для аналізу і скажи...",
        "Log in to the application, record a session for analysis, and say...",
        "",
    ),
    (
        "15 -20 раз із айті -словами, як сьогодні.",
        "15-20 times with IT words, like today.",
        "Cyborg.",
    ),
    ("СК ТОП ВОЗК ВІСПР ДІПЛОЙ ПОВІЛЬНІЩЕ", "SK TOP VOZK VISPR DEPLOY SLOWLY", ""),
    (
        "Я прожену записи через нову модель і воск і покажу що краще.",
        "I will record through the new model and voice, and show what is better.",
        "",
    ),
    ("ЧУЄ саме ТЕБЕ.", "HEARS YOU.", "Дякую на саме ТЕБЕ. Дякую на ТВОЄМ ГОЛОСІЇ."),
    (
        "Дякую на Твоєму голосі результат підтвердиться вбудувати...",
        "Thank you for your voice, the result will be confirmed embedded...",
        "",
    ),
    ("Дану модель замість воск.", "This model instead of voice.", ""),
    ("Вона переверятим виспер.", "She will verify the voice.", ""),
    (
        "А він отримуватиме її запис як головний текст для перекладу.",
        "And he will receive her recording as the main text for translation.",
        "Воск тоді приберемо...",
    ),
    (
        "Воск тоді приберемо, а не триматимемо поряд.",
        "Then we will remove the voice, and not keep it nearby.",
        "",
    ),
    ("Коли запишеш фрази, скажи.", "When you write the phrases, say so.", ""),
]


# whisper hallucinations the model only transliterated: rightly rejected
LIVE_TRANSLITERATED = {"Tvers.", "Visper."}


@pytest.mark.parametrize("source, output, next_text", LIVE_2026_11_01)
def test_meta_check_passes_the_live_translations(source, output, next_text):
    expected = "transliterated" if output in LIVE_TRANSLITERATED else None
    assert reject_reason(source, output, next_text) == expected
    assert reject_reason(source, output, next_text, complete=True) == expected


@pytest.mark.parametrize(
    "output",
    [
        "I notice you provided two transcripts of the same audio.",
        "Please provide the text you want translated.",
        "The SOUND transcript seems garbled; TEXT says hello.",
        "Here is the translation: okay.",
        "Translation: okay.",
        "Okay. (Note: the recognizer misheard the second word.)",
        "It looks like a speech recognition error.",
    ],
)
def test_meta_replies_are_rejected(output):
    assert reject_reason("раз два три чотири пять шість сім", output) == "meta"


# live test 2026-11-01 (enterprise text), unit 26 (whisper-only): the second
# half of the enumeration was dropped and nothing caught it
UNIT_26_SOURCE = (
    "про стежуваність вимог контроль версії управління залежностями"
    " централізоване логування та моніторинг життєвого циклу програмного"
    " забезпечення"
)
UNIT_26_OUTPUT = "requirement tracking, version control, dependency management"


def test_dropped_half_is_too_short_only_on_the_whole_reply():
    assert reject_reason(UNIT_26_SOURCE, UNIT_26_OUTPUT, complete=True) == "too_short"
    # mid-stream the reply may still grow: no verdict yet
    assert reject_reason(UNIT_26_SOURCE, UNIT_26_OUTPUT) is None
    full = (
        "requirement tracking, version control, dependency management,"
        " centralized logging and monitoring of the software development"
        " lifecycle"
    )
    assert reject_reason(UNIT_26_SOURCE, full, complete=True) is None


@pytest.mark.parametrize(
    "source, output",
    [
        # the lowest-ratio real translations of 2026-11-01 (journal _log_unit
        # lines + session 20261101-154159): 0 false positives
        ("Звук на копий чи не поки.", "Sound on or off."),
        ("А лось заботу краще меж просто", "Well, much better than"),
        ("Що ж тут у нас є", "What do we have here"),
        (
            "К складній синтактичні конструкції аббревіатури та контекстні"
            " залежності.",
            "Complex syntactic constructions, abbreviations, and contextual"
            " dependencies.",
        ),
        # under MIN_SOURCE_WORDS: no floor
        ("Ну це вже набагато краще", "Better."),
    ],
)
def test_real_short_translations_are_not_too_short(source, output):
    assert reject_reason(source, output, complete=True) is None


def test_min_output_words_floor():
    assert min_output_words("раз два три чотири п'ять") == 0  # 5 words
    assert min_output_words("раз два три чотири п'ять шість") == 3
    assert min_output_words("раз - два, три — чотири п'ять шість сім") == 4
    assert (
        reject_reason("раз два три чотири п'ять шість", "One two.", complete=True)
        == "too_short"
    )


# live 2026-11-01 18:07-18:12 (TEXT, SOUND, reply): context poisoning - the
# model transliterated every unit
POISONED = [
    ("Моно жабра аналогове.", "мону жабра налогове", "Monu."),
    ("Дата Телеграм.", "де ти тіль гра", "De ty tyl gra."),
    ("Раз, два, три.", "раз два три", "Raz dva tri."),
    ("Раз.", "ра", "Ra."),
    ("Один.", "один", "Odyin."),
    ("Щас.", "стя", "Stia."),
    ("Так.", "тя", "Tia."),
    ("Господин Аллахай, штука.", "гоподи лагаєще штук", "Hopody."),
    (
        "Системний вивід за замовчення.",
        "цес темний вивід за замовченн",
        "Ces temny vyvid za zamovchen.",
    ),
    (
        "Також просимо вас активувати логи.",
        "також просуємо вас актививати лой",
        "Takozh prosymo vas aktivuvaty logy.",
    ),
    (
        "Все мати прямий зв 'язок із спеціалістом",
        "щоб мати прямий зв’язок і спеціалістом",
        "Shchob maty pryamy zvyazok i speetsialistom",
    ),
    ("Раз, один, два, три.", "аз один два три", "Az odyn dva try."),
]


@pytest.mark.parametrize("text, sound, output", POISONED)
def test_transliterated_replies_are_rejected(text, sound, output):
    assert reject_reason(text, output, sound=sound) == "transliterated"


def test_sound_is_needed_where_text_differs():
    text, sound, output = POISONED[1]  # TEXT «Дата Телеграм», SOUND «де ти тіль гра»
    assert reject_reason(text, output) is None
    assert reject_reason(text, output, sound=sound) == "transliterated"


@pytest.mark.parametrize(
    "text, sound, output",
    [
        # English cognates, names, terms, acronyms, numbers (live 2026-11-01)
        ("Модель.", "", "Model."),
        ("ентерпрайз архітектур.", "ентерпраз архітект ч", "Enterprise architecture"),
        ("Девопс.", "де в опс", "DevOps."),
        ("Телеграм.", "це лягра", "It's Telegram."),
        ("Телеграм.", "телеграм", "Telegram."),
        ("Кубернетіс.", "кубернетіс", "Kubernetes."),
        ("Хе", "аі", "AI"),
        ("Раз, два, три.", "раз два три", "One, two, three."),
        ("15 -20 раз із айті -словами.", "", "15-20 times with IT words."),
        ("Тікаво У нас виходить.", "цікаво в нас виходи", "Interesting, it works."),
        # a transliterated name in a translated sentence
        ("Оля сказала привіт.", "оля сказала привіт", "Olya said hello."),
        # the glossary (``terms``) is English by definition
        ("Сайборг.", "сайборг", "Cyborg."),
    ],
)
def test_translations_with_cognates_and_names_pass(text, sound, output):
    assert reject_reason(text, output, sound=sound, terms=("Cyborg",)) is None


def test_missing_word_list_is_an_explicit_error(monkeypatch):
    english_words.cache_clear()
    monkeypatch.delenv(EN_DICT_ENV)
    try:
        with pytest.raises(RuntimeError, match=EN_DICT_ENV):
            english_words()
    finally:
        english_words.cache_clear()
