"""Model management tab for SettingsDialog."""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFrame, QGroupBox, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget, QComboBox,
)

from ...core.models import ModelInfo, ModelManager, ModelStatus
from ...core.huggingface import WYOMING_WHISPER_MODELS


STATUS_ICON = {
    ModelStatus.CACHED: "\u2705",
    ModelStatus.MISSING: "\u274c",
    ModelStatus.DOWNLOADING: "\u2b07\ufe0f",
    ModelStatus.ERROR: "\u26a0\ufe0f",
    ModelStatus.NIX_MANAGED: "\U0001f535",
    ModelStatus.NOT_REQUIRED: "\u23f8",
}

STATUS_TEXT = {
    ModelStatus.CACHED: "cached",
    ModelStatus.MISSING: "missing",
    ModelStatus.DOWNLOADING: "downloading\u2026",
    ModelStatus.ERROR: "error",
    ModelStatus.NIX_MANAGED: "nix-managed",
    ModelStatus.NOT_REQUIRED: "not required",
}

# Default config keys we persist
_CONFIG_KEYS = {
    "use_wyoming": False,
    "whisper_model": "small",
    "wyoming_model": "small-int8",
}


class _DownloadThread(QThread):
    progress = Signal(int, str)
    finished = Signal(bool)

    def __init__(self, manager: ModelManager, model_id: str, whisper_model: str = "small"):
        super().__init__()
        self._manager = manager
        self._model_id = model_id
        self._whisper_model = whisper_model

    def run(self):
        ok = self._manager.download(
            self._model_id,
            self._whisper_model,
            lambda pct, msg: self.progress.emit(pct, msg),
        )
        self.finished.emit(ok)


class ModelCard(QFrame):
    """Single-model status card."""

    download_requested = Signal(str)

    def __init__(self, model_id: str, info: ModelInfo, parent=None):
        super().__init__(parent)
        self._model_id = model_id
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._build(info)

    def _build(self, info: ModelInfo):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        header = QHBoxLayout()
        icon = STATUS_ICON.get(info.status, "?")
        status_text = STATUS_TEXT.get(info.status, "")
        self._status_label = QLabel(f"{icon}  {info.spec.display_name}  \u2014  {status_text}")
        self._status_label.setStyleSheet("font-weight: bold;")
        header.addWidget(self._status_label)
        header.addStretch()

        if info.size_bytes:
            size_label = QLabel(ModelManager().format_size(info.size_bytes))
            size_label.setStyleSheet("color: gray; font-size: 11px;")
            header.addWidget(size_label)

        layout.addLayout(header)

        hint = QLabel(info.spec.model_id)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        if info.status in (ModelStatus.MISSING, ModelStatus.ERROR):
            btn_row = QHBoxLayout()
            self._dl_btn = QPushButton("Download")
            self._dl_btn.setMaximumWidth(120)
            self._dl_btn.clicked.connect(lambda: self.download_requested.emit(self._model_id))
            btn_row.addWidget(self._dl_btn)
            btn_row.addStretch()
            layout.addLayout(btn_row)

            self._progress = QProgressBar()
            self._progress.setRange(0, 0)
            self._progress.setVisible(False)
            self._progress.setMaximumHeight(12)
            layout.addWidget(self._progress)

            self._log_label = QLabel()
            self._log_label.setStyleSheet("color: gray; font-size: 10px;")
            self._log_label.setVisible(False)
            layout.addWidget(self._log_label)
        else:
            self._dl_btn = None
            self._progress = None
            self._log_label = None

        if info.spec.size_hint and info.status == ModelStatus.MISSING:
            size_hint_label = QLabel(f"Expected size: {info.spec.size_hint}")
            size_hint_label.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(size_hint_label)

    def set_downloading(self, active: bool):
        if self._dl_btn:
            self._dl_btn.setEnabled(not active)
        if self._progress:
            self._progress.setVisible(active)
        if self._log_label:
            self._log_label.setVisible(active)

    def update_log(self, message: str):
        if self._log_label:
            self._log_label.setText(message)

    def set_status(self, info: ModelInfo):
        icon = STATUS_ICON.get(info.status, "?")
        text = STATUS_TEXT.get(info.status, "")
        self._status_label.setText(f"{icon}  {info.spec.display_name}  \u2014  {text}")


class ModelTab(QWidget):
    """Models tab content for SettingsDialog."""

    whisper_backend_changed = Signal(str)
    whisper_model_changed = Signal(str)
    wyoming_model_changed = Signal(str)

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        merged = {**_CONFIG_KEYS, **current_settings}
        self._settings = merged
        self._manager = ModelManager()
        self._threads: dict[str, _DownloadThread] = {}
        self._cards: dict[str, ModelCard] = {}
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Whisper backend toggle ---
        backend_group = QGroupBox("Whisper Backend")
        backend_layout = QVBoxLayout()

        # Row 1: backend + model selection
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(["Wyoming (remote)", "Local (faster-whisper)"])
        use_wyoming = self._settings.get("use_wyoming", False)
        self._backend_combo.setCurrentIndex(0 if use_wyoming else 1)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        row1.addWidget(self._backend_combo)

        row1.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(self._manager.whisper_models)
        current_model = self._settings.get("whisper_model", "small")
        idx = self._manager.whisper_models.index(current_model) if current_model in self._manager.whisper_models else 0
        self._model_combo.setCurrentIndex(idx)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        row1.addWidget(self._model_combo)
        row1.addStretch()
        backend_layout.addLayout(row1)

        # Row 2: Wyoming model selection (visible only in Wyoming mode)
        row2_widget = QWidget()
        row2 = QHBoxLayout(row2_widget)
        row2.addWidget(QLabel("Wyoming model:"))
        self._wyoming_model_combo = QComboBox()
        self._wyoming_model_combo.addItems(WYOMING_WHISPER_MODELS)
        current_wyoming_model = self._settings.get("wyoming_model", "small-int8")
        wy_idx = WYOMING_WHISPER_MODELS.index(current_wyoming_model) if current_wyoming_model in WYOMING_WHISPER_MODELS else 0
        self._wyoming_model_combo.setCurrentIndex(wy_idx)
        self._wyoming_model_combo.currentIndexChanged.connect(self._on_wyoming_model_changed)
        row2.addWidget(self._wyoming_model_combo)
        row2.addStretch()
        backend_layout.addWidget(row2_widget)
        self._wyoming_model_row = row2_widget

        backend_group.setLayout(backend_layout)
        layout.addWidget(backend_group)

        # --- Model status cards ---
        status_group = QGroupBox("Model Status")
        status_layout = QVBoxLayout()

        self._whisper_card = self._make_whisper_card()
        self._cards["whisper"] = self._whisper_card
        status_layout.addWidget(self._whisper_card)

        # Whisper model browser (available models for download)
        self._whisper_browser_label = QLabel()
        self._whisper_browser_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        self._whisper_browser_label.setVisible(False)
        status_layout.addWidget(self._whisper_browser_label)
        self._whisper_browser_widgets: list[QWidget] = []

        for model_id in ("translate", "tts", "en_core_web_sm"):
            info = self._manager.get_info(model_id)
            card = ModelCard(model_id, info)
            card.download_requested.connect(self._on_download)
            self._cards[model_id] = card
            status_layout.addWidget(card)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        self._refresh_ui()
        layout.addStretch()

    def _make_whisper_card(self) -> ModelCard:
        use_wyoming = self._settings.get("use_wyoming", False)
        whisper_model = self._settings.get("whisper_model", "small")
        if use_wyoming:
            info = self._manager.whisper_not_required()
        else:
            info = self._manager.get_info("whisper", whisper_model)
        card = ModelCard("whisper", info)
        card.download_requested.connect(self._on_download)
        return card

    def _on_backend_changed(self, index: int):
        backend = "wyoming" if index == 0 else "local"
        self.whisper_backend_changed.emit(backend)
        self._refresh_ui()

    def _on_model_changed(self, index: int):
        model = self._manager.whisper_models[index]
        self.whisper_model_changed.emit(model)
        self._refresh_whisper_card()

    def _on_wyoming_model_changed(self, index: int):
        model = WYOMING_WHISPER_MODELS[index]
        self.wyoming_model_changed.emit(model)

    def _refresh_ui(self):
        is_wyoming = self._backend_combo.currentIndex() == 0
        self._wyoming_model_row.setVisible(is_wyoming)
        self._refresh_whisper_card()
        self._show_whisper_browser(is_wyoming)

    def _refresh_whisper_card(self):
        is_wyoming = self._backend_combo.currentIndex() == 0
        whisper_model = self._manager.whisper_models[self._model_combo.currentIndex()]
        if is_wyoming:
            info = self._manager.whisper_not_required()
        else:
            info = self._manager.get_info("whisper", whisper_model)
        self._whisper_card.set_status(info)

    def _show_whisper_browser(self, visible: bool):
        """Show available whisper models with download buttons in Wyoming mode."""
        # Clear old browser widgets
        for w in self._whisper_browser_widgets:
            w.setParent(None)
            w.deleteLater()
        self._whisper_browser_widgets.clear()

        if not visible:
            self._whisper_browser_label.setVisible(False)
            return

        parent_layout = self._whisper_card.parentWidget().layout()
        if not parent_layout:
            return

        self._whisper_browser_label.setText("Available Whisper Models (pre-download for Wyoming):")
        self._whisper_browser_label.setVisible(True)

        models = self._manager.get_downloadable_whisper_models()
        for m in models:
            row = QHBoxLayout()
            label = QLabel(f"{m['display_name']}  ({m['size_hint']})")
            label.setStyleSheet("font-size: 11px;")
            row.addWidget(label)
            row.addStretch()

            # Check if already cached
            info = self._manager.get_info("whisper", m["id"])
            if info.status == ModelStatus.CACHED:
                size_label = QLabel(self._manager.format_size(info.size_bytes))
                size_label.setStyleSheet("color: gray; font-size: 10px;")
                row.addWidget(size_label)
                cached = QLabel("cached")
                cached.setStyleSheet("color: green; font-size: 10px;")
                row.addWidget(cached)
            else:
                btn = QPushButton("Download")
                btn.setMaximumWidth(100)
                model_id = m["id"]
                btn.clicked.connect(lambda checked, mid=model_id: self._on_download_whisper(mid))
                row.addWidget(btn)

            container = QWidget()
            container.setLayout(row)
            parent_layout.insertWidget(
                parent_layout.indexOf(self._whisper_browser_label) + 1 + len(self._whisper_browser_widgets),
                container,
            )
            self._whisper_browser_widgets.append(container)

    def _on_download_whisper(self, model_id: str):
        if model_id in self._threads and self._threads[model_id].isRunning():
            return

        def progress_wrapper(pct, msg):
            card = self._cards.get("whisper")
            if card:
                card.update_log(msg)

        thread = _DownloadThread(self._manager, "whisper", model_id)
        card = self._cards.get("whisper")
        if card:
            card.set_downloading(True)
        thread.progress.connect(progress_wrapper)
        thread.finished.connect(lambda ok: self._on_download_done("whisper", ok))
        self._threads[model_id] = thread
        thread.start()

    def _on_download(self, model_id: str):
        if model_id in self._threads and self._threads[model_id].isRunning():
            return
        whisper_model = self._manager.whisper_models[self._model_combo.currentIndex()]
        thread = _DownloadThread(self._manager, model_id, whisper_model)
        card = self._cards[model_id]
        card.set_downloading(True)
        thread.progress.connect(lambda _pct, msg: card.update_log(msg))
        thread.finished.connect(lambda ok: self._on_download_done(model_id, ok))
        self._threads[model_id] = thread
        thread.start()

    def _on_download_done(self, model_id: str, ok: bool):
        card = self._cards.get(model_id)
        if card:
            card.set_downloading(False)
            whisper_model = self._manager.whisper_models[self._model_combo.currentIndex()]
            info = self._manager.get_info(model_id, whisper_model)
            card.set_status(info)
        # Refresh the whisper browser to update cached status
        self._show_whisper_browser(self._backend_combo.currentIndex() == 0)

    def get_settings(self) -> dict:
        return {
            "whisper_model": self._manager.whisper_models[self._model_combo.currentIndex()],
            "wyoming_model": WYOMING_WHISPER_MODELS[self._wyoming_model_combo.currentIndex()],
        }
