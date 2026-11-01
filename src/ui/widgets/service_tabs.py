"""Per-service settings tabs: ASR, Vosk, TTS, LLM.

Two kinds of settings, two owners:
- per service node (model incl. download, beam size, VAD, default voice, ...):
  read and changed on the chosen node through its runtime ``GET|POST /config``
  (persisted by the service in RT_CONFIG_STATE, shared by every client);
- per user (whisper prompt, TTS voice/speed, LLM prompt/template/params):
  ``services.{asr,tts,llm}.*`` in the edge config.yml, sent with each request.
"""

from __future__ import annotations

import threading
import time
from html import escape
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.prompt import (
    DEFAULT_CHECK_SYSTEM_PROMPT,
    DEFAULT_CHECK_USER_TEMPLATE,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TERMS,
    DEFAULT_USER_TEMPLATE,
    asr_prompt_errors,
    build_messages,
    check_template_errors,
    render_asr_prompt,
    parse_terms,
    split_words,
    system_prompt_errors,
    template_errors,
)
from ...clients.llm_worker import WorkerModel
from ...clients.model_status import ModelStatus, parse_status
from .model_state import (
    ERROR_STYLE,
    WARN_STYLE,
    format_dropped,
    format_memory,
    format_model_html,
)
from ...core.runtime import OFF, EdgeSettings

PREVIEW_SAMPLE = (
    "Dzień dobry wszystkim. Dzisiaj omówimy wdrożenie klastra NixOS i"
    " konfigurację usług rozpoznawania mowy na każdym węźle."
)
# the same audio as Vosk would hear it (no punctuation, misspellings)
PREVIEW_CHECK = (
    "dzień dobry wszystkim dzisiaj omówimy wdrożenie klastra nix os i"
    " konfiguracje usług rozpoznawania mowy na każdym węźle"
)


# LLM prompt variables (core/prompt.py PLACEHOLDERS / CHECK_PLACEHOLDERS)
PROMPT_VARIABLES_HELP = (
    "Змінні prompt і шаблону:\n"
    "  {{text}} - текст whisper (обов'язкова в шаблоні запиту)\n"
    "  {{vosk_text}} - текст Vosk того ж аудіо (лише зі звіркою; обов'язкова"
    " в її шаблоні)\n"
    "  {{source_lang}} - мова мовлення (Polish, Ukrainian, ...)\n"
    "  {{target_lang}} - мова перекладу (English)\n"
    "  {{terms}} - речення зі «Словника термінів» (лише в system prompt;"
    " порожній словник - нічого)"
)


def active_prompts_text(vosk_use: Optional[str]) -> str:
    """Which prompt pair translates now (services.use.vosk; the runtime
    Vosk state is in the status panel row «Звірка (rt-vosk)»)."""
    if vosk_use == OFF:
        return "Зараз активні: «Без Vosk» (звірку Vosk вимкнено в «Сервіси»)."
    return (
        "Зараз активні: «Зі звіркою Vosk» - для кожної фрази, яку Vosk"
        " розпізнав; коли Vosk недоступний (див. «Звірка (rt-vosk)» на"
        " головному вікні) - «Без Vosk»."
    )


