"""Per-service settings tabs: ASR, TTS, LLM.

Two kinds of settings, two owners:
- per service node (model incl. download, beam size, VAD, default voice, ...):
  read and changed on the chosen node through its runtime ``GET|POST /config``
  (persisted by the service in RT_CONFIG_STATE, shared by every client);
- per user (whisper prompt, TTS voice/speed, LLM prompt/template/params):
  ``services.{asr,tts,llm}.*`` in the edge config.yml, sent with each request.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.prompt import (
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_TEMPLATE,
    asr_prompt_errors,
    build_messages,
    render_asr_prompt,
    split_words,
    template_errors,
)
from ...translation_system import PREVIOUS_WORDS_MAX
from ...core.runtime import EdgeSettings

PREVIEW_SAMPLE = (
    "Dzień dobry wszystkim. Dzisiaj omówimy wdrożenie klastra NixOS i"
    " konfigurację usług rozpoznawania mowy na każdym węźle."
)

# recognized words of the stream so far, for the Whisper prompt preview
PREVIEW_PREVIOUS = (
    "Добрий день усім. Сьогодні говоримо про розгортання кластера NixOS"
    " і налаштування сервісів розпізнавання мовлення"
)

# A model switch reloads the service and may download the model first.
APPLY_TIMEOUT_S = 3600.0


class Background(QObject):
    """Run ``fn`` off the GUI thread; ``cb(ok, result_or_exc)`` on the GUI thread."""

    _done = Signal(object, object, object)

    def __init__(self, parent=None, sync: bool = False):
        super().__init__(parent)
        self.sync = sync  # tests: run inline
        self._done.connect(lambda cb, ok, r: cb(ok, r))

    def run(self, fn: Callable[[], Any], cb: Callable[[bool, Any], None]) -> None:
        def work():
            try:
                ok, result = True, fn()
            except Exception as e:  # reported to the user via cb
                ok, result = False, e
            if self.sync:
                cb(ok, result)
            else:
                self._done.emit(cb, ok, result)

        if self.sync:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()


def _size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


class _Editor:
    """One runtime config field: widget + typed read-back."""

    def __init__(self, name: str, value: Any, models: Optional[dict] = None):
        self.name, self.value = name, value
        if name == "model" and models is not None:
            box = QComboBox()
            box.setEditable(True)  # absolute model directory also allowed
            for m in models.get("models", []):
                state = "кеш" if m["cached"] else f"завантажити ~{_size(m['size'])}"
                box.addItem(f"{m['id']} ({state})", m["id"])
            idx = box.findData(value)
            if idx < 0:
                box.addItem(str(value), value)
                idx = box.count() - 1
            box.setCurrentIndex(idx)
            self.widget: QWidget = box
        elif isinstance(value, bool):
            self.widget = QCheckBox()
            self.widget.setChecked(value)
        elif isinstance(value, int):
            self.widget = QSpinBox()
            self.widget.setRange(0, 10_000_000)
            self.widget.setValue(value)
        elif isinstance(value, float):
            self.widget = QDoubleSpinBox()
            self.widget.setRange(0.0, 1_000_000.0)
            self.widget.setDecimals(3)
            self.widget.setValue(value)
        elif isinstance(value, (list, tuple)):
            self.widget = QLineEdit(",".join(str(v) for v in value))
        else:
            self.widget = QLineEdit("" if value is None else str(value))

    def read(self) -> Any:
        w, v = self.widget, self.value
        if isinstance(w, QComboBox):
            text = w.currentText().strip()
            if w.itemText(w.currentIndex()) == text and w.currentData() is not None:
                return w.currentData()
            return text
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            return w.value()
        text = w.text().strip()
        if isinstance(v, (list, tuple)):
            return [t.strip() for t in text.split(",") if t.strip()]
        if v is None and not text:
            return None
        return text


class ServiceConfigPanel(QGroupBox):
    """Runtime config of one rt-asr / rt-tts node (``GET|POST /config``)."""

    loaded = Signal(object)  # the chosen endpoint, after a successful load

    def __init__(
        self,
        kind: str,
        endpoints: List[Any],
        client_factory: Callable[[Any], Any],
        bg: Background,
        parent=None,
    ):
        super().__init__("Налаштування сервісу на вузлі (для всіх клієнтів)", parent)
        self.kind = kind
        self.endpoints = list(endpoints)
        self._factory = client_factory
        self._bg = bg
        self.config: Optional[dict] = None
        self.editors: Dict[str, _Editor] = {}
        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.node = QComboBox()
        for e in self.endpoints:
            label = f"{e.node} — {e.url}" + (" (локальний)" if e.local else "")
            self.node.addItem(label)
        bar.addWidget(QLabel("Вузол:"))
        bar.addWidget(self.node, 1)
        self.load_button = QPushButton("Завантажити")
        self.load_button.clicked.connect(self.load)
        bar.addWidget(self.load_button)
        outer.addLayout(bar)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        outer.addWidget(self.form_host)
        self.apply_button = QPushButton("Застосувати на сервісі")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        outer.addWidget(self.apply_button)
        self.status = QLabel(
            "" if self.endpoints else "сервісів цього типу не знайдено"
        )
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.load_button.setEnabled(bool(self.endpoints))
        self.node.currentIndexChanged.connect(lambda _i: self._clear())

    def endpoint(self):
        i = self.node.currentIndex()
        return self.endpoints[i] if 0 <= i < len(self.endpoints) else None

    def client(self):
        return self._factory(self.endpoint())

    def _clear(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.editors, self.config = {}, None
        self.apply_button.setEnabled(False)

    def load(self) -> None:
        if self.endpoint() is None:
            return
        self.status.setText("завантаження…")
        client = self.client()

        def fetch():
            try:
                return client.get_config(), client.models()
            finally:
                client.close()

        self._bg.run(fetch, self._loaded)

    def _loaded(self, ok: bool, result: Any) -> None:
        if not ok:
            self.status.setText(f"помилка: {result}")
            return
        config, models = result
        self._clear()
        self.config = config
        for name, value in sorted(config.get("values", {}).items()):
            editor = _Editor(name, value, models if name == "model" else None)
            self.editors[name] = editor
            source = config.get("source", {}).get(name)
            label = f"{name}{' *' if source == 'override' else ''}:"
            self.form.addRow(label, editor.widget)
        self.apply_button.setEnabled(True)
        self.status.setText(
            f"версія {config.get('version')}; * = змінено на вузлі (не з env)"
        )
        self.loaded.emit(self.endpoint())

    def changes(self) -> Dict[str, Any]:
        out = {}
        for name, editor in self.editors.items():
            value = editor.read()
            old = editor.value
            if isinstance(old, tuple):
                old = list(old)
            if value != old:
                out[name] = value
        return out

    def apply(self) -> None:
        if self.config is None:
            return
        changes = self.changes()
        if not changes:
            self.status.setText("без змін")
            return
        version = int(self.config.get("version", 0)) + 1
        self.status.setText(
            "застосовую… (зміна моделі перезавантажує сервіс і може"
            " завантажувати модель)"
        )
        self.apply_button.setEnabled(False)
        client = self.client()

        def post():
            try:
                return client.update_config(changes, version, timeout_s=APPLY_TIMEOUT_S)
            finally:
                client.close()

        def done(ok: bool, result: Any) -> None:
            self.apply_button.setEnabled(True)
            if not ok:
                self.status.setText(f"не застосовано: {result}")
                return
            reloaded = " (модель перезавантажено)" if result.get("reloaded") else ""
            self.load()
            self.status.setText(f"застосовано{reloaded}: {', '.join(changes)}")

        self._bg.run(post, done)


class AsrTab(QWidget):
    """Whisper prompt (per user) + rt-asr node config (model, beams, VAD...)."""

    def __init__(
        self, settings: EdgeSettings, endpoints, client_factory, bg, parent=None
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        user = QGroupBox("Розпізнавання (мої налаштування)")
        form = QFormLayout(user)
        self.prompt = QPlainTextEdit(settings.asr.prompt)
        self.prompt.setPlaceholderText(
            "Whisper prompt: словник, імена, терміни, стиль. Порожньо = без prompt"
        )
        self.prompt.setFixedHeight(80)
        self.previous_words = QSpinBox()
        self.previous_words.setRange(0, PREVIOUS_WORDS_MAX)
        self.previous_words.setValue(settings.asr.previous_words)
        self.previous_words.setToolTip(
            "Скільки останніх розпізнаних слів підставляється в {{previous_text}}"
            " (0 = жодних); без змінної в шаблоні нічого не додається"
        )
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(80)
        form.addRow("Шаблон prompt:", self.prompt)
        form.addRow(
            "",
            QLabel(
                "Змінні (необов'язкові): {{previous_text}} - попередні слова,"
                " {{source_lang}}"
            ),
        )
        form.addRow("Попередніх слів:", self.previous_words)
        form.addRow("Попередній перегляд:", self.preview)
        self._source_lang = settings.source_language
        self.prompt.textChanged.connect(self.update_preview)
        self.previous_words.valueChanged.connect(self.update_preview)
        self.update_preview()
        layout.addWidget(user)
        self.service = ServiceConfigPanel("asr", endpoints, client_factory, bg)
        layout.addWidget(self.service)
        layout.addStretch()

    def update_preview(self) -> None:
        template = self.prompt.toPlainText().strip()
        errors = asr_prompt_errors("Шаблон prompt", template)
        if errors:
            self.preview.setPlainText("\n".join(errors))
            return
        lang = None if self._source_lang == "auto" else self._source_lang
        prompt = render_asr_prompt(
            template, PREVIEW_PREVIOUS.split(), self.previous_words.value(), lang
        )
        self.preview.setPlainText(prompt or "(без prompt)")

    def values(self) -> Dict[str, Any]:
        return {
            "services.asr.prompt": self.prompt.toPlainText().strip(),
            "services.asr.previous_words": self.previous_words.value(),
        }


class TtsTab(QWidget):
    """Voice/speed (per user, from GET /v1/audio/voices) + rt-tts node config."""

    def __init__(
        self, settings: EdgeSettings, endpoints, client_factory, bg, parent=None
    ):
        super().__init__(parent)
        self._factory = client_factory
        self._bg = bg
        layout = QVBoxLayout(self)
        user = QGroupBox("Озвучка (мої налаштування)")
        form = QFormLayout(user)
        self.voice = QComboBox()
        self.voice.addItem("(типовий голос сервісу)", None)
        if settings.tts.voice:
            self.voice.addItem(settings.tts.voice, settings.tts.voice)
            self.voice.setCurrentIndex(1)
        self.voices_button = QPushButton("Список голосів")
        self.voices_button.setEnabled(bool(endpoints))
        row = QHBoxLayout()
        row.addWidget(self.voice, 1)
        row.addWidget(self.voices_button)
        form.addRow("Голос:", row)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.25, 4.0)
        self.speed.setSingleStep(0.05)
        self.speed.setValue(settings.tts.speed or 1.0)
        self.speed_default = QCheckBox("типова швидкість сервісу")
        self.speed_default.setChecked(settings.tts.speed is None)
        self.speed.setEnabled(settings.tts.speed is not None)
        self.speed_default.toggled.connect(lambda on: self.speed.setEnabled(not on))
        form.addRow("Швидкість:", self.speed)
        form.addRow("", self.speed_default)
        self.voice_status = QLabel("")
        form.addRow("", self.voice_status)
        layout.addWidget(user)
        self.service = ServiceConfigPanel("tts", endpoints, client_factory, bg)
        layout.addWidget(self.service)
        layout.addStretch()
        self.voices_button.clicked.connect(self.load_voices)
        self.service.loaded.connect(lambda _e: self.load_voices())

    def load_voices(self) -> None:
        endpoint = self.service.endpoint()
        if endpoint is None:
            return
        client = self._factory(endpoint)

        def fetch():
            try:
                return client.voices()
            finally:
                client.close()

        self._bg.run(fetch, self._voices_loaded)

    def _voices_loaded(self, ok: bool, result: Any) -> None:
        if not ok:
            self.voice_status.setText(f"голоси недоступні: {result}")
            return
        current = self.voice.currentData()
        self.voice.clear()
        self.voice.addItem(f"(типовий голос сервісу: {result.get('default')})", None)
        for v in result.get("voices", []):
            self.voice.addItem(
                f"{v['id']} ({v.get('language')}, {v.get('gender')})", v["id"]
            )
        idx = self.voice.findData(current)
        if current is not None and idx < 0:
            self.voice.addItem(f"{current} (немає на цьому вузлі)", current)
            idx = self.voice.count() - 1
        self.voice.setCurrentIndex(max(idx, 0))
        self.voice_status.setText(f"{len(result.get('voices', []))} голосів")

    def values(self) -> Dict[str, Any]:
        return {
            "services.tts.voice": self.voice.currentData(),
            "services.tts.speed": (
                None if self.speed_default.isChecked() else self.speed.value()
            ),
        }


class LlmTab(QWidget):
    """Translation prompt/template and request parameters (per user)."""

    def __init__(
        self,
        settings: EdgeSettings,
        services=(),
        endpoints=(),
        client_factory: Optional[Callable[[Any], Any]] = None,
        bg: Optional[Background] = None,
        parent=None,
    ):
        super().__init__(parent)
        llm = settings.llm
        form = QFormLayout(self)
        self.endpoints = list(endpoints)
        self._factory = client_factory
        self._bg = bg or Background(self)
        # which worker translates: auto (local first, failover) or one node,
        # local or remote alike (services.use.llm)
        self.use = QComboBox()
        self.use.addItem("auto (локальний першим, failover)", None)
        for e in self.endpoints:
            label = f"{e.node} — {e.url}" + (" (локальний)" if e.local else "")
            self.use.addItem(label, e.node)
        pinned = settings.use.llm
        if pinned is not None:
            if self.use.findData(pinned) < 0:
                self.use.addItem(f"{pinned} (недоступний)", pinned)
            self.use.setCurrentIndex(self.use.findData(pinned))
        self.models_button = QPushButton("Моделі вузла")
        self.models_button.setEnabled(
            bool(self.endpoints) and client_factory is not None
        )
        self.models_button.clicked.connect(self.load_models)
        self.models_status = QLabel("")
        self.models_status.setWordWrap(True)
        self.model = QComboBox()
        self.model.setEditable(True)
        names = [m for e in services if e.kind == "llm" for m in e.models]
        for name in dict.fromkeys(["local", *names, llm.model]):
            self.model.addItem(name)
        self.model.setCurrentText(llm.model)
        self.system_prompt = QPlainTextEdit(llm.system_prompt)
        self.system_prompt.setFixedHeight(110)
        self.user_template = QPlainTextEdit(llm.user_template)
        self.user_template.setFixedHeight(60)
        reset = QPushButton("Типові prompt і шаблон")
        reset.clicked.connect(self.reset_prompts)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.05)
        self.temperature.setValue(llm.temperature)
        self.max_tokens_cap = QSpinBox()
        self.max_tokens_cap.setRange(16, 8192)
        self.max_tokens_cap.setValue(llm.max_tokens_cap)
        self.context_pairs = QSpinBox()
        self.context_pairs.setRange(0, 20)
        self.context_pairs.setValue(llm.context_pairs)
        self.words_per_request = QSpinBox()
        self.words_per_request.setRange(1, 2000)
        self.words_per_request.setValue(llm.words_per_request)
        self.words_per_request.setToolTip(
            "Скільки слів розпізнаного тексту йде в один запит до LLM; довший"
            " текст ділиться (по кінцях речень, якщо вміщаються) і кожна частина"
            " підставляється в шаблон замість {{text}}"
        )
        self.notice = QLabel("\n".join(settings.notices))
        self.notice.setStyleSheet("color: #b36b00;")
        self.notice.setWordWrap(True)
        self.notice.setVisible(bool(settings.notices))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(150)
        form.addRow(self.notice)
        form.addRow("Воркер (вузол):", self.use)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.models_button)
        form.addRow("Модель:", model_row)
        form.addRow("", self.models_status)
        form.addRow("System prompt:", self.system_prompt)
        form.addRow("Шаблон запиту:", self.user_template)
        form.addRow(
            "",
            QLabel(
                "Змінні: {{text}} - сюди вставляються слова (обов'язкова),"
                " {{source_lang}}, {{target_lang}}"
            ),
        )
        form.addRow("", reset)
        form.addRow("Слів на запит:", self.words_per_request)
        form.addRow("Temperature:", self.temperature)
        form.addRow("Max tokens:", self.max_tokens_cap)
        form.addRow("Контекст (пар фраз):", self.context_pairs)
        form.addRow("Попередній перегляд:", self.preview)
        for w in (self.system_prompt, self.user_template):
            w.textChanged.connect(self.update_preview)
        self.words_per_request.valueChanged.connect(self.update_preview)
        self.update_preview()
        if "llm.model" in settings.managed:
            self.model.setEnabled(False)
            self.model.setToolTip("Задано через RT_EDGE_* (лише читання)")

    def model_endpoint(self):
        """Chosen worker, or the first one (auto order) for the model list."""
        node = self.use.currentData()
        for e in self.endpoints:
            if node is None or e.node == node:
                return e
        return None

    def load_models(self) -> None:
        """GET /v1/models of the chosen worker (its discovered token)."""
        endpoint = self.model_endpoint()
        if endpoint is None or self._factory is None:
            self.models_status.setText("воркер недоступний")
            return
        client = self._factory(endpoint)
        self.models_status.setText(f"запит моделей у {endpoint.node}…")

        def fetch():
            try:
                return client.models()
            finally:
                client.close()

        def done(ok: bool, result: Any) -> None:
            if not ok:
                self.models_status.setText(f"{endpoint.node}: {result}")
                return
            items = result.get("data") or result.get("models") or []
            ids = [str(m.get("id")) for m in items if m.get("id")]
            current = self.model.currentText()
            self.model.clear()
            for name in dict.fromkeys(["local", *ids, current]):
                self.model.addItem(name)
            self.model.setCurrentText(current)
            self.models_status.setText(f"{endpoint.node}: {', '.join(ids) or '-'}")

        self._bg.run(fetch, done)

    def update_preview(self) -> None:
        """Rendered requests for PREVIEW_SAMPLE (or the template errors)."""
        system = self.system_prompt.toPlainText().strip()
        template = self.user_template.toPlainText().strip()
        errors = template_errors("System prompt", system, False) + template_errors(
            "Шаблон", template, True
        )
        if errors:
            self.preview.setPlainText("\n".join(errors))
            return
        chunks = split_words(PREVIEW_SAMPLE, self.words_per_request.value())
        lines = [f"[system] {build_messages(system, template, '', 'pl')[0]['content']}"]
        for n, chunk in enumerate(chunks, 1):
            user = build_messages(system, template, chunk, "pl")[-1]["content"]
            lines.append(f"[запит {n}/{len(chunks)}] {user}")
        self.preview.setPlainText("\n".join(lines))

    def reset_prompts(self) -> None:
        self.system_prompt.setPlainText(DEFAULT_SYSTEM_PROMPT)
        self.user_template.setPlainText(DEFAULT_USER_TEMPLATE)

    def values(self) -> Dict[str, Any]:
        return {
            "services.use.llm": self.use.currentData(),
            "services.llm.model": self.model.currentText().strip(),
            "services.llm.system_prompt": self.system_prompt.toPlainText().strip(),
            "services.llm.user_template": self.user_template.toPlainText().strip(),
            "services.llm.temperature": self.temperature.value(),
            "services.llm.max_tokens_cap": self.max_tokens_cap.value(),
            "services.llm.context_pairs": self.context_pairs.value(),
            "services.llm.words_per_request": self.words_per_request.value(),
        }
