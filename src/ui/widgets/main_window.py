"""Refactored main window for the real-time translation application using controller pattern."""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QProgressBar,
    QCheckBox, QGroupBox, QSystemTrayIcon, QMenu,
    QFrame, QSplitter, QApplication
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QAction
import sys
from loguru import logger
from typing import Optional, Dict

from ..controller import UIController
from ..controller.ui_controller import SERVICE_NAMES, PipelineEventLog
from ...controller.translator_controller import ConcreteTranslatorController
from ...adapters import DirectAdapter
from .status_logger import StatusLogger, StatusManager
from .service_status_panel import ServiceStatusPanel
from ...core.runtime import EdgeSettings
from .settings_dialog import SettingsButton
from .tray_icon import AppState, app_state, make_icon
from ...audio.routing import VIRTUAL_MIC_SOURCE, VIRTUAL_OUTPUT_SINK

POWER_OFF_TEXT = "Зупинити все й звільнити пам'ять"
POWER_ON_TEXT = "Запустити"
DEFAULT_OUTPUT_TEXT = "Системний вихід (за замовчуванням)"
DEFAULT_INPUT_TEXT = "Системний мікрофон (за замовчуванням)"
MONITOR_TEXT = "Також чути в навушниках"
# config.yml keys of the device choices (defaults in config/default.yml)
INPUT_KEY = "audio.input_device"
OUTPUT_KEY = "audio.output_device"
MONITOR_KEY = "audio.monitor_device"
MONITOR_ON_KEY = "audio.monitor_enabled"
# «Показувати звук (фонетика)» under each recognized phrase
SHOW_SOUND_KEY = "ui.show_phonetic_text"
RECORD_KEY = "services.record_session"
RECORD_TEXT = "Записувати сесію для аналізу"


def _save_choice(key: str, value) -> None:
    """Persist one device choice; ConfigManager writes only non-defaults."""
    from ...core.config import get_config_manager

    cfg = get_config_manager()
    cfg.set(key, value)
    cfg.save()


