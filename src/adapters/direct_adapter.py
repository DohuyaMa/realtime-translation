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
        if not self._auto_spawn_services:
            return
        from ..core.config import get_config_manager
        app_cfg = get_config_manager()
        cfg = get_runtime_config()

        whisper_device = app_cfg.get('models.whisper.device', 'cuda')
        beam_size = app_cfg.get('models.whisper.beam_size', 5)
        temperature = app_cfg.get('models.whisper.temperature', 0.0)
        initial_prompt = app_cfg.get('models.whisper.initial_prompt', '')

        if self.use_wyoming:
            whisper_socket = cfg.get_hybrid_whisper_socket_path()
            whisper_module = 'src.whisper.hybrid_whisper_service'
            whisper_args = [
                '--socket-path', whisper_socket,
                '--use-wyoming',
                '--wyoming-host', self.wyoming_host,
                '--wyoming-port', str(self.wyoming_port),
            ]
        else:
            whisper_socket = cfg.get_whisper_socket_path()
            whisper_module = 'src.whisper.whisper_service'
            whisper_args = [
                '--socket-path', whisper_socket,
                '--device', whisper_device,
                '--beam-size', str(beam_size),
                '--temperature', str(temperature),
            ]
            if initial_prompt:
                whisper_args.extend(['--initial-prompt', initial_prompt])

        if self._need_spawn(whisper_socket):
            self._spawn_service('whisper', whisper_module, whisper_args)

        translate_socket = cfg.get_translate_socket_path()
        num_beams = app_cfg.get('models.translate.num_beams', 4)
        repetition_penalty = app_cfg.get('models.translate.repetition_penalty', 1.2)
        max_length = app_cfg.get('models.translate.max_length', 200)
        translate_args = [
            '--socket-path', translate_socket,
            '--num-beams', str(num_beams),
            '--repetition-penalty', str(repetition_penalty),
            '--max-length', str(max_length),
        ]
        if self._need_spawn(translate_socket):
            self._spawn_service('translate', 'src.translate.translate_service', translate_args)
        tts_socket = cfg.get_tts_socket_path()
        tts_voice = app_cfg.get('models.tts.voice', 'af_heart')
        tts_speed = app_cfg.get('models.tts.speed', 1.0)
        tts_args = [
            '--socket-path', tts_socket,
            '--voice', tts_voice,
            '--speed', str(tts_speed),
        ]
        if self._need_spawn(tts_socket):
            self._spawn_service('tts', 'src.tts.tts_service', tts_args)

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
        """Start a specific service."""
        try:
            # Check if the translation system has IPC clients available
            if (name == 'capture' and self.translation_system.capture_client is None) or \
               (name == 'whisper' and self.translation_system.whisper_client is None) or \
               (name == 'translate' and self.translation_system.translate_client is None) or \
               (name == 'tts' and self.translation_system.tts_client is None) or \
               (name == 'playback' and self.translation_system.playback_client is None):
                # In devShell mode, we may want to allow certain operations to continue
                # For services that can be started directly without IPC
                if name in ['capture', 'whisper', 'translate', 'tts', 'playback']:
                    logger.info(f"Service {name} not started (no IPC client in devShell mode)")
                    self._devshell_started.add(name)
                    return True
                else:
                    logger.warning(f"Cannot start {name} service: not recognized")
                    return False
            
            return self.translation_system.start_service(name)
        except Exception as e:
            logger.error(f"Failed to start {name} service: {e}")
            return False
    
    def stop_service(self, name: str) -> bool:
        """Stop a specific service."""
        try:
            # Check if the translation system has IPC clients available
            if (name == 'capture' and self.translation_system.capture_client is None) or \
               (name == 'whisper' and self.translation_system.whisper_client is None) or \
               (name == 'translate' and self.translation_system.translate_client is None) or \
               (name == 'tts' and self.translation_system.tts_client is None) or \
               (name == 'playback' and self.translation_system.playback_client is None):
                # In devShell mode, we may want to allow certain operations to continue
                # For services that can be stopped directly without IPC
                if name in ['capture', 'whisper', 'translate', 'tts', 'playback']:
                    logger.info(f"Service {name} not stopped (no IPC client in devShell mode)")
                    self._devshell_started.discard(name)
                    return True
                else:
                    logger.warning(f"Cannot stop {name} service: not recognized")
                    return False
            
            return self.translation_system.stop_service(name)
        except Exception as e:
            logger.error(f"Failed to stop {name} service: {e}")
            return False
    
    def get_status(self) -> Dict:
        """Get system status."""
        status = self.translation_system.get_stats()
        # In devShell mode all IPC clients are None; reflect manually-started services.
        devshell_mode = all(
            getattr(self.translation_system, f'{s}_client', None) is None
            for s in ('capture', 'whisper', 'translate', 'tts', 'playback')
        )
        if devshell_mode:
            for name in ('capture', 'whisper', 'translate', 'tts', 'playback'):
                status[f'{name}_connected'] = name in self._devshell_started
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