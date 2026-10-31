"""DirectAdapter: the edge app's backend, wrapping TranslationSystem."""

import dataclasses
from typing import Callable, Dict, List, Optional

from loguru import logger

from ..clients import ServiceHealthMonitor, WorkerSessions
from ..controller.controller import Device
from ..core.preflight.pipewire import PipeWirePreflight
from ..core.runtime import EdgeSettings, get_runtime_config
from ..translation_system import TranslationSystem


def _default_router():
    from ..audio.routing import AudioRouter

    return AudioRouter()


class DirectAdapter:
    def __init__(
        self,
        source_lang: str = "auto",
        target_lang: str = "en",
        sample_rate: int = 16000,
        skip_preflight: bool = False,
        settings: Optional[EdgeSettings] = None,
        translation_system_factory: Callable[..., TranslationSystem] = (
            TranslationSystem
        ),
        health_factory: Callable[..., ServiceHealthMonitor] = ServiceHealthMonitor,
        audio_router_factory: Callable[[], object] = _default_router,
    ):
        if not skip_preflight:
            logger.info("Running PipeWire preflight check...")
            if not PipeWirePreflight.check():
                raise RuntimeError(
                    "PipeWire preflight check failed: rt_virtual_input/"
                    "rt_virtual_output sinks not found. Enable "
                    "rt-translator.virtualSinks (Home Manager), "
                    "nixosModules.virtual-sinks, or run `nix develop`."
                )
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.sample_rate = sample_rate
        self.settings = settings or get_runtime_config()
        self.last_error: Optional[str] = None
        self._health_factory = health_factory
        self._monitor = None
        self._monitor_key = None
        self._monitor_running = False
        self._input_device: Optional[str] = None
        self._output_device: Optional[str] = None
        self.translation_system = translation_system_factory(self.settings)
        try:
            self.audio_router = audio_router_factory()
        except Exception as e:
            logger.warning("audio router unavailable: {}", e)
            self.audio_router = None

    # -- health -----------------------------------------------------------
    def _health(self):
        """Health monitor bound to the system's current backends."""
        ts = self.translation_system
        key = (ts.pools, ts.llm, ts.llm_target)
        stale = self._monitor_key is None or any(
            a is not b for a, b in zip(key, self._monitor_key)
        )
        if self._monitor is None or stale:
            was_running = bool(self._monitor and self._monitor_running)
            if self._monitor is not None:
                self._monitor.stop()
            sessions = ts.llm_target
            if not isinstance(sessions, WorkerSessions):
                sessions = None
            self._monitor = self._health_factory(ts.pools, ts.llm, sessions)
            self._monitor_key = key
            self._monitor_running = False
            if was_running:
                self._monitor.start()
                self._monitor_running = True
        return self._monitor

    def _start_health(self) -> None:
        monitor = self._health()
        monitor.start()
        self._monitor_running = True

    def _stop_health(self) -> None:
        if self._monitor is not None:
            self._monitor.stop()
        self._monitor_running = False

    # -- pipeline ---------------------------------------------------------
    def start_pipeline(self) -> bool:
        errors = self.settings.validate()
        if errors:
            self.last_error = "; ".join(errors)
            logger.error("Cannot start pipeline: {}", self.last_error)
            return False
        try:
            ok = self.translation_system.start()
        except Exception as e:
            self.last_error = str(e)
            logger.exception("Failed to start pipeline: {}", e)
            return False
        if not ok:
            self.last_error = self.translation_system.last_error or "start failed"
            return False
        self.last_error = None
        self._start_health()
        return True

    def stop_pipeline(self) -> bool:
        try:
            self.translation_system.stop()
            return True
        except Exception as e:
            logger.exception("Failed to stop pipeline: {}", e)
            return False
        finally:
            self._stop_health()

    def reconfigure_services(self, settings: EdgeSettings) -> bool:
        errors = settings.validate()
        if errors:
            self.last_error = "; ".join(errors)
            logger.error("Rejected service settings: {}", self.last_error)
            return False
        try:
            self.translation_system.apply_settings(settings)
        except Exception as e:
            self.last_error = str(e)
            logger.exception("Failed to apply settings: {}", e)
            return False
        was_running = self._monitor_running
        self.settings = settings
        ts = self.translation_system
        if was_running and not ts.is_running:
            self.last_error = ts.last_error or "restart failed"
            self._stop_health()
            return False
        if was_running:
            self._health()
        self.last_error = None
        logger.info("Speech services reconfigured: mode={}", settings.mode)
        return True

    def refresh_services(self) -> Dict:
        return self._health().poll_once()

    def get_status(self) -> Dict:
        return self.translation_system.get_stats()

    # -- languages ------------------------------------------------
    def set_languages(self, source_lang: str, target_lang: str = "en") -> bool:
        self.source_lang, self.target_lang = source_lang, target_lang
        new = dataclasses.replace(self.settings, source_language=source_lang)
        if new.validate():
            logger.warning("unsupported source language {!r}", source_lang)
            return False
        return self.reconfigure_services(new)

    # -- audio devices ----------------------------------------------------
    def _devices(self, key: str) -> List[Device]:
        if self.audio_router is None:
            return []
        result = []
        for info in self.audio_router.list_devices().get(key, []):
            if isinstance(info, dict):
                result.append(Device(info.get("name", ""), info.get("description", "")))
            else:
                result.append(Device(str(info), str(info)))
        return result

    def get_input_devices(self) -> List[Device]:
        return self._devices("inputs")

    def get_output_devices(self) -> List[Device]:
        return self._devices("outputs")

    def set_input_device(self, device_id: str) -> bool:
        if not device_id or device_id.lower() == "default":
            return True
        self._input_device = device_id
        self.translation_system.set_devices(self._input_device, self._output_device)
        if self.audio_router:
            self.audio_router.set_default_source(device_id)
        return True

    def set_output_device(self, device_id: str) -> bool:
        if not device_id or device_id.lower() == "default":
            return True
        self._output_device = device_id
        self.translation_system.set_devices(self._input_device, self._output_device)
        if self.audio_router:
            self.audio_router.set_default_sink(device_id)
        return True

    def get_audio_levels(self) -> Dict[str, float]:
        return {"input": self.translation_system._audio_level_input, "output": 0.0}

    def toggle_translation(self, enabled: bool) -> bool:
        mode = "auto" if enabled else "transcribe"
        return self.reconfigure_services(dataclasses.replace(self.settings, mode=mode))

    def cleanup(self) -> None:
        self._stop_health()
        self.translation_system.cleanup()