class MainWindow(QMainWindow):
    """Main window for the real-time translation application using controller pattern."""
    _update_signal = Signal(object)
    _task_done = Signal(object, object)  # callback, result
    _status_signal = Signal(str)
    _pipeline_event = Signal(object)  # TranslationSystem status event

    def __init__(self, controller: Optional[UIController] = None):
        super().__init__()
        
        # UI controller (abstraction over backend)
        self.ui_controller: Optional[UIController] = controller
        
        # Status manager for separating status display from logging
        self.status_logger = StatusLogger()
        self.status_manager = StatusManager(self.status_logger)
        self._restore_show_sound()
        
        # Service status panel
        self.service_status_panel = ServiceStatusPanel()
        
        # UI state
        self.is_translating = False
        # pipeline-start warm-up: services still warming ([] = all ready)
        self._warming: list = []
        # kill switch: standby after «Зупинити все…»; busy while it works
        self._standby = False
        self._power_busy: Optional[str] = None  # "stopping" | "starting"
        self._power_errors: list = []
        self.update_timer: Optional[QTimer] = None
        
        # Connect UI controller callbacks
        self._update_signal.connect(self._apply_ui_update)
        self._task_done.connect(self._finish_task)
        self._status_signal.connect(self._apply_status)
        self._busy = False
        self.ui_controller.set_status_callback(self.handle_status_update)
        self.ui_controller.set_update_callback(self.update_ui_from_controller)
        self._event_log = PipelineEventLog()
        self._pipeline_event.connect(self._apply_pipeline_event)
        self.ui_controller.set_pipeline_event_callback(self._pipeline_event.emit)
        
        self.init_ui()
        self.setup_tray_icon()
        self.refresh_device_lists()
        self._refresh_app_state()
        
        self.service_status_panel.refresh_requested.connect(
            self.on_refresh_requested
        )
        self.service_status_panel.endpoints_requested.connect(
            self.settings_button.open_settings
        )
        self.service_status_panel.use_changed.connect(self.on_use_changed)
        self.ui_controller.start_event_polling(interval=0.5)
        
    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle('Real-Time Translator')
        self.setMinimumWidth(800)
        self.setMinimumHeight(700)  # Increased to accommodate status logger
        
        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Create a splitter to separate main controls from status logger
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Upper part: main controls
        upper_widget = QWidget()
        upper_layout = QVBoxLayout(upper_widget)
        
        # Language selection
        lang_group = QGroupBox("Language Settings")
        lang_layout = QVBoxLayout()
        
        # Source language
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("Source Language:"))
        self.source_lang_combo = QComboBox()
        self.source_lang_combo.addItems(
            ["Auto", "Ukrainian (uk)", "Polish (pl)", "Russian (ru)", "English (en)"]
        )
        self.source_lang_combo.currentTextChanged.connect(self.on_language_changed)
        source_layout.addWidget(self.source_lang_combo)
        lang_layout.addLayout(source_layout)
        
        # Target language (fixed to English)
        target_layout = QHBoxLayout()
        target_layout.addWidget(QLabel("Target Language:"))
        target_label = QLabel("English (en)")
        target_layout.addWidget(target_label)
        lang_layout.addLayout(target_layout)
        
        lang_group.setLayout(lang_layout)
        upper_layout.addWidget(lang_group)
        
        # Audio devices
        audio_group = QGroupBox("Audio Devices")
        audio_layout = QVBoxLayout()
        
        # Input device
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("Input Device:"))
        self.input_device_combo = QComboBox()
        self.input_device_combo.setToolTip(
            "Звідки застосунок записує мову; системний мікрофон інших"
            " застосунків не змінюється"
        )
        self.input_device_combo.currentIndexChanged.connect(
            self.on_input_device_changed
        )
        input_layout.addWidget(self.input_device_combo)
        audio_layout.addLayout(input_layout)
        
        # Output device: where the translation is played (the app's own
        # stream only - the system default sink is not touched)
        output_layout = QHBoxLayout()
        output_layout.addWidget(QLabel("Output Device:"))
        self.output_device_combo = QComboBox()
        self.output_device_combo.setToolTip(
            f"Куди звучить переклад. «{VIRTUAL_OUTPUT_SINK}» → інші застосунки"
            f" чують його як мікрофон «RT Переклад (мікрофон)» ({VIRTUAL_MIC_SOURCE})."
        )
        self.output_device_combo.currentIndexChanged.connect(
            self.on_output_device_changed
        )
        output_layout.addWidget(self.output_device_combo)
        audio_layout.addLayout(output_layout)

        # Monitor: the translation also in the user's headphones, in sync
        monitor_layout = QHBoxLayout()
        self.monitor_check = QCheckBox(MONITOR_TEXT)
        self.monitor_check.setToolTip(
            "Переклад звучить і в «Output Device», і в цьому пристрої одночасно"
        )
        self.monitor_check.toggled.connect(self.on_monitor_changed)
        monitor_layout.addWidget(self.monitor_check)
        self.monitor_device_combo = QComboBox()
        self.monitor_device_combo.currentIndexChanged.connect(
            lambda _i: self.on_monitor_changed()
        )
        monitor_layout.addWidget(self.monitor_device_combo, 1)
        audio_layout.addLayout(monitor_layout)
        
        audio_group.setLayout(audio_layout)
        upper_layout.addWidget(audio_group)
        
        # Audio levels
        levels_group = QGroupBox("Audio Levels")
        levels_layout = QVBoxLayout()
        
        # Input level
        input_level_layout = QHBoxLayout()
        input_level_layout.addWidget(QLabel("Input:"))
        self.input_level = QProgressBar()
        self.input_level.setMinimum(0)
        self.input_level.setMaximum(100)
        input_level_layout.addWidget(self.input_level)
        levels_layout.addLayout(input_level_layout)
        
        # Output level
        output_level_layout = QHBoxLayout()
        output_level_layout.addWidget(QLabel("Output:"))
        self.output_level = QProgressBar()
        self.output_level.setMinimum(0)
        self.output_level.setMaximum(100)
        output_level_layout.addWidget(self.output_level)
        levels_layout.addLayout(output_level_layout)
        
        levels_group.setLayout(levels_layout)
        upper_layout.addWidget(levels_group)
        
        # Service status panel
        upper_layout.addWidget(self.service_status_panel)
        # Kill switch: stop everything and free the memory / start again
        power_layout = QHBoxLayout()
        self.power_button = QPushButton(POWER_OFF_TEXT)
        self.power_button.clicked.connect(self.toggle_power)
        power_layout.addWidget(self.power_button)
        self.power_label = QLabel()
        self.power_label.setWordWrap(True)
        power_layout.addWidget(self.power_label, 1)
        upper_layout.addLayout(power_layout)
        # Status and controls
        status_layout = QHBoxLayout()
        
        # Translation toggle
        self.translate_button = QPushButton("Start Translation")
        self.translate_button.clicked.connect(self.toggle_translation)
        status_layout.addWidget(self.translate_button)
        
        # Settings button
        self.settings_button = SettingsButton()
        self.settings_button.settings_applied.connect(self.on_general_settings_changed)
        status_layout.addWidget(self.settings_button)
        
        # Always on top
        self.always_on_top = QCheckBox("Always on Top")
        self.always_on_top.stateChanged.connect(self.toggle_always_on_top)
        status_layout.addWidget(self.always_on_top)
        
        # Minimize to tray
        self.minimize_to_tray = QCheckBox("Minimize to Tray")
        self.minimize_to_tray.setChecked(True)
        status_layout.addWidget(self.minimize_to_tray)

        # «Записувати сесію для аналізу» (services.record_session)
        self.record_check = QCheckBox(RECORD_TEXT)
        self.record_check.setToolTip(
            "Аудіо кожної фрази (WAV) + whisper, фонетика, рішення фільтра і переклад"
            " (JSONL) у ~/.local/state/real-time-translator/sessions/<час>/ -"
            " для офлайн-аналізу. Типово вимкнено."
        )
        self._restore_record_check()
        status_layout.addWidget(self.record_check)
        
        upper_layout.addLayout(status_layout)
        
        # Add upper widget to splitter
        main_splitter.addWidget(upper_widget)
        
        # Lower part: status logger
        main_splitter.addWidget(self.status_logger)
        
        # Set initial sizes (give more space to controls, less to logger)
        main_splitter.setSizes([500, 200])
        
        main_layout.addWidget(main_splitter)
        
        # Set up update timer (reduced frequency as per context recommendations)
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.fallback_update)
        self.update_timer.start(1000)  # Update every 1000ms as fallback only
    
    def fallback_update(self):
        """Fallback update method in case event-based updates fail."""
        # This is just a fallback; primary updates come from the controller
        pass
    
    def setup_tray_icon(self):
        """Set up system tray icon and menu."""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.activated.connect(self._on_tray_activated)

        # Create tray menu
        tray_menu = QMenu()

        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)

        self.tray_toggle_action = QAction("Start Translation", self)
        self.tray_toggle_action.triggered.connect(self.toggle_translation)
        tray_menu.addAction(self.tray_toggle_action)

        self.tray_power_action = QAction(POWER_OFF_TEXT, self)
        self.tray_power_action.triggered.connect(self.toggle_power)
        tray_menu.addAction(self.tray_power_action)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def app_state(self) -> AppState:
        return app_state(
            standby=self._standby,
            busy=self._power_busy,
            translating=self.is_translating,
            errors=self._power_errors,
        )

    def _refresh_app_state(self) -> None:
        """Tray icon/tooltip, state label, kill switch and translate
        controls from the current state - GUI thread only."""
        state = self.app_state()
        self.tray_icon.setIcon(make_icon(state))
        self.tray_icon.setToolTip(state.tooltip)
        self.setWindowIcon(make_icon(state))
        self.power_label.setText(f"Стан: {state.text}")
        power_text = POWER_ON_TEXT if self._standby else POWER_OFF_TEXT
        for widget in (self.power_button, self.tray_power_action):
            widget.setText(power_text)
            widget.setEnabled(self._power_busy is None)
        toggle_text = (
            "Stop Translation" if self.is_translating else "Start Translation"
        )
        self.tray_toggle_action.setText(toggle_text)
        can_translate = not self._standby and self._power_busy is None
        self.translate_button.setEnabled(can_translate)
        self.tray_toggle_action.setEnabled(can_translate)
        if self._standby:
            self.translate_button.setToolTip(f"Спершу «{POWER_ON_TEXT}»")
        else:
            self.translate_button.setToolTip("")

    def toggle_power(self):
        """Kill switch: «Зупинити все й звільнити пам'ять» (stop pipeline ->
        release the worker session -> unload this host's models) or
        «Запустити» (the reverse) - off the GUI thread."""
        if not self.ui_controller or self._power_busy is not None:
            return
        stopping = not self._standby
        fn = self.ui_controller.standby if stopping else self.ui_controller.resume

        def work():
            errors = list(fn() or [])
            return errors, bool(self.ui_controller.get_status().get("running", False))

        if not self._run_async(work, self._on_power_done):
            return
        self._power_busy = "stopping" if stopping else "starting"
        self._power_errors = []
        self._refresh_app_state()
        self.status_manager.log_info(self.app_state().text)

    def _on_power_done(self, result):
        stopping = self._power_busy == "stopping"
        self._power_busy = None
        if isinstance(result, Exception):
            errors, running = [f"{type(result).__name__}: {result}"], False
        else:
            errors, running = result
        self._standby = stopping
        self._power_errors = errors
        self.is_translating = running
        self._refresh_app_state()
        text = self.app_state().text
        self.status_manager.set_status(text)
        if errors:
            self.status_manager.log_error(text)
        else:
            self.status_manager.log_info(text)
            if stopping:
                self.status_manager.log_info(
                    "LLM llama-воркера не вивантажено: він обслуговує чергу Django"
                )

    def _restore_show_sound(self) -> None:
        """«Показувати звук (фонетика)»: saved in config.yml, saved on
        toggle."""
        from ...core.config import get_config_manager

        box = self.status_logger.show_sound
        box.setChecked(bool(get_config_manager().get(SHOW_SOUND_KEY)))
        box.toggled.connect(lambda on: _save_choice(SHOW_SOUND_KEY, on))

    def _restore_record_check(self) -> None:
        """The record toggle mirrors the settings; RT_EDGE_RECORD_SESSION
        makes it read-only (managed)."""
        from .settings_dialog import current_settings

        s = current_settings()
        self.record_check.setChecked(s.record_session)
        if "record_session" in s.managed:
            self.record_check.setEnabled(False)
            self.record_check.setToolTip("Задано змінною RT_EDGE_RECORD_SESSION")
        self.record_check.toggled.connect(self.on_record_session_changed)

    def on_record_session_changed(self, on: bool) -> None:
        from .settings_dialog import apply_value

        settings, errors = apply_value(RECORD_KEY, bool(on))
        if errors:
            self.status_manager.log_error(f"Запис сесії: {'; '.join(errors)}")
            return
        self.on_general_settings_changed(settings)

    def refresh_device_lists(self):
        """Fill the device lists, select the saved choices (config.yml) and
        hand them to the backend; later changes are saved by the handlers."""
        from ...core.config import get_config_manager

        cfg = get_config_manager()
        try:
            inputs = self.ui_controller.get_input_devices()
            outputs = self.ui_controller.get_output_devices()
        except Exception as e:
            logger.error(f"Error refreshing device lists: {e}")
            self.status_manager.log_error(f"Error refreshing device lists: {e}")
            return
        lists = (
            (self.input_device_combo, DEFAULT_INPUT_TEXT, inputs, INPUT_KEY),
            (self.output_device_combo, DEFAULT_OUTPUT_TEXT, outputs, OUTPUT_KEY),
            (self.monitor_device_combo, None, outputs, MONITOR_KEY),
        )
        for combo, default_text, devices, key in lists:
            combo.blockSignals(True)
            combo.clear()
            if default_text is not None:
                combo.addItem(default_text, "")
            for device in devices:
                combo.addItem(device['description'], device['name'])
            saved = cfg.get(key)
            index = combo.findData(saved) if saved else -1
            if saved and index < 0:
                self.status_manager.log_warning(
                    f"Збережений пристрій {saved} зараз недоступний"
                )
            combo.setCurrentIndex(max(index, 0))
            combo.blockSignals(False)
        self.monitor_check.blockSignals(True)
        self.monitor_check.setChecked(bool(cfg.get(MONITOR_ON_KEY)))
        self.monitor_check.blockSignals(False)
        self.monitor_device_combo.setEnabled(self.monitor_check.isChecked())
        self._apply_devices()

    def _choice(self, combo) -> Optional[str]:
        return combo.currentData() or None

    def _monitor_choice(self) -> Optional[str]:
        if not self.monitor_check.isChecked():
            return None
        return self._choice(self.monitor_device_combo)

    def _apply_devices(self) -> None:
        """Hand the selected devices to the backend (no saving)."""
        ok = self.ui_controller.set_input_device(
            self._choice(self.input_device_combo)
        )
        if not ok:
            err = self.ui_controller.last_error() or "unknown error"
            self.status_manager.log_error(f"Не вдалося вибрати мікрофон: {err}")
        self.ui_controller.set_output_device(self._choice(self.output_device_combo))
        self.ui_controller.set_monitor_device(self._monitor_choice())

    def _finish_task(self, done, result):
        self._busy = False
        done(result)

    def _run_async(self, fn, done, exclusive=True):
        """Run blocking backend work off the GUI thread; done(result) runs on it.

        Only one exclusive task runs at a time; overlapping requests are dropped.
        """
        import threading

        if exclusive and self._busy:
            self.status_manager.log_info('Busy, request ignored')
            return False
        self._busy = True

        def work():
            try:
                result = fn()
            except Exception as e:
                logger.error(f"Background task failed: {e}")
                result = e
            self._task_done.emit(done, result)

        threading.Thread(target=work, daemon=True, name="ui-task").start()
        return True

    def toggle_translation(self):
        """Toggle translation on/off without blocking the GUI thread."""
        if not self.ui_controller or not self.translate_button.isEnabled():
            return
        self.translate_button.setEnabled(False)
        self.translate_button.setText("...")

        def work():
            ok = self.ui_controller.toggle_pipeline()
            running = self.ui_controller.get_status().get('running', False)
            return ok, running

        if not self._run_async(work, self._on_toggle_done):
            self.translate_button.setEnabled(True)
            self.translate_button.setText(
                "Stop Translation" if self.is_translating else "Start Translation"
            )

    def _on_toggle_done(self, result):
        ok, running = (
            result if isinstance(result, tuple) else (False, self.is_translating)
        )
        if not ok:
            err = self.ui_controller.last_error() or 'unknown error'
            self.status_manager.log_error(f'Pipeline toggle failed: {err}')
        else:
            self.is_translating = bool(running)
            msg = 'Translation active' if running else 'Translation stopped'
            self.status_manager.set_status(self._translation_status_text())
            self.status_manager.log_info(msg)
        self.translate_button.setText(
            "Stop Translation" if self.is_translating else "Start Translation"
        )
        self._refresh_app_state()

    def toggle_always_on_top(self, state):
        """Toggle always-on-top window state."""
        flags = self.windowFlags()
        if state:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        
    def on_language_changed(self, text):
        """Handle language selection change."""
        if not self.ui_controller:
            return
            
        # Extract language code from combo box text
        lang_code = text.split('(')[-1].strip(')')
        if lang_code.lower() == 'auto':
            lang_code = 'auto'
            
        self._run_async(
            lambda: self.ui_controller.set_languages(lang_code), lambda _r: None
        )
    
    def on_input_device_changed(self, index):
        """Capture straight from the chosen source; the system default
        microphone is never changed."""
        if not self.ui_controller or index < 0:
            return
        name = self.input_device_combo.itemData(index) or None
        text = self.input_device_combo.itemText(index)
        if not self.ui_controller.set_input_device(name):
            err = self.ui_controller.last_error() or "unknown error"
            self.status_manager.log_error(f"Не вдалося вибрати мікрофон {text}: {err}")
            return
        _save_choice(INPUT_KEY, name)
        self.status_manager.set_status(f"Мікрофон: {text}")
        self.status_manager.log_info(f"Мікрофон: {text}")

    def on_monitor_changed(self, *_args):
        """«Також чути в навушниках»: the translation also goes to the
        chosen sink, in sync with «Output Device»."""
        if not self.ui_controller:
            return
        on = self.monitor_check.isChecked()
        self.monitor_device_combo.setEnabled(on)
        name = self._monitor_choice()
        if not self.ui_controller.set_monitor_device(name):
            self.status_manager.log_error("Не вдалося вибрати навушники")
            return
        _save_choice(MONITOR_ON_KEY, on)
        _save_choice(MONITOR_KEY, self._choice(self.monitor_device_combo))
        if on:
            text = self.monitor_device_combo.currentText()
            self.status_manager.log_info(f"Переклад також звучить у: {text}")
        else:
            self.status_manager.log_info("Переклад у навушниках вимкнено")

    def on_output_device_changed(self, index):
        """Sink the translation is played into (None = the default sink)."""
        if not self.ui_controller or index < 0:
            return
        name = self.output_device_combo.itemData(index) or None
        text = self.output_device_combo.itemText(index)
        if not self.ui_controller.set_output_device(name):
            self.status_manager.log_error(f"Не вдалося вибрати вихід: {text}")
            return
        _save_choice(OUTPUT_KEY, name)
        self.status_manager.log_info(f"Переклад звучить у: {text}")
        if name == VIRTUAL_OUTPUT_SINK:
            self.status_manager.log_info(
                "В інших застосунках (Zoom, Teams, браузер) оберіть мікрофон"
                f" «RT Переклад (мікрофон)» ({VIRTUAL_MIC_SOURCE})"
            )

    def update_ui(self):
        """Update UI elements with current status - called by timer."""
        # This is the fallback polling approach, but we also update from controller events
        if self.ui_controller:
            self.ui_controller.update_ui()
    
    def update_ui_from_controller(self, update_data: Dict):
        """Called from UIController polling thread — marshals to main thread via signal."""
        self._update_signal.emit(update_data)

    def _apply_ui_update(self, update_data: Dict):
        """Apply UI update — always runs on the main Qt thread."""
        # Audio level
        input_level = int(update_data.get('input', 0) * 100)
        self.input_level.setValue(input_level)

        # Recognized / translated text panels
        for phrase in update_data.get('pending_recognized', []):
            self.status_manager.log_recognized(phrase["text"], phrase["sound"])
        for text in update_data.get('pending_translated', []):
            self.status_manager.log_translated(text)
        self.status_manager.set_tail(update_data.get('source_tail') or "")

        # Service status indicators
        self.service_status_panel.update_status(update_data)

    def _translation_status_text(self) -> str:
        if not self.is_translating:
            return 'Translation stopped'
        if self._warming:
            names = ", ".join(SERVICE_NAMES.get(k, k) for k in self._warming)
            return f'Translation active - прогрів… ({names})'
        return 'Translation active'

    def _apply_pipeline_event(self, event: Dict):
        """Pipeline problems (rejected translations, service errors incl. a
        model not ready) in the log - GUI thread (marshalled by signal)."""
        if event.get("status") == "warmup_state":
            # «прогрів…» in the status line until every service is warm
            self._warming = [] if event.get("ready") else event.get("pending", [])
            self.status_manager.set_status(self._translation_status_text())
        line = self._event_log.lines(event)
        if line is None:
            return
        level, message = line
        if level == "ERROR":
            self.status_manager.log_error(message)
            self.status_manager.set_status(message)
        elif level == "WARN":
            self.status_manager.log_warning(message)
        else:
            self.status_manager.log_info(message)
    
    def on_refresh_requested(self):
        """Force a health poll of the remote services (off the GUI thread)."""
        if not self.ui_controller:
            return
        self._run_async(
            self.ui_controller.refresh_services,
            lambda _r: self.status_manager.log_info('Service health refreshed'),
        )

    def on_general_settings_changed(self, settings):
        """Apply EdgeSettings saved from the endpoints dialog (async)."""
        if not self.ui_controller or not isinstance(settings, EdgeSettings):
            return

        def done(ok):
            if ok is True:
                self.status_manager.log_info("Settings applied successfully")
                self._update_language_combo(
                    self.source_lang_combo, settings.source_language
                )
            else:
                err = self.ui_controller.last_error() or "unknown error"
                self.status_manager.log_error(f"Settings rejected: {err}")

        self._run_async(lambda: self.ui_controller.reconfigure_services(settings), done)

    def on_use_changed(self, kind, node):
        """Status panel: user picked the instance for ``kind`` (None = auto).

        Persisted to config.yml and applied at once (backends rebuilt)."""
        from .settings_dialog import apply_use

        settings, errors = apply_use(kind, node)
        if errors:
            self.status_manager.log_error(
                f"Вибір {kind} відхилено: {'; '.join(errors)}"
            )
            return
        self.status_manager.log_info(f"{kind}: використовувати {node or 'auto'}")
        self.on_general_settings_changed(settings)

    def on_settings_changed(self, settings):
        """Handle settings changes from the settings dialog - legacy method."""
        # This is now an alias to the new method
        self.on_general_settings_changed(settings)
        
    def _update_language_combo(self, combo, lang_code):
        """Update a language combo box to show the correct language."""
        lang_display_map = {
            'auto': 'Auto',
            'uk': 'Ukrainian (uk)',
            'pl': 'Polish (pl)',
            'en': 'English (en)',
            'ru': 'Russian (ru)',
            'de': 'German (de)',
            'fr': 'French (fr)',
            'es': 'Spanish (es)'
        }
        display_text = lang_display_map.get(lang_code, 'Auto')
        index = combo.findText(display_text)
        if index >= 0:
            combo.setCurrentIndex(index)
        
    def handle_status_update(self, message: str):
        """Status updates may come from worker threads; marshal to the GUI."""
        self._status_signal.emit(message)

    def _apply_status(self, message: str):
        self.status_manager.set_status(message)
        self.status_manager.log_info(message)

    def closeEvent(self, event):
        """Handle window close event."""
        if self.minimize_to_tray.isChecked():
            event.ignore()
            self.hide()
        else:
            self.quit_application()
            
    def quit_application(self):
        """Clean up and quit the application."""
        if self.ui_controller:
            # Stop event polling before cleanup
            self.ui_controller.stop_event_polling()
            self.ui_controller.cleanup()
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        # Use QApplication.quit() instead of sys.exit(0) as per context recommendations
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            # Fallback - though this should not happen in normal operation
            import sys
            sys.exit(0)