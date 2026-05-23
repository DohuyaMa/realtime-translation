"""Main coordinator for the real-time translation system using modular architecture."""

import base64
import json
import os
import socket
import struct
import threading
from typing import Optional, Dict, List
from loguru import logger
import numpy as np

from .audio.routing import AudioRouter
from .common.ipc import IPCClient
from .core.runtime import get_runtime_config


class WhisperSocketClient:
    """Client for whisper/hybrid-whisper using their native little-endian length-prefixed protocol.

    Protocol: each frame = 4-byte little-endian length + payload.
    Control frames are JSON; audio frames are raw int16 PCM bytes.
    Results arrive as newline-delimited JSON on the same connection after "stop".
    """

    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def _send_frame(self, sock: socket.socket, data: bytes) -> None:
        sock.sendall(struct.pack("<I", len(data)) + data)

    def transcribe(self, audio_int16: np.ndarray, language: Optional[str] = None) -> List[str]:
        """Send audio and return list of transcribed text segments.

        Opens a fresh connection per call (required by whisper's session model).
        """
        if not os.path.exists(self.socket_path):
            logger.debug(f"Whisper socket not found: {self.socket_path}")
            return []

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_path)
        except OSError as e:
            logger.error(f"Cannot connect to whisper at {self.socket_path}: {e}")
            return []

        texts: List[str] = []
        try:
            start_cmd: Dict = {"cmd": "start"}
            if language and language != "auto":
                start_cmd["language"] = language
            self._send_frame(sock, json.dumps(start_cmd).encode())

            raw = audio_int16.tobytes()
            for i in range(0, len(raw), 4096):
                self._send_frame(sock, raw[i:i + 4096])

            self._send_frame(sock, json.dumps({"cmd": "stop"}).encode())

            sock.settimeout(15.0)
            buf = b""
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if line:
                            try:
                                msg = json.loads(line)
                                if msg.get("type") == "segment":
                                    text = msg.get("text", "").strip()
                                    if text:
                                        texts.append(text)
                            except Exception:
                                pass
            except socket.timeout:
                pass
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
        finally:
            try:
                sock.close()
            except Exception:
                pass

        return texts


def _try_connect(client: IPCClient) -> bool:
    try:
        client.connect()
        return True
    except Exception:
        return False