# A model switch reloads the service and may download the model first.
APPLY_TIMEOUT_S = 3600.0
# POST /model/load|unload only start the transition; progress is polled.
MODEL_ACTION_TIMEOUT_S = 60.0
# /status polling while a model downloads/loads or a request is in flight
STATE_POLL_MS = 1000


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
    """One runtime config field: a control derived from the service's field
    schema (GET /config ``schema``) + typed read-back. Free text only for
    ``text`` fields (or a service that publishes no schema)."""

    def __init__(
        self,
        name: str,
        value: Any,
        meta: Optional[dict] = None,
        models: Optional[dict] = None,
    ):
        self.name, self.value = name, value
        self.meta = meta or {}
        kind = self.meta.get("type") or _type_of(value)
        self.kind = kind
        if kind == "model":
            box = QComboBox()
            listed = {m["id"]: m for m in (models or {}).get("models", [])}
            for mid in dict.fromkeys([*listed, *self.meta.get("choices", ())]):
                m = listed.get(mid)
                if m is None:
                    label = mid
                else:
                    lang = f" [{m['language']}]" if m.get("language") else ""
                    if m["cached"]:
                        label = f"{mid}{lang} (кеш)"
                    else:
                        label = f"{mid}{lang} (завантажити ~{_size(m['size'])})"
                if m and m.get("warning"):
                    label += f" ⚠ {m['warning']}"
                box.addItem(label, mid)
            self._select(box, value)
            self.widget: QWidget = box
        elif kind == "choice":
            box = QComboBox()
            for c in self.meta.get("choices", ()):
                box.addItem(c if c != "" else "auto / не задано", c)
            self._select(box, "" if value is None else value)
            self.widget = box
        elif kind == "multichoice":
            lst = QListWidget()
            lst.setFixedHeight(110)
            chosen = set(value or ())
            for c in self.meta.get("choices", ()):
                item = QListWidgetItem(c)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if c in chosen else Qt.CheckState.Unchecked
                )
                lst.addItem(item)
            self.widget = lst
        elif kind == "bool":
            self.widget = QCheckBox()
            self.widget.setChecked(bool(value))
        elif kind == "int":
            self.widget = QSpinBox()
            self.widget.setRange(
                int(self.meta.get("min", 0)), int(self.meta.get("max", 10_000_000))
            )
            self.widget.setValue(int(value))
        elif kind == "float":
            self.widget = QDoubleSpinBox()
            self.widget.setDecimals(2)
            self.widget.setRange(
                float(self.meta.get("min", 0.0)),
                float(self.meta.get("max", 1_000_000.0)),
            )
            self.widget.setSingleStep(0.05 if self.meta.get("max", 10) <= 10 else 1.0)
            self.widget.setValue(float(value))
        else:
            self.widget = QLineEdit(
                ",".join(map(str, value))
                if isinstance(value, (list, tuple))
                else ("" if value is None else str(value))
            )
        if self.meta.get("description"):
            self.widget.setToolTip(self.meta["description"])

    @staticmethod
    def _select(box: QComboBox, value: Any) -> None:
        idx = box.findData(value)
        if idx < 0:  # current value outside the published choices: keep it
            box.addItem(f"{value} (поточне)", value)
            idx = box.count() - 1
        box.setCurrentIndex(idx)

    def read(self) -> Any:
        w, v = self.widget, self.value
        if isinstance(w, QComboBox):
            data = w.currentData()
            return None if data == "" and self.meta.get("nullable") else data
        if isinstance(w, QListWidget):
            return [
                w.item(i).text()
                for i in range(w.count())
                if w.item(i).checkState() == Qt.CheckState.Checked
            ]
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


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "text"


