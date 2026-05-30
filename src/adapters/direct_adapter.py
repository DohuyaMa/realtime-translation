"""Direct adapter that wraps the existing TranslationSystem."""
import os
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Dict, List
from loguru import logger

from ..translation_system import TranslationSystem
from ..controller.controller import Device
from ..core.preflight.pipewire import PipeWirePreflight
from ..core.env import setup_ml_env
from ..core.runtime import get_runtime_config


class DirectAdapter:
    """Direct adapter that wraps the existing TranslationSystem implementation."""
    
    def __init__(self, source_lang: str = "auto", target_lang: str = "en", sample_rate: int = 16000, skip_preflight: bool = False, use_wyoming: bool = False, wyoming_host: str = "localhost", wyoming_port: int = 10300, auto_spawn_services: bool = True):
        # Check PipeWire availability before proceeding (unless explicitly skipped)
        if not skip_preflight:
            logger.info("Running PipeWire preflight check...")
            if not PipeWirePreflight.check():
                logger.error("PipeWire preflight check failed — virtual sinks not found")
                raise RuntimeError(
                    "PipeWire preflight check failed. "
                    "Ensure PipeWire virtual sinks are set up: "
                    "run `systemctl --user start rt-virtual-sinks` or `nix develop`"
                )
            logger.info("PipeWire preflight check passed")
        else:
            logger.warning("PipeWire preflight check skipped via skip_preflight=True")
        
        # Set up environment variables
        setup_ml_env()
        logger.info(f"Initializing DirectAdapter: {source_lang}->{target_lang} "
                     f"wyoming={use_wyoming} sr={sample_rate}Hz")
        self.translation_system = TranslationSystem(
            source_lang=source_lang,
            target_lang=target_lang,
            sample_rate=sample_rate,
            use_wyoming=use_wyoming,
            wyoming_host=wyoming_host,
            wyoming_port=wyoming_port
        )
        
        # Store configuration for Wyoming service
        self.use_wyoming = use_wyoming
        self.wyoming_host = wyoming_host
        self.wyoming_port = wyoming_port
        
        # Store reference to audio router for direct device management in dev mode
        self.audio_router = self.translation_system.audio_router

        # Track which services have been "started" in devShell mode (no IPC clients)
        self._devshell_started: set = set()

        self._service_processes: Dict[str, subprocess.Popen] = {}
        self._auto_spawn_services = auto_spawn_services

    def reconfigure_wyoming(self, use_wyoming: bool, wyoming_host: str = "localhost", wyoming_port: int = 10300):
        """Reconfigure the adapter to use Wyoming services or local services."""
        try:
            # Store new Wyoming settings
            old_wyoming = self.use_wyoming
            self.use_wyoming = use_wyoming
            self.wyoming_host = wyoming_host
            self.wyoming_port = wyoming_port
            
            # Get current pipeline status
            current_status = self.translation_system.get_stats()
            was_running = current_status.get('running', False)
            
            logger.info(f"Reconfiguring: wyoming {old_wyoming}→{use_wyoming} "
                         f"({wyoming_host}:{wyoming_port}) "
                         f"pipeline_running={was_running}")
            
            if was_running:
                self.stop_pipeline()
            
            # Store current language settings
            source_lang = self.translation_system.source_lang
            target_lang = self.translation_system.target_lang
            sample_rate = self.translation_system.sample_rate
            
            # Clean up the current translation system
            self.translation_system.cleanup()
            
            # Create a new translation system with the new Wyoming configuration
            from ..translation_system import TranslationSystem
            self.translation_system = TranslationSystem(
                source_lang=source_lang,
                target_lang=target_lang,
                sample_rate=sample_rate,
                use_wyoming=use_wyoming,
                wyoming_host=wyoming_host,
                wyoming_port=wyoming_port
            )
            
            self.audio_router = self.translation_system.audio_router

            if was_running:
                self.start_pipeline()

            logger.info(f"Wyoming reconfiguration completed (wyoming={use_wyoming})")
            return True
        except Exception as e:
            logger.exception(f"Failed to reconfigure Wyoming settings: {e}")
            return False

    # ------------------------------------------------------------------
    # Service subprocess lifecycle
    # ------------------------------------------------------------------

    _MODULE_ENTRYPOINTS = {
        'src.capture.capture_service':          'translator-capture',
        'src.playback.playback_service':        'translator-playback',
        'src.whisper.whisper_service':          'translator-whisper',
        'src.whisper.hybrid_whisper_service':   'translator-hybrid-whisper',
        'src.translate.translate_service':      'translator-translate',
        'src.tts.tts_service':                  'translator-tts',
    }

    # systemd user units (systemctl --user)
    _USER_UNITS: Dict[str, str] = {
        'capture':        'rt-capture',
        'whisper':        'rt-whisper',
        'hybrid-whisper': 'rt-hybrid-whisper',
        'translate':      'rt-translate',
        'tts':            'rt-tts',
        'playback':       'rt-playback',
    }
    # systemd system units (sudo systemctl)
    _SYSTEM_UNITS: Dict[str, str] = {
        'wyoming': 'wyoming-faster-whisper-main',
    }

    def _whisper_unit(self) -> str:
        return 'rt-hybrid-whisper' if self.use_wyoming else 'rt-whisper'

    def _systemctl_user(self, action: str, unit: str) -> bool:
        try:
            subprocess.run(
                ['systemctl', '--user', action, unit],
                check=True, capture_output=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error("systemctl --user {} {} failed: {}", action, unit, e.stderr.decode())
            return False

    def _systemctl_system(self, action: str, unit: str) -> bool:
        try:
            subprocess.run(
                ['sudo', 'systemctl', action, unit],
                check=True, capture_output=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error("sudo systemctl {} {} failed: {}", action, unit, e.stderr.decode())
            return False

    def get_service_active(self, name: str) -> bool:
        """Check if a service is active via systemctl."""
        if name in self._SYSTEM_UNITS:
            unit = self._SYSTEM_UNITS[name]
            result = subprocess.run(
                ['systemctl', 'is-active', '--quiet', unit],
                capture_output=True,
            )
            return result.returncode == 0
        unit = self._USER_UNITS.get(name)
        if name == 'whisper':
            unit = self._whisper_unit()
        if not unit:
            return False
        result = subprocess.run(
            ['systemctl', '--user', 'is-active', '--quiet', unit],
            capture_output=True,
        )
        return result.returncode == 0

    def _resolve_cmd(self, module: str) -> list:
        """Prefer installed entry-point scripts over python -m <module>."""
        entrypoint = self._MODULE_ENTRYPOINTS.get(module)
        if entrypoint:
            # Sibling script in same bin dir as the running translator-ui
            bin_dir = pathlib.Path(sys.argv[0]).resolve().parent
            ep_path = bin_dir / entrypoint
            if ep_path.exists():
                return [str(ep_path)]
            found = shutil.which(entrypoint)
            if found:
                return [found]
        return [sys.executable, '-m', module]

    def _spawn_service(self, name: str, module: str, args: List[str]) -> None:
        cmd = self._resolve_cmd(module) + args
        env = os.environ.copy()
        # Nix wraps scripts via shell; the raw sys.executable doesn't inherit
        # the package's PYTHONPATH. Propagate sys.path explicitly so subprocesses
        # can find the installed `src` package and its dependencies.
        python_path_entries = [p for p in sys.path if p]
        if python_path_entries:
            existing = env.get('PYTHONPATH', '')
            combined = ':'.join(python_path_entries)
            env['PYTHONPATH'] = f"{combined}:{existing}" if existing else combined
        logger.info(f"Spawning {name} service: {' '.join(cmd)}")
        process = subprocess.Popen(cmd, env=env)
        self._service_processes[name] = process

    @staticmethod
    def _socket_is_live(path: str) -> bool:
        """Return True only if a UNIX socket file exists AND has an active listener."""
        import socket as _socket
        if not os.path.exists(path):
            return False
        try:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect(path)
            s.close()
            return True
        except OSError:
            return False

    def _need_spawn(self, socket_path: str) -> bool:
        if self._socket_is_live(socket_path):
            return False
        if os.path.exists(socket_path):
            logger.warning(f"Removing stale socket: {socket_path}")
            try:
                os.unlink(socket_path)
            except OSError:
                pass
        return True

    def _ensure_essential_services(self) -> None:
        """Start any services that are not yet running via systemctl --user."""
        cfg = get_runtime_config()
        whisper_unit = self._whisper_unit()
        whisper_socket = (cfg.get_hybrid_whisper_socket_path()
                          if self.use_wyoming else cfg.get_whisper_socket_path())

        for name, unit, socket_path in [
            ('whisper',   whisper_unit,   whisper_socket),
            ('translate', 'rt-translate', cfg.get_translate_socket_path()),
            ('tts',       'rt-tts',       cfg.get_tts_socket_path()),
        ]:
            if not self._socket_is_live(socket_path):
                logger.info("Starting {} via systemctl --user ...", name)
                self._systemctl_user('start', unit)

    def _wait_for_services(self, timeout: float = 120.0) -> None:
        if not self._auto_spawn_services:
            return
        spawn_names = set(self._service_processes.keys())
        if not spawn_names:
            return
        cfg = get_runtime_config()
        check = []
        if 'whisper' in spawn_names:
            check.append(('whisper', cfg.get_hybrid_whisper_socket_path()
                         if self.use_wyoming else cfg.get_whisper_socket_path()))
        if 'translate' in spawn_names:
            check.append(('translate', cfg.get_translate_socket_path()))
        if 'tts' in spawn_names:
            check.append(('tts', cfg.get_tts_socket_path()))
        start = time.monotonic()
        for name, path in check:
            while not os.path.exists(path):
                elapsed = time.monotonic() - start
                if elapsed > timeout:
                    logger.warning(f"Timed out ({elapsed:.0f}s) waiting for {name} socket: {path}")
                    break
                time.sleep(0.5)

    def _stop_subprocesses(self) -> None:
        if not self._service_processes:
            return
        logger.info(f"Terminating {len(self._service_processes)} service subprocess(es)...")
        for name, proc in self._service_processes.items():
            try:
                proc.terminate()
                proc.wait(timeout=5)
                logger.debug(f"{name} service subprocess terminated")
            except subprocess.TimeoutExpired:
                proc.kill()
                logger.warning(f"{name} service subprocess killed (SIGKILL)")
            except Exception:
                pass
        self._service_processes.clear()
        logger.info("All service subprocesses terminated")

    def start_pipeline(self) -> bool:
        try:
            logger.info("Starting translation pipeline...")
            self._ensure_essential_services()
            self._wait_for_services()
            self.translation_system.start()
            logger.info("Translation pipeline started")
            return True
        except Exception as e:
            logger.exception(f"Failed to start pipeline: {e}")
            return False
    
    def stop_pipeline(self) -> bool:
        try:
            logger.info("Stopping translation pipeline...")
            self.translation_system.stop()
            self._stop_subprocesses()
            logger.info("Translation pipeline stopped")
            return True
        except Exception as e:
            logger.exception(f"Failed to stop pipeline: {e}")
            return False
    
    def start_service(self, name: str) -> bool:
        """Start a systemd service (user or system level)."""
        try:
            if name in self._SYSTEM_UNITS:
                return self._systemctl_system('start', self._SYSTEM_UNITS[name])
            unit = self._whisper_unit() if name == 'whisper' else self._USER_UNITS.get(name)
            if not unit:
                logger.warning("Unknown service: {}", name)
                return False
            return self._systemctl_user('start', unit)
        except Exception as e:
            logger.error("Failed to start {} service: {}", name, e)
            return False

    def stop_service(self, name: str) -> bool:
        """Stop a systemd service and its socket (to prevent socket-activation restart)."""
        try:
            if name in self._SYSTEM_UNITS:
                return self._systemctl_system('stop', self._SYSTEM_UNITS[name])
            unit = self._whisper_unit() if name == 'whisper' else self._USER_UNITS.get(name)
            if not unit:
                logger.warning("Unknown service: {}", name)
                return False
            # Stop socket first to prevent immediate socket-activation restart
            self._systemctl_user('stop', f'{unit}.socket')
            return self._systemctl_user('stop', unit)
        except Exception as e:
            logger.error("Failed to stop {} service: {}", name, e)
            return False

    def restart_service(self, name: str) -> bool:
        """Restart a systemd service (user or system level)."""
        try:
            if name in self._SYSTEM_UNITS:
                return self._systemctl_system('restart', self._SYSTEM_UNITS[name])
            unit = self._whisper_unit() if name == 'whisper' else self._USER_UNITS.get(name)
            if not unit:
                logger.warning("Unknown service for restart: {}", name)
                return False
            ok = self._systemctl_user('restart', unit)
            logger.info("Restarted {} (unit={}): {}", name, unit, "ok" if ok else "failed")
            return ok
        except Exception as e:
            logger.error("Failed to restart {} service: {}", name, e)
            return False

    def restart_all_services(self) -> Dict[str, bool]:
        """Restart all pipeline services and reconnect IPC clients."""
        results: Dict[str, bool] = {}
        for name in ['translate', 'tts', 'whisper', 'capture', 'playback']:
            results[name] = self.restart_service(name)
        time.sleep(2.0)
        self.reconnect_ipc()
        logger.info("restart_all_services results: {}", results)
        return results

    def change_whisper_model(
        self,
        model_name: str,
        progress_cb=None,
    ) -> bool:
        """Download model if needed, persist to config, restart whisper service.

        Args:
            model_name: One of tiny/base/small/medium/large
            progress_cb: Optional callable(pct: int, msg: str) for progress reporting

        Returns:
            True on success, False on failure
        """
        from ..core.models import ModelManager, ModelStatus
        from ..core.config import get_config_manager

        def _report(pct: int, msg: str):
            logger.info(f"[change_whisper_model] {pct}%  {msg}")
            if progress_cb:
                progress_cb(pct, msg)

        _report(0, f"Checking model '{model_name}'...")
        manager = ModelManager()
        info = manager.get_info("whisper", model_name)

        if info.status == ModelStatus.MISSING:
            _report(5, f"Model '{model_name}' not cached — downloading...")
            ok = manager.download(
                "whisper",
                model_name,
                progress_cb=lambda pct, msg: _report(5 + int(pct * 0.85), msg),
            )
            if not ok:
                _report(-1, f"Download failed for '{model_name}'")
                return False
            _report(90, f"Model '{model_name}' downloaded.")
        else:
            _report(90, f"Model '{model_name}' already cached.")

        # Persist to config
        try:
            cfg = get_config_manager()
            cfg.set("models.whisper.model", model_name)
            cfg.save()
            _report(93, f"Config saved: models.whisper.model = {model_name}")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            _report(-1, f"Config save failed: {e}")
            return False

        # Restart whisper service so it picks up the new model
        _report(95, "Restarting rt-whisper service...")
        ok = self.restart_service("whisper")
        if ok:
            _report(100, f"Whisper service restarted with model '{model_name}'")
        else:
            _report(-1, "Service restart failed — check systemd logs")
        return ok

    def reconnect_ipc(self) -> int:
        """Force-reconnect all IPC clients. Returns number newly connected."""
        # Clear stale clients first
        for name, attr, path in self.translation_system._ipc_service_map():
            client = getattr(self.translation_system, attr)
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass
            setattr(self.translation_system, attr, None)
        n = self.translation_system.reconnect_ipc_clients()
        logger.info("reconnect_ipc: {} client(s) connected", n)
        return n
    
    def get_status(self) -> Dict:
        """Get system status including real systemd service states."""
        status = self.translation_system.get_stats()
        for name in list(self._USER_UNITS) + list(self._SYSTEM_UNITS):
            status[f'{name}_connected'] = self.get_service_active(name)
        # 'whisper' key reflects whichever whisper unit is in use
        status['whisper_connected'] = self.get_service_active('whisper')
        return status
    
    def set_languages(self, source_lang: str, target_lang: str = "en") -> bool:
        """Set source and target languages."""
        try:
            # Check if the translation system has IPC clients available
            # If not, we can't set languages through the services
            if self.translation_system.whisper_client is None and self.translation_system.translate_client is None:
                logger.info(f"Setting languages in devShell mode: {source_lang} -> {target_lang}")
                # Still allow setting at the system level even if services are not available
                self.translation_system.source_lang = source_lang
                self.translation_system.target_lang = target_lang
                return True
            
            self.translation_system.set_languages(source_lang, target_lang)
            return True
        except Exception as e:
            logger.error(f"Failed to set languages: {e}")
            return False
    
    def get_input_devices(self) -> List[Device]:
        """Get available input devices."""
        devices = self.translation_system.get_audio_devices()
        input_devices = []
        for device_info in devices.get('inputs', []):
            # Assuming the device_info is a dict with 'name' and 'description' keys
            if isinstance(device_info, dict):
                input_devices.append(Device(device_info.get('name', ''), device_info.get('description', '')))
            else:
                # If it's not a dict, assume it's already a device dict with name and description
                input_devices.append(Device(str(device_info), str(device_info)))
        return input_devices
    
    def get_output_devices(self) -> List[Device]:
        """Get available output devices."""
        devices = self.translation_system.get_audio_devices()
        output_devices = []
        for device_info in devices.get('outputs', []):
            if isinstance(device_info, dict):
                output_devices.append(Device(device_info.get('name', ''), device_info.get('description', '')))
            else:
                output_devices.append(Device(str(device_info), str(device_info)))
        return output_devices
    
    def set_input_device(self, device_id: str) -> bool:
        """Set the input device."""
        if not device_id or device_id.lower() == "default":
            return True   # placeholder — nothing to set
        try:
            # Store user's choice so _pipeline_loop uses it via sounddevice
            self.translation_system._preferred_input_device_name = device_id
            # Also try IPC so the standalone capture service knows (if it ever supports it)
            if self.translation_system.capture_client is not None:
                self.translation_system.set_input_device(device_id)
            if self.audio_router:
                self.audio_router.set_default_source(device_id)
            logger.info(f"Input device set to: {device_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to set input device: {e}")
            return False

    def set_output_device(self, device_id: str) -> bool:
        """Set the output device."""
        if not device_id or device_id.lower() == "default":
            return True   # placeholder — nothing to set
        try:
            # Store user's choice so TTS playback uses it via sounddevice
            self.translation_system._preferred_output_device_name = device_id
            # Also set PulseAudio default sink via audio router
            if self.audio_router:
                self.audio_router.set_default_sink(device_id)
                logger.info(f"Output device set to: {device_id}")
                return True
            logger.error("Audio router not available")
            return False
        except Exception as e:
            logger.error(f"Failed to set output device: {e}")
            return False
    
    def get_audio_levels(self) -> Dict[str, float]:
        """Get current audio input/output levels."""
        return {
            'input': self.translation_system._audio_level_input,
            'output': 0.0,
        }
    
    def toggle_translation(self, enabled: bool) -> bool:
        """Enable or disable translation."""
        try:
            self.translation_system.toggle_translation(enabled)
            return True
        except Exception as e:
            logger.error(f"Failed to toggle translation: {e}")
            return False
    
    def cleanup(self) -> None:
        self._stop_subprocesses()
        self.translation_system.cleanup()