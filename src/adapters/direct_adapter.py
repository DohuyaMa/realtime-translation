"""DirectAdapter: the edge app's backend, wrapping TranslationSystem."""

import dataclasses
import time
from typing import Callable, Dict, List, Optional

from loguru import logger

from ..clients import ServiceHealthMonitor, WorkerSessions
from ..clients.model_status import parse_status
from ..controller.controller import Device
from ..core.preflight.pipewire import PipeWirePreflight
from ..core.runtime import EdgeSettings, get_runtime_config
from ..translation_system import TranslationSystem

# POST /model/load|unload only start the transition on the service
MODEL_ACTION_TIMEOUT_S = 60.0
# «Запустити» waits this long for the local models before restarting the
# pipeline (a first load may download the model)
MODEL_READY_TIMEOUT_S = 600.0
MODEL_POLL_S = 0.5
STANDBY_ERROR = "система зупинена рубильником: спершу «Запустити»"


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
                    "rt_virtual_output sinks or the rt_virtual_mic source not"
                    " found. Enable "
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
        self._monitor_device: Optional[str] = None
        # kill switch: True after standby() until resume()
        self.in_standby = False
        self._resume_pipeline = False
        self._sleep = time.sleep
        self._clock = time.monotonic
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
        if self.in_standby:
            self.last_error = STANDBY_ERROR
            logger.error("Cannot start pipeline: {}", self.last_error)
            return False
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

    # -- kill switch ------------------------------------------------------
    def standby(self) -> List[str]:
        """«Зупинити все й звільнити пам'ять», in this order: stop capture and
        the pipeline, release the worker app session (the worker goes back to
        Django tasks), stop periodic discovery, unload THIS host's rt-asr /
        rt-vosk / rt-tts models (cluster nodes serve others; rt-vosk
        switched off in the settings is left alone). The llama worker's LLM
        stays loaded - it serves the Django queue. Every step runs; the
        failed ones are returned and logged, never swallowed."""
        ts = self.translation_system
        self._resume_pipeline = bool(ts.is_running)
        self.in_standby = True
        errors: List[str] = []
        if not self.stop_pipeline():
            errors.append("pipeline: stop failed (see log)")
        for name, step in (
            ("worker session", ts.release_session),
            ("discovery", ts.pause_background),
        ):
            try:
                step()
            except Exception as e:
                logger.exception("standby: {} failed: {}", name, e)
                errors.append(f"{name}: {e}")
        errors += self._model_action("unload")
        self.last_error = "; ".join(errors) or None
        logger.info(
            "standby: done{}", f" with errors: {self.last_error}" if errors else ""
        )
        return errors

    def resume(self) -> List[str]:
        """«Запустити» - standby in reverse: load this host's models (waits
        until they are ready), resume periodic discovery, and restart the
        pipeline if it was running at standby (that reopens the worker
        session and warms the LLM up)."""
        ts = self.translation_system
        errors = self._model_action("load")
        errors += self._wait_models_ready()
        try:
            ts.resume_background()
        except Exception as e:
            logger.exception("resume: discovery failed: {}", e)
            errors.append(f"discovery: {e}")
        self.in_standby = False
        if self._resume_pipeline and not self.start_pipeline():
            errors.append(f"pipeline: {self.last_error}")
        self._resume_pipeline = False
        self.last_error = "; ".join(errors) or None
        logger.info(
            "resume: done{}", f" with errors: {self.last_error}" if errors else ""
        )
        return errors

    def _model_action(self, action: str) -> List[str]:
        """POST /model/<action> on this host's rt-asr / rt-vosk / rt-tts."""
        errors: List[str] = []
        for kind, client in self.translation_system.local_model_clients().items():
            try:
                call = client.load_model if action == "load" else client.unload_model
                call(timeout_s=MODEL_ACTION_TIMEOUT_S)
                logger.info(
                    "{}: model {} requested on {}", kind, action, client.base_url
                )
            except Exception as e:
                logger.error("{}: model {} failed: {}", kind, action, e)
                errors.append(f"{kind}: model {action}: {e}")
            finally:
                client.close()
        return errors

    def _wait_models_ready(self) -> List[str]:
        """Poll this host's models until each is ready (or error/timeout)."""
        clients = self.translation_system.local_model_clients()
        pending = set(clients)
        errors: List[str] = []
        deadline = self._clock() + MODEL_READY_TIMEOUT_S
        try:
            while pending:
                for kind in sorted(pending):
                    try:
                        st = parse_status(clients[kind].get_status())
                    except Exception as e:
                        errors.append(f"{kind}: status: {e}")
                        pending.discard(kind)
                        continue
                    if st.state == "ready":
                        pending.discard(kind)
                    elif st.state in ("error", "unloaded"):
                        errors.append(f"{kind}: model {st.state}: {st.error or ''}")
                        pending.discard(kind)
                if pending and self._clock() >= deadline:
                    errors += [
                        f"{k}: model not ready after {MODEL_READY_TIMEOUT_S:.0f} s"
                        for k in sorted(pending)
                    ]
                    break
                if pending:
                    self._sleep(MODEL_POLL_S)
        finally:
            for client in clients.values():
                client.close()
        return errors

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

    def set_event_callback(self, cb: Optional[Callable[[Dict], None]]) -> None:
        """Pipeline status events (errors, rejected translations...); called
        from pipeline threads."""
        self.translation_system.set_status_callback(cb)

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

    def _set_devices(self) -> None:
        self.translation_system.set_devices(
            self._input_device, self._output_device, self._monitor_device
        )

    def set_input_device(self, device_id: Optional[str]) -> bool:
        """Source the app records (``None``/"" = the default source). The
        app captures it directly - the system default mic stays, so other
        apps' microphone never changes. A capture running now switches at
        once; a failure keeps the old source and sets ``last_error``."""
        old = self._input_device
        self._input_device = device_id or None
        try:
            self._set_devices()
        except Exception as e:
            self._input_device = old
            self.last_error = str(e)
            logger.error("Cannot capture from {}: {}", device_id, e)
            return False
        return True

    def set_output_device(self, device_id: Optional[str]) -> bool:
        """Sink the translation is played into (``None``/"" = the default
        sink). Only the app's own playback moves - the system default sink
        stays, so other apps' audio never ends up in the virtual mic."""
        self._output_device = device_id or None
        self._set_devices()
        return True

    def set_monitor_device(self, device_id: Optional[str]) -> bool:
        """«Також чути в навушниках»: a second sink played in sync with the
        output (``None`` = off)."""
        self._monitor_device = device_id or None
        self._set_devices()
        return True

    def get_audio_levels(self) -> Dict[str, float]:
        return {"input": self.translation_system._audio_level_input, "output": 0.0}

    def toggle_translation(self, enabled: bool) -> bool:
        mode = "auto" if enabled else "transcribe"
        return self.reconfigure_services(dataclasses.replace(self.settings, mode=mode))

    def cleanup(self) -> None:
        self._stop_health()
        self.translation_system.cleanup()