class ServiceConfigPanel(QGroupBox):
    """One rt-asr / rt-tts node: its ACTUAL model (state, download progress,
    error, memory) with explicit load/unload, and its runtime config
    (``GET|POST /config``; a model change applies only via «Застосувати»)."""

    loaded = Signal(object)  # the chosen endpoint, after a successful load
    state_changed = Signal()  # config, /v1/models or /status re-read

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
        self.models: Optional[dict] = None  # GET /v1/models of the node
        self.editors: Dict[str, _Editor] = {}
        self.model_status: Optional[ModelStatus] = None
        self._in_flight: Optional[str] = None  # load | unload | apply
        self._polling = False  # a /status request is out
        outer = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.node = QComboBox()
        for e in self.endpoints:
            label = f"{e.node} — {e.url}" + (" (локальний)" if e.local else "")
            self.node.addItem(label)
        bar.addWidget(QLabel("Вузол:"))
        bar.addWidget(self.node, 1)
        self.load_button = QPushButton("Оновити")
        self.load_button.setToolTip("Прочитати стан моделі і налаштування вузла")
        self.load_button.clicked.connect(self.load)
        bar.addWidget(self.load_button)
        outer.addLayout(bar)
        self.model_state = QLabel("модель: стан невідомий (натисніть «Оновити»)")
        self.model_state.setTextFormat(Qt.TextFormat.RichText)
        self.model_state.setWordWrap(True)
        outer.addWidget(self.model_state)
        self.memory = QLabel("")
        outer.addWidget(self.memory)
        actions = QHBoxLayout()
        self.load_model_button = QPushButton("Завантажити модель")
        self.load_model_button.setToolTip(
            "Завантажити в пам'ять модель з налаштувань вузла"
        )
        self.load_model_button.clicked.connect(self.load_model)
        self.unload_model_button = QPushButton("Вивантажити модель")
        self.unload_model_button.setToolTip("Звільнити RAM/VRAM, які тримає модель")
        self.unload_model_button.clicked.connect(self.unload_model)
        actions.addWidget(self.load_model_button)
        actions.addWidget(self.unload_model_button)
        actions.addStretch()
        outer.addLayout(actions)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        outer.addWidget(self.form_host)
        self.apply_button = QPushButton("Застосувати на сервісі")
        self.apply_button.setToolTip(
            "Зміна моделі: вивантаження поточної, завантаження нової"
        )
        self.apply_button.clicked.connect(lambda: self.apply())
        outer.addWidget(self.apply_button)
        self.status = QLabel(
            "" if self.endpoints else "сервісів цього типу не знайдено"
        )
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(STATE_POLL_MS)
        self.poll_timer.timeout.connect(self.refresh_state)
        self.node.currentIndexChanged.connect(lambda _i: self._clear())
        self._update_buttons()

    def endpoint(self):
        i = self.node.currentIndex()
        return self.endpoints[i] if 0 <= i < len(self.endpoints) else None

    def client(self):
        return self._factory(self.endpoint())

    def _clear(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.editors, self.config, self.models = {}, None, None
        self.model_status = None
        self.model_state.setText("модель: стан невідомий (натисніть «Оновити»)")
        self.memory.setText("")
        self._update_buttons()

    # -- actual model: state, memory, load / unload -------------------------

    def configured_model(self) -> Optional[str]:
        values = (self.config or {}).get("values") or {}
        model = values.get("model")
        return str(model) if model else None

    def busy(self) -> bool:
        """A request is in flight or the model is downloading/loading."""
        return self._in_flight is not None or bool(
            self.model_status and self.model_status.transitional
        )

    def _update_buttons(self) -> None:
        busy = self.busy()
        state = self.model_status.state if self.model_status else None
        self.load_model_button.setEnabled(not busy and state in ("unloaded", "error"))
        self.unload_model_button.setEnabled(not busy and state in ("ready", "error"))
        self.apply_button.setEnabled(not busy and self.config is not None)
        self.load_button.setEnabled(bool(self.endpoints) and self._in_flight is None)
        if busy and not self.poll_timer.isActive():
            self.poll_timer.start()
        elif not busy:
            self.poll_timer.stop()

    def refresh_state(self) -> None:
        """GET /status of the chosen node (also the poll timer's tick)."""
        endpoint = self.endpoint()
        if endpoint is None or self._polling:
            return
        self._polling = True
        client = self._factory(endpoint)

        def fetch():
            try:
                return client.get_status()
            finally:
                client.close()

        def done(ok: bool, result: Any) -> None:
            self._polling = False
            if endpoint is not self.endpoint():
                return  # node switched meanwhile
            self._state_loaded(ok, result)

        self._bg.run(fetch, done)

    def _state_loaded(self, ok: bool, result: Any) -> None:
        if not ok:
            self.model_status = None
            self.model_state.setText(
                f'<span style="{ERROR_STYLE}">'
                + escape(f"стан моделі недоступний: {result}")
                + "</span>"
            )
            self.memory.setText("")
        else:
            self.model_status = parse_status(result)
            self.model_state.setText(
                format_model_html(self.model_status, self.configured_model())
            )
            texts = (
                format_memory(self.model_status),
                format_dropped(self.model_status),
            )
            self.memory.setText("; ".join(t for t in texts if t))
        self._update_buttons()
        self.state_changed.emit()

    def _model_action(self, action: str) -> None:
        endpoint = self.endpoint()
        if endpoint is None or self.busy():
            return
        client = self._factory(endpoint)
        call = client.load_model if action == "load" else client.unload_model
        self._in_flight = action
        self.status.setText(
            "завантажую модель…" if action == "load" else "вивантажую модель…"
        )
        self._update_buttons()

        def post():
            try:
                return call(timeout_s=MODEL_ACTION_TIMEOUT_S)
            finally:
                client.close()

        def done(ok: bool, result: Any) -> None:
            self._in_flight = None
            if ok:
                self.status.setText(
                    "запит на завантаження моделі прийнято"
                    if action == "load"
                    else "модель вивантажено"
                )
            else:
                what = "завантажити" if action == "load" else "вивантажити"
                self.status.setText(f"не вдалося {what} модель: {result}")
            self._update_buttons()
            self.refresh_state()

        self._bg.run(post, done)

    def load_model(self) -> None:
        """POST /model/load: the configured model; progress via polling."""
        self._model_action("load")

    def unload_model(self) -> None:
        """POST /model/unload: free RAM/VRAM of this node's model."""
        self._model_action("unload")

    # -- runtime config ------------------------------------------------------

    def load(self) -> None:
        if self.endpoint() is None:
            return
        self.status.setText("читаю налаштування…")
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
        self.config, self.models = config, models
        self.refresh_state()
        schema = config.get("schema")
        for name, value in sorted(config.get("values", {}).items()):
            meta = (schema or {}).get(name)
            if meta is None and name == "model":
                meta = {"type": "model"}
            editor = _Editor(name, value, meta, models if name == "model" else None)
            self.editors[name] = editor
            source = config.get("source", {}).get(name)
            label = f"{name}{' *' if source == 'override' else ''}:"
            self.form.addRow(label, editor.widget)
        self._update_buttons()
        note = (
            ""
            if schema
            else "; сервіс не публікує схему полів (стара версія) - частина полів"
            " як текст, оновіть сервіс"
        )
        self.status.setText(
            f"версія {config.get('version')}; * = змінено на вузлі (не з env){note}"
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

    def apply(self, changes: Optional[Dict[str, Any]] = None) -> None:
        """POST /config with the edited fields (or exactly ``changes``)."""
        if self.config is None:
            return
        changes = self.changes() if changes is None else changes
        if not changes:
            self.status.setText("без змін")
            return
        version = int(self.config.get("version", 0)) + 1
        self.status.setText(
            "застосовую… (зміна моделі: вивантаження поточної, завантаження"
            " нової, можливо з мережі)"
        )
        self._in_flight = "apply"
        self._update_buttons()  # polls /status: download progress meanwhile
        client = self.client()

        def post():
            try:
                return client.update_config(changes, version, timeout_s=APPLY_TIMEOUT_S)
            finally:
                client.close()

        def done(ok: bool, result: Any) -> None:
            self._in_flight = None
            self._update_buttons()
            if not ok:
                self.status.setText(f"не застосовано: {result}")
                self.refresh_state()
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
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(80)
        form.addRow("Шаблон prompt:", self.prompt)
        form.addRow(
            "",
            QLabel(
                "Змінна (необов'язкова): {{source_lang}}. Уже розпізнаний текст"
                " rt-asr сам додає до prompt"
            ),
        )
        form.addRow("Попередній перегляд:", self.preview)
        self._source_lang = settings.source_language
        self.prompt.textChanged.connect(self.update_preview)
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
        prompt = render_asr_prompt(template, lang)
        self.preview.setPlainText(
            f"{prompt or '(без prompt)'}\n[+ уже розпізнаний текст стріму]"
        )

    def values(self) -> Dict[str, Any]:
        return {
            "services.asr.prompt": self.prompt.toPlainText().strip(),
        }


class VoskTab(QWidget):
    """rt-vosk node: its ONE model (language), load/unload, runtime config
    and, when the session language differs from the model's, an explicit
    one-click switch. Which instance cross-checks whisper (or none):
    «Сервіси» / status panel."""

    def __init__(
        self, settings: EdgeSettings, endpoints, client_factory, bg, parent=None
    ):
        super().__init__(parent)
        self.source_language = settings.source_language
        layout = QVBoxLayout(self)
        note = QLabel(
            "Vosk розпізнає той самий звук, що й whisper, і не вигадує слів:"
            " LLM перекладає лише те, що чув Vosk (промпт звірки - вкладка"
            " «Переклад (LLM)»); сегмент без жодного слова у Vosk не"
            " перекладається. На вузлі одночасно одна модель Vosk однієї мови;"
            " фраза іншою мовою - без звірки (лише whisper). Вимкнути звірку:"
            " «Сервіси» → «Звірка Vosk»."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.service = ServiceConfigPanel("vosk", endpoints, client_factory, bg)
        layout.addWidget(self.service)
        self.language_hint = QLabel("")
        self.language_hint.setWordWrap(True)
        layout.addWidget(self.language_hint)
        self.switch_button = QPushButton("")
        self.switch_button.setVisible(False)
        self.switch_button.clicked.connect(self.switch_model)
        layout.addWidget(self.switch_button)
        layout.addStretch()
        self._target: Optional[str] = None  # model the button would load
        self.service.state_changed.connect(self.update_language_hint)

    def _languages(self) -> Dict[str, str]:
        """Model id -> language, from the node's GET /v1/models."""
        models = (self.service.models or {}).get("models", [])
        return {m["id"]: m.get("language") or "" for m in models}

    def active_model(self) -> Optional[str]:
        """The model the node actually holds (else the configured one)."""
        ms = self.service.model_status
        if ms is not None and ms.model_id:
            return ms.model_id
        return self.service.configured_model()

    def model_for(self, language: str) -> Optional[str]:
        """A model of ``language``: one already on the node first."""
        models = (self.service.models or {}).get("models", [])
        of_lang = [m for m in models if m.get("language") == language]
        of_lang.sort(key=lambda m: not m.get("cached"))
        return of_lang[0]["id"] if of_lang else None

    def update_language_hint(self) -> None:
        model = self.active_model()
        langs = self._languages()
        self._target = None
        if model is None or model not in langs:
            self.language_hint.setText("")
            self.switch_button.setVisible(False)
            return
        lang = langs[model]
        src = self.source_language
        text = f"Модель Vosk на вузлі: {model} (мова «{lang}»)."
        style = ""
        if src == "auto":
            text += (
                " Мова мовлення - auto: звірка лише для фраз мовою"
                f" «{lang}», решта - лише whisper."
            )
        elif src != lang:
            style = "color: #b36b00;"
            text += (
                f" Мова мовлення - «{src}»: звірка Vosk недоступна, переклад"
                " лише з whisper."
            )
            self._target = self.model_for(src)
            if self._target is None:
                text += f" Моделі Vosk для «{src}» немає в реєстрі."
        self.language_hint.setText(text)
        self.language_hint.setStyleSheet(style)
        self.switch_button.setText(f"Завантажити модель для «{src}» ({self._target})")
        self.switch_button.setVisible(self._target is not None)
        self.switch_button.setEnabled(not self.service.busy())

    def set_source_language(self, language: str) -> None:
        """«Мова» changed in this dialog (not saved yet): re-check the hint."""
        self.source_language = language
        self.update_language_hint()

    def switch_model(self) -> None:
        """Explicit: POST /config model = a model of the session language."""
        if self._target is not None:
            self.service.apply({"model": self._target})


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


# The worker answers POST /models/load only once the model is in memory (a
# Hugging Face model is downloaded first, no progress is published).
WORKER_LOAD_TIMEOUT_S = APPLY_TIMEOUT_S

WORKER_SHARED_WARNING = (
    "Воркер спільний: ця модель перекладає і задачі черги Django на цьому"
    " вузлі. Поки app-сесія перекладача жива, воркер не бере задач черги;"
    " після неї черга працює на моделі, завантаженій тут. Задачі з"
    " закріпленою моделлю перемикають модель самі, а після простою воркер"
    " вивантажує модель і повертається до своєї типової (Nix)."
)


def format_worker_model_html(m: WorkerModel) -> str:
    """'завантажена: X (local)' / 'не завантажена (наступна: X)' + error."""
    source = {"local": "локальний файл", "huggingface": "Hugging Face"}.get(
        m.source, m.source
    )
    if m.loaded:
        text = f"модель воркера: <b>{escape(m.name)}</b> ({escape(source)}) - готова"
    else:
        nxt = f" (при першому запиті завантажиться {m.name})" if m.name else ""
        text = "модель воркера: не завантажена" + escape(nxt)
    queue = {"paused": "на паузі (app-сесія)", "active": "бере задачі"}.get(
        m.django_polling or "", m.django_polling or "-"
    )
    text += escape(f"; app-сесій: {m.app_sessions}; черга Django: {queue}")
    if m.error:
        text += (
            "<br>"
            + f'<span style="{ERROR_STYLE}">'
            + escape(f"помилка завантаження: {m.error}")
            + "</span>"
        )
    return text


class WorkerModelPanel(QGroupBox):
    """The model ACTUALLY in the chosen llama worker (``GET /status``), its
    model files (``GET /models``) and explicit load (a local file or a
    Hugging Face repo + file) / unload. The ``model`` of a chat request is
    ignored by the worker: the model loaded here is what translates."""

    def __init__(
        self,
        endpoint: Callable[[], Any],
        client_factory: Optional[Callable[[Any], Any]],
        bg: Background,
        parent=None,
    ):
        super().__init__("Модель воркера (для всіх його клієнтів)", parent)
        self._endpoint = endpoint
        self._factory = client_factory
        self._bg = bg
        self.state: Optional[WorkerModel] = None
        self._in_flight: Optional[str] = None  # load | unload | refresh
        self._polling = False
        self._started = 0.0
        self._what = ""
        outer = QVBoxLayout(self)
        self.warning = QLabel(WORKER_SHARED_WARNING)
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(WARN_STYLE)
        outer.addWidget(self.warning)
        self.model_state = QLabel(
            "модель воркера: стан невідомий (натисніть «Оновити»)"
        )
        self.model_state.setTextFormat(Qt.TextFormat.RichText)
        self.model_state.setWordWrap(True)
        outer.addWidget(self.model_state)
        local = QHBoxLayout()
        self.local_model = QComboBox()
        self.local_model.setToolTip("Файли моделей у MODEL_DIR воркера (GET /models)")
        local.addWidget(QLabel("Файл на воркері:"))
        local.addWidget(self.local_model, 1)
        self.refresh_button = QPushButton("Оновити")
        self.refresh_button.setToolTip("Прочитати стан і список моделей воркера")
        self.refresh_button.clicked.connect(self.refresh)
        local.addWidget(self.refresh_button)
        outer.addLayout(local)
        hf = QHBoxLayout()
        self.hf_repo = QLineEdit()
        self.hf_repo.setPlaceholderText(
            "Hugging Face repo, напр. IndexTeam/Index-Translate-2B-GGUF"
        )
        self.hf_file = QLineEdit()
        self.hf_file.setPlaceholderText("файл .gguf (порожньо - перший .gguf)")
        hf.addWidget(QLabel("або з HF:"))
        hf.addWidget(self.hf_repo, 2)
        hf.addWidget(self.hf_file, 1)
        outer.addLayout(hf)
        actions = QHBoxLayout()
        self.load_model_button = QPushButton("Завантажити модель")
        self.load_model_button.setToolTip(
            "HF repo заповнено - завантажити з Hugging Face у MODEL_DIR і в"
            " пам'ять; інакше - обраний файл воркера"
        )
        self.load_model_button.clicked.connect(self.load_model)
        self.unload_model_button = QPushButton("Вивантажити модель")
        self.unload_model_button.setToolTip("Звільнити RAM/VRAM, які тримає модель")
        self.unload_model_button.clicked.connect(self.unload_model)
        actions.addWidget(self.load_model_button)
        actions.addWidget(self.unload_model_button)
        actions.addStretch()
        outer.addLayout(actions)
        self.status = QLabel("" if endpoint() else "воркерів не знайдено")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(STATE_POLL_MS)
        self.poll_timer.timeout.connect(self._tick)
        self.local_model.currentIndexChanged.connect(lambda _i: self._update_buttons())
        self.hf_repo.textChanged.connect(lambda _t: self._update_buttons())
        self._update_buttons()

    def busy(self) -> bool:
        return self._in_flight is not None

    def clear(self) -> None:
        """The chosen worker changed: forget the other worker's state."""
        if self.busy():
            return
        self.state = None
        self.local_model.clear()
        self.model_state.setText("модель воркера: стан невідомий (натисніть «Оновити»)")
        self.status.setText("")
        self._update_buttons()

    def _update_buttons(self) -> None:
        usable = self._endpoint() is not None and self._factory is not None
        idle = usable and not self.busy()
        has_source = bool(self.hf_repo.text().strip()) or self.local_model.count() > 0
        self.refresh_button.setEnabled(idle)
        self.load_model_button.setEnabled(idle and has_source)
        self.unload_model_button.setEnabled(
            idle and bool(self.state and self.state.loaded)
        )
        if self._in_flight in ("load", "unload"):
            self.poll_timer.start()
        else:
            self.poll_timer.stop()

    def _client(self):
        endpoint = self._endpoint()
        if endpoint is None or self._factory is None:
            self.status.setText("воркер недоступний")
            return None, None
        return endpoint, self._factory(endpoint)

    # -- state / model files ------------------------------------------------

    def refresh(self, note: Optional[str] = None) -> None:
        """GET /status + GET /models of the chosen worker; ``note`` (the
        result of a load/unload) stays in the status line."""
        endpoint, client = self._client()
        if client is None or self.busy():
            return
        self._in_flight = "refresh"
        self.status.setText(f"читаю стан воркера {endpoint.node}…")
        self._update_buttons()

        def fetch():
            try:
                return client.worker_status(), client.worker_models()
            finally:
                client.close()

        def done(ok: bool, result: Any) -> None:
            self._in_flight = None
            if endpoint is self._endpoint():
                if ok:
                    self._show(*result)
                    self.status.setText(note or f"{endpoint.node}: стан прочитано")
                else:
                    self._error(result)
                    head = f"{note}; " if note else ""
                    self.status.setText(f"{head}{endpoint.node}: {result}")
            self._update_buttons()

        self._bg.run(fetch, done)

    def _show(self, state: WorkerModel, models: list) -> None:
        self._set_state(state)
        current = self.local_model.currentData() or state.name
        self.local_model.clear()
        for m in models:
            name = str(m.get("name") or "")
            if name:
                size = m.get("size_bytes")
                label = f"{name} ({_size(size)})" if size else name
                self.local_model.addItem(label, name)
        idx = self.local_model.findData(current)
        self.local_model.setCurrentIndex(max(idx, 0))

    def _set_state(self, state: WorkerModel) -> None:
        self.state = state
        self.model_state.setText(format_worker_model_html(state))

    def _error(self, error: Any) -> None:
        self.state = None
        self.model_state.setText(
            f'<span style="{ERROR_STYLE}">'
            + escape(f"стан воркера недоступний: {error}")
            + "</span>"
        )

    def _tick(self) -> None:
        """Poll /status while a load/unload is out (the worker publishes no
        download progress: elapsed time + what is in memory now)."""
        elapsed = int(time.monotonic() - self._started)
        self.status.setText(f"{self._what}… {elapsed} с")
        endpoint, client = self._client()
        if client is None or self._polling:
            if client is not None:
                client.close()
            return
        self._polling = True

        def fetch():
            try:
                return client.worker_status()
            finally:
                client.close()

        def done(ok: bool, result: Any) -> None:
            self._polling = False
            if endpoint is not self._endpoint() or not self.busy():
                return
            if ok:
                self._set_state(result)
            else:
                self._error(result)

        self._bg.run(fetch, done)

    # -- load / unload -----------------------------------------------------

    def load_model(self) -> None:
        """POST /models/load: the HF repo (+ file) if given, else the chosen
        file of MODEL_DIR."""
        repo = self.hf_repo.text().strip()
        if repo:
            kwargs = {"repo_id": repo, "filename": self.hf_file.text().strip() or None}
            what = f"{repo}/{kwargs['filename'] or '(перший .gguf)'}"
            self._what = f"завантаження {what} з Hugging Face і в пам'ять"
        else:
            name = self.local_model.currentData()
            if not name:
                self.status.setText("оберіть файл моделі або вкажіть HF repo")
                return
            kwargs, what = {"model_name": name}, name
            self._what = f"завантаження {name} в пам'ять"
        self._action(
            "load",
            lambda c: c.load_worker_model(timeout_s=WORKER_LOAD_TIMEOUT_S, **kwargs),
            lambda r: f"завантажено: {r.get('model') or what}",
        )

    def unload_model(self) -> None:
        """DELETE /models/unload."""
        self._what = "вивантаження моделі"
        self._action(
            "unload",
            lambda c: c.unload_worker_model(timeout_s=MODEL_ACTION_TIMEOUT_S),
            lambda r: (
                "модель вивантажено"
                if r.get("status") == "unloaded"
                else f"воркер: {r.get('message') or r.get('status')}"
            ),
        )

    def _action(self, action: str, call, describe) -> None:
        endpoint, client = self._client()
        if client is None or self.busy():
            return
        self._in_flight = action
        self._started = time.monotonic()
        self.status.setText(f"{self._what}…")
        self._update_buttons()

        def post():
            try:
                return call(client)
            finally:
                client.close()

        def done(ok: bool, result: Any) -> None:
            self._in_flight = None
            if ok:
                note = f"{endpoint.node}: {describe(result)}"
            else:
                what = "завантажити" if action == "load" else "вивантажити"
                note = f"{endpoint.node}: не вдалося {what} модель: {result}"
            self.status.setText(note)
            self._update_buttons()
            if endpoint is self._endpoint():
                self.refresh(note)

        self._bg.run(post, done)


class LlmTab(QWidget):
    """Translation prompt/template and request parameters (per user)."""

    def __init__(
        self,
        settings: EdgeSettings,
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
        self.worker_model = WorkerModelPanel(
            self.model_endpoint, client_factory, self._bg
        )
        self.use.currentIndexChanged.connect(lambda _i: self.worker_model.clear())
        self.system_prompt = QPlainTextEdit(llm.system_prompt)
        self.system_prompt.setFixedHeight(110)
        self.user_template = QPlainTextEdit(llm.user_template)
        self.user_template.setFixedHeight(60)
        # two-text mode (whisper + Vosk cross-check, TASK-SYS-12)
        self.check_system_prompt = QPlainTextEdit(llm.check_system_prompt)
        self.check_system_prompt.setFixedHeight(110)
        self.check_user_template = QPlainTextEdit(llm.check_user_template)
        self.check_user_template.setFixedHeight(60)
        # «Словник термінів» (services.llm.terms) -> {{terms}}
        self.terms = QPlainTextEdit(", ".join(llm.terms))
        self.terms.setFixedHeight(60)
        self.terms.setPlaceholderText(
            "Терміни через кому або з нового рядка (CRM, SaaS, Playwright)."
            " Порожньо = без словника"
        )
        self.terms.setToolTip(
            "Підставляється в system prompt замість {{terms}}: LLM перекладає"
            " розчуті неправильно терміни як правильні англійські"
        )
        reset = QPushButton("Типові prompt, шаблон і словник")
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
        form.addRow(self.worker_model)
        form.addRow(QLabel(PROMPT_VARIABLES_HELP))
        plain = QGroupBox("Без Vosk (лише текст whisper)")
        plain_form = QFormLayout(plain)
        plain_form.addRow("System prompt:", self.system_prompt)
        plain_form.addRow("Шаблон запиту:", self.user_template)
        check = QGroupBox("Зі звіркою Vosk (текст whisper + текст Vosk)")
        check_form = QFormLayout(check)
        check_form.addRow("System prompt:", self.check_system_prompt)
        check_form.addRow("Шаблон запиту:", self.check_user_template)
        self.active_prompts = QLabel(active_prompts_text(settings.use.vosk))
        self.active_prompts.setWordWrap(True)
        self.active_prompts.setStyleSheet("font-weight: bold;")
        form.addRow(self.active_prompts)
        form.addRow(plain)
        form.addRow(check)
        form.addRow("Словник термінів:", self.terms)
        form.addRow("Слів у {{text}} (максимум):", self.words_per_request)
        form.addRow("", reset)
        form.addRow("Temperature:", self.temperature)
        form.addRow("Max tokens:", self.max_tokens_cap)
        form.addRow("Контекст (пар фраз):", self.context_pairs)
        form.addRow("Попередній перегляд:", self.preview)
        for w in (
            self.system_prompt,
            self.user_template,
            self.check_system_prompt,
            self.check_user_template,
            self.terms,
        ):
            w.textChanged.connect(self.update_preview)
        self.words_per_request.valueChanged.connect(self.update_preview)
        self.update_preview()

    def model_endpoint(self):
        """Chosen worker, or the first one (auto order) for its model."""
        node = self.use.currentData()
        for e in self.endpoints:
            if node is None or e.node == node:
                return e
        return None

    def update_preview(self) -> None:
        """Rendered requests for PREVIEW_SAMPLE (or the template errors)."""
        system = self.system_prompt.toPlainText().strip()
        template = self.user_template.toPlainText().strip()
        check_system = self.check_system_prompt.toPlainText().strip()
        check_template = self.check_user_template.toPlainText().strip()
        errors = (
            system_prompt_errors("System prompt", system)
            + template_errors("Шаблон", template, True)
            + check_template_errors("System prompt звірки", check_system, False)
            + check_template_errors("Шаблон звірки", check_template, True)
        )
        if errors:
            self.preview.setPlainText("\n".join(errors))
            return
        chunks = split_words(PREVIEW_SAMPLE, self.words_per_request.value())
        terms = parse_terms(self.terms.toPlainText())
        head = build_messages(system, template, "", "pl", terms=terms)[0]["content"]
        lines = [
            f"[одиниця йде в переклад одразу (кінець речення, пауза, клауза"
            f" від 12 слів, 20 слів); до {self.words_per_request.value()} слів"
            f" у {{{{text}}}} на запит, неповний запит не чекає]",
            f"[system] {head}",
        ]
        for n, chunk in enumerate(chunks, 1):
            user = build_messages(system, template, chunk, "pl")[-1]["content"]
            words = len(chunk.split())
            lines.append(
                f"[запит {n}/{len(chunks)}, {{{{text}}}} = {words} слів] {user}"
            )
        check = build_messages(
            check_system,
            check_template,
            PREVIEW_SAMPLE,
            "pl",
            check=PREVIEW_CHECK,
            terms=terms,
        )
        lines.append(f"[звірка Vosk, system] {check[0]['content']}")
        lines.append(f"[звірка Vosk, запит] {check[-1]['content']}")
        self.preview.setPlainText("\n".join(lines))

    def reset_prompts(self) -> None:
        self.system_prompt.setPlainText(DEFAULT_SYSTEM_PROMPT)
        self.user_template.setPlainText(DEFAULT_USER_TEMPLATE)
        self.check_system_prompt.setPlainText(DEFAULT_CHECK_SYSTEM_PROMPT)
        self.check_user_template.setPlainText(DEFAULT_CHECK_USER_TEMPLATE)
        self.terms.setPlainText(", ".join(DEFAULT_TERMS))

    def values(self) -> Dict[str, Any]:
        return {
            "services.use.llm": self.use.currentData(),
            "services.llm.system_prompt": self.system_prompt.toPlainText().strip(),
            "services.llm.user_template": self.user_template.toPlainText().strip(),
            "services.llm.check_system_prompt": (
                self.check_system_prompt.toPlainText().strip()
            ),
            "services.llm.check_user_template": (
                self.check_user_template.toPlainText().strip()
            ),
            "services.llm.temperature": self.temperature.value(),
            "services.llm.max_tokens_cap": self.max_tokens_cap.value(),
            "services.llm.context_pairs": self.context_pairs.value(),
            "services.llm.words_per_request": self.words_per_request.value(),
            "services.llm.terms": list(parse_terms(self.terms.toPlainText())),
        }