class TranslationSystem:
    """Main coordinator for the real-time translation system."""

    def __init__(
        self,
        source_lang: str = "auto",
        target_lang: str = "en",
        sample_rate: int = 16000,
        use_wyoming: bool = False,
        wyoming_host: str = "localhost",
        wyoming_port: int = 10300,
    ):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.sample_rate = sample_rate
        self.use_wyoming = use_wyoming
        self.wyoming_host = wyoming_host
        self.wyoming_port = wyoming_port

        self.audio_router: Optional[AudioRouter] = None

        # IPC clients — whisper has its own protocol, tracked separately
        self.capture_client: Optional[IPCClient] = None
        self.whisper_client: Optional[IPCClient] = None   # kept for API compat
        self.translate_client: Optional[IPCClient] = None
        self.tts_client: Optional[IPCClient] = None
        self.playback_client: Optional[IPCClient] = None

        self.is_running = False
        self.translation_enabled = True
        self.status_callback: Optional[callable] = None

        self._capture_thread: Optional[threading.Thread] = None

        # Audio level (updated every ~20 ms from capture callback)
        self._audio_level_input: float = 0.0

        # Pending recognized/translated text — consumed by get_stats() poll
        self._pending_recognized: List[str] = []
        self._pending_translated: List[str] = []
        self._pipeline_lock = threading.Lock()

        self._initialize_components()
        logger.info(f"Translation system initialized: {source_lang}->{target_lang}")

    # ------------------------------------------------------------------
    # Initialisation / reconnection
    # ------------------------------------------------------------------

    def _initialize_components(self):
        try:
            self.audio_router = AudioRouter()
            input_device, output_device = self.audio_router.get_virtual_devices()
            logger.info(f"Using virtual audio devices: {input_device}, {output_device}")
        except Exception as e:
            logger.error(f"Failed to initialize AudioRouter: {e}")
            raise

        self._connect_ipc_clients()

    def _ipc_service_map(self):
        cfg = get_runtime_config()
        return [
            ("capture",   "capture_client",   cfg.get_capture_socket_path()),
            ("translate", "translate_client", cfg.get_translate_socket_path()),
            ("tts",       "tts_client",       cfg.get_tts_socket_path()),
            ("playback",  "playback_client",  cfg.get_playback_socket_path()),
        ]

    def _connect_ipc_clients(self):
        """Try to connect to each service; leave as None if socket unavailable."""
        for name, attr, path in self._ipc_service_map():
            if getattr(self, attr) is None and os.path.exists(path):
                client = IPCClient(path)
                if _try_connect(client):
                    setattr(self, attr, client)
                    logger.info(f"Connected to {name} service")
                else:
                    logger.debug(f"{name} socket exists but connection refused")
            elif not os.path.exists(path):
                logger.debug(f"{name} socket not found: {path}")

    def _whisper_socket_path(self) -> str:
        cfg = get_runtime_config()
        return (cfg.get_hybrid_whisper_socket_path()
                if self.use_wyoming else cfg.get_whisper_socket_path())

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start(self):
        if self.is_running:
            logger.warning("Translation system already running")
            return

        # Reconnect to any services that came up since last attempt
        self._connect_ipc_clients()

        self.is_running = True
        self._capture_thread = threading.Thread(
            target=self._pipeline_loop, daemon=True, name="rt-pipeline"
        )
        self._capture_thread.start()
        logger.info("Translation system started")

    def stop(self):
        if not self.is_running:
            return
        self.is_running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=6.0)
            self._capture_thread = None
        logger.info("Translation system stopped")

    # ------------------------------------------------------------------
    # Device selection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_input_device() -> Optional[int]:
        """Pick the best mic: prefer PulseAudio/PipeWire names over raw ALSA."""
        import sounddevice as sd
        preferred = ["Stereo Microphone", "Digital Microphone", "Built-in Microphone",
                     "Microphone", "Mono Microphone"]
        devices = sd.query_devices()
        for pattern in preferred:
            for d in devices:
                if d["max_input_channels"] > 0 and pattern.lower() in d["name"].lower():
                    logger.info(f"Selected input device [{d['index']}]: {d['name']}")
                    return d["index"]
        default = sd.query_devices(kind="input")
        logger.info(f"Using default input device: {default['name']}")
        return None

    @staticmethod
    def _find_output_device() -> Optional[int]:
        """Pick the best speaker: prefer PulseAudio/PipeWire names over raw ALSA."""
        import sounddevice as sd
        preferred = ["Analog Stereo", "Speaker", "Headphone", "Internal Audio"]
        devices = sd.query_devices()
        for pattern in preferred:
            for d in devices:
                if d["max_output_channels"] > 0 and pattern.lower() in d["name"].lower():
                    logger.info(f"Selected output device [{d['index']}]: {d['name']}")
                    return d["index"]
        default = sd.query_devices(kind="output")
        logger.info(f"Using default output device: {default['name']}")
        return None

    # ------------------------------------------------------------------
    # Pipeline loop
    # ------------------------------------------------------------------

    def _pipeline_loop(self):
        """Capture via sounddevice at 48 kHz (PipeWire native), downsample to
        16 kHz, chunk into 3-second windows, and drive the full pipeline.

        pyaudio + ALSA is avoided because the ALSA-PipeWire bridge module path
        differs between build-time and run-time Nix store entries, causing
        ALSA to reject the 16 kHz sample rate.
        """
        import queue as _queue
        import sounddevice as sd

        CAPTURE_RATE = 48000          # PipeWire native rate
        WHISPER_RATE = self.sample_rate  # 16000
        CHUNK_SEC = 3.0
        chunk_frames = int(CHUNK_SEC * CAPTURE_RATE)

        audio_q: _queue.Queue = _queue.Queue()

        def _cb(indata, frames, cb_time, status):
            if status:
                logger.debug(f"Sounddevice input status: {status}")
            mono = indata[:, 0]
            # RMS → 0..1 range (speech typically 0.01–0.3 full-scale)
            rms = float(np.sqrt(np.mean(mono ** 2)))
            self._audio_level_input = min(rms * 5.0, 1.0)
            audio_q.put(mono.copy())

        input_device = TranslationSystem._find_input_device()
        try:
            stream = sd.InputStream(
                samplerate=CAPTURE_RATE,
                channels=1,
                dtype="float32",
                blocksize=1024,
                callback=_cb,
                device=input_device,
            )
            stream.start()
        except Exception as e:
            logger.error(f"Cannot open microphone: {e}")
            self.is_running = False
            return

        logger.info(f"Microphone capture started at {CAPTURE_RATE} Hz")
        buf = np.empty(0, dtype=np.float32)
        try:
            while self.is_running:
                try:
                    chunk = audio_q.get(timeout=0.5)
                    buf = np.append(buf, chunk)
                except _queue.Empty:
                    continue

                if len(buf) >= chunk_frames:
                    segment = buf[:chunk_frames]
                    buf = buf[chunk_frames:]

                    # Downsample float32 48k→16k via linear interpolation
                    new_len = int(len(segment) * WHISPER_RATE / CAPTURE_RATE)
                    resampled = np.interp(
                        np.linspace(0, len(segment) - 1, new_len),
                        np.arange(len(segment)),
                        segment,
                    )
                    audio_int16 = np.clip(resampled * 32767, -32768, 32767).astype(np.int16)

                    if self.translation_enabled:
                        threading.Thread(
                            target=self._process_chunk,
                            args=(audio_int16,),
                            daemon=True,
                        ).start()
        finally:
            stream.stop()
            stream.close()
            logger.info("Microphone capture stopped")

    def _process_chunk(self, audio_int16: np.ndarray):
        """whisper → translate → TTS → sounddevice playback."""
        whisper = WhisperSocketClient(self._whisper_socket_path())
        lang = None if self.source_lang == "auto" else self.source_lang
        texts = whisper.transcribe(audio_int16, language=lang)

        for text in texts:
            if not text:
                continue
            logger.info(f"Recognized: {text}")
            with self._pipeline_lock:
                self._pending_recognized.append(text)

            translated = self._translate(text)
            if not translated:
                continue
            logger.info(f"Translated: {translated}")
            with self._pipeline_lock:
                self._pending_translated.append(translated)

            self._speak(translated)

            if self.status_callback:
                self.status_callback({
                    "status": "translation_complete",
                    "original_text": text,
                    "translated_text": translated,
                })

    def _translate(self, text: str) -> Optional[str]:
        if not self.translate_client:
            return None
        try:
            result = self.translate_client.send_message("translate_text", {"text": text})
            if result and result.get("status") == "success":
                return result["data"]["translated_text"]
            logger.warning(f"Translate service returned: {result}")
        except Exception as e:
            logger.error(f"Translate error: {e}")
            self.translate_client = None   # force reconnect next cycle
        return None

    def _speak(self, text: str):
        if not self.tts_client:
            return
        try:
            result = self.tts_client.send_message("synthesize_text", {"text": text})
            if not (result and result.get("status") == "success"):
                logger.warning(f"TTS service returned: {result}")
                return
            audio_b64 = result["data"]["audio_data"]
            audio_arr = np.frombuffer(base64.b64decode(audio_b64), dtype=np.float32)
            samplerate = result["data"].get("sample_rate", 24000)
            try:
                import sounddevice as sd
                out_dev = TranslationSystem._find_output_device()
                sd.play(audio_arr, samplerate=samplerate, device=out_dev, blocking=True)
            except Exception as e:
                logger.error(f"Playback error: {e}")
        except Exception as e:
            logger.error(f"TTS error: {e}")
            self.tts_client = None   # force reconnect next cycle

    # ------------------------------------------------------------------
    # Service control (IPC, forwarded)
    # ------------------------------------------------------------------

    def start_service(self, service_name: str) -> bool:
        try:
            if service_name == "capture" and self.capture_client:
                self.capture_client.send_message("start_capture", {})
            return True
        except Exception as e:
            logger.error(f"Failed to start {service_name}: {e}")
            return False

    def stop_service(self, service_name: str) -> bool:
        try:
            if service_name == "capture" and self.capture_client:
                self.capture_client.send_message("stop_capture", {})
            return True
        except Exception as e:
            logger.error(f"Failed to stop {service_name}: {e}")
            return False

    def set_languages(self, source_lang: str, target_lang: str = "en"):
        self.source_lang = source_lang
        self.target_lang = target_lang
        if self.translate_client and source_lang != "auto":
            try:
                self.translate_client.send_message("set_languages", {
                    "data": {"source_lang": source_lang, "target_lang": target_lang}
                })
            except Exception as e:
                logger.error(f"Failed to update translate languages: {e}")
        logger.info(f"Languages updated: {source_lang}->{target_lang}")

    def toggle_translation(self, enabled: bool):
        self.translation_enabled = enabled
        logger.info(f"Translation {'enabled' if enabled else 'disabled'}")

    def set_status_callback(self, callback: callable):
        self.status_callback = callback

    # ------------------------------------------------------------------
    # Audio devices
    # ------------------------------------------------------------------

    def get_audio_devices(self) -> Dict:
        if self.audio_router:
            return self.audio_router.list_devices()
        return {"inputs": {}, "outputs": {}}

    def set_input_device(self, device_name: str):
        if self.capture_client:
            try:
                return self.capture_client.send_message(
                    "set_input_device", {"device_name": device_name}
                )
            except Exception as e:
                logger.error(f"Failed to set input device: {e}")
        return None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict:
        cfg = get_runtime_config()
        whisper_path = self._whisper_socket_path()
        stats: Dict = {
            "running": self.is_running,
            "translation_enabled": self.translation_enabled,
            "source_language": self.source_lang,
            "target_language": self.target_lang,
            "whisper_connected": os.path.exists(whisper_path),
            "audio_level_input": self._audio_level_input,
        }
        for name, attr, path in self._ipc_service_map():
            stats[f"{name}_connected"] = getattr(self, attr) is not None

        # Drain pending text lists (thread-safe swap)
        with self._pipeline_lock:
            stats["pending_recognized"] = list(self._pending_recognized)
            stats["pending_translated"] = list(self._pending_translated)
            self._pending_recognized.clear()
            self._pending_translated.clear()

        return stats

    def all_services_connected(self) -> bool:
        return all([
            self.translate_client is not None,
            self.tts_client is not None,
        ])

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self):
        self.stop()
        if self.audio_router:
            self.audio_router.cleanup()
        for client in [self.capture_client, self.whisper_client,
                       self.translate_client, self.tts_client, self.playback_client]:
            if client:
                try:
                    client.disconnect()
                except Exception:
                    pass
        logger.info("Translation system cleaned up")

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass
