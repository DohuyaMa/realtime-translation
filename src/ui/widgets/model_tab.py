"""Model management tab for SettingsDialog."""
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame, QGroupBox, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget, QComboBox,
)

from ...core.models import ModelInfo, ModelManager, ModelStatus


STATUS_ICON = {
    ModelStatus.CACHED: "✅",
    ModelStatus.MISSING: "❌",
    ModelStatus.DOWNLOADING: "⬇️",
    ModelStatus.ERROR: "⚠️",
    ModelStatus.NIX_MANAGED: "🔵",
    ModelStatus.NOT_REQUIRED: "⏸",
}

STATUS_TEXT = {
    ModelStatus.CACHED: "cached",
    ModelStatus.MISSING: "missing",
    ModelStatus.DOWNLOADING: "downloading…",
    ModelStatus.ERROR: "error",
    ModelStatus.NIX_MANAGED: "nix-managed",
    ModelStatus.NOT_REQUIRED: "not required",
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

    download_requested = Signal(str)  # model_id

    def __init__(self, model_id: str, info: ModelInfo, parent=None):
        super().__init__(parent)
        self._model_id = model_id
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._build(info)

    def _build(self, info: ModelInfo):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Header row: icon + name + status
        header = QHBoxLayout()
        icon = STATUS_ICON.get(info.status, "?")
        status_text = STATUS_TEXT.get(info.status, "")
        self._status_label = QLabel(f"{icon}  {info.spec.display_name}  —  {status_text}")
        self._status_label.setStyleSheet("font-weight: bold;")
        header.addWidget(self._status_label)
        header.addStretch()

        if info.size_bytes:
            size_label = QLabel(ModelManager().format_size(info.size_bytes))
            size_label.setStyleSheet("color: gray; font-size: 11px;")
            header.addWidget(size_label)

        layout.addLayout(header)

        # Model id hint
        hint = QLabel(info.spec.model_id)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        # Download button + progress (hidden unless missing/error)
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
        self._status_label.setText(f"{icon}  {info.spec.display_name}  —  {text}")


class ModelTab(QWidget):
    """Models tab content for SettingsDialog."""

    whisper_backend_changed = Signal(str)   # "local" or "wyoming"
    whisper_model_changed = Signal(str)     # "small", "medium", etc.

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self._settings = current_settings
        self._manager = ModelManager()
        self._threads: dict[str, _DownloadThread] = {}
        self._cards: dict[str, ModelCard] = {}
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Whisper backend toggle ---
        backend_group = QGroupBox("Whisper Backend")
        backend_layout = QHBoxLayout()

        backend_layout.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(["Wyoming (remote)", "Local (faster-whisper)"])
        use_wyoming = self._settings.get("use_wyoming", True)
        self._backend_combo.setCurrentIndex(0 if use_wyoming else 1)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        backend_layout.addWidget(self._backend_combo)

        backend_layout.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(self._manager.whisper_models)
        current_model = self._settings.get("whisper_model", "small")
        idx = self._manager.whisper_models.index(current_model) if current_model in self._manager.whisper_models else 0
        self._model_combo.setCurrentIndex(idx)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        backend_layout.addWidget(self._model_combo)
        backend_layout.addStretch()

        backend_group.setLayout(backend_layout)
        layout.addWidget(backend_group)

        # --- Model status cards ---
        status_group = QGroupBox("Model Status")
        status_layout = QVBoxLayout()

        self._whisper_card = self._make_whisper_card()
        self._cards["whisper"] = self._whisper_card
        status_layout.addWidget(self._whisper_card)

        for model_id in ("translate", "tts", "en_core_web_sm"):
            info = self._manager.get_info(model_id)
            card = ModelCard(model_id, info)
            card.download_requested.connect(self._on_download)
            self._cards[model_id] = card
            status_layout.addWidget(card)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)
        layout.addStretch()

    def _make_whisper_card(self) -> ModelCard:
        use_wyoming = self._settings.get("use_wyoming", True)
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
        self._refresh_whisper_card()

    def _on_model_changed(self, index: int):
        model = self._manager.whisper_models[index]
        self.whisper_model_changed.emit(model)
        self._refresh_whisper_card()

    def _refresh_whisper_card(self):
        use_wyoming = self._backend_combo.currentIndex() == 0
        whisper_model = self._manager.whisper_models[self._model_combo.currentIndex()]
        if use_wyoming:
            info = self._manager.whisper_not_required()
        else:
            info = self._manager.get_info("whisper", whisper_model)
        self._whisper_card.set_status(info)

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
        card = self._cards[model_id]
        card.set_downloading(False)
        whisper_model = self._manager.whisper_models[self._model_combo.currentIndex()]
        info = self._manager.get_info(model_id, whisper_model)
        card.set_status(info)

    def get_settings(self) -> dict:
        return {
            "use_wyoming": self._backend_combo.currentIndex() == 0,
            "whisper_model": self._manager.whisper_models[self._model_combo.currentIndex()],
        }
