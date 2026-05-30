import argparse
import json
import os
import socket
import struct
import threading
import time
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel
from loguru import logger

from ..status_logger import StatusManager
from .wyoming_client import WyomingWhisperService
from ..core.runtime import get_runtime_config

_log_timing = time.monotonic

PCM_DTYPE = np.int16
SAMPLE_RATE = 16000


class WhisperSession:
    def __init__(self, model: Optional[WhisperModel], language: Optional[str], use_wyoming: bool = False):
        self.model = model
        self.language = language
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.use_wyoming = use_wyoming
        if use_wyoming:
            self.wyoming_service = WyomingWhisperService()
            self.wyoming_service.connect()

    def feed_audio(self, data: bytes):
        with self.lock:
            if self.use_wyoming and self.wyoming_service:
                # Send audio data directly to Wyoming service
                self.wyoming_service.send_audio(data)
            else:
                # Store audio in buffer for local processing
                self.buffer.extend(data)

    def consume(self) -> Optional[np.ndarray]:
        with self.lock:
            if not self.buffer:
                return None
            pcm = np.frombuffer(self.buffer, dtype=PCM_DTYPE)
            self.buffer.clear()
            return pcm.astype(np.float32) / 32768.0


def run_server(socket_path: str, model_name: str, device: str, compute_type: str,
               use_wyoming: bool = False, wyoming_host: str = "localhost", wyoming_port: int = 10300,
               beam_size: int = 5, temperature: float = 0.0, initial_prompt: str = ""):
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    # Initialize model only if not using wyoming
    model = None
    wyoming_service = None
    if not use_wyoming:
        t0 = _log_timing()
        logger.info("Loading Whisper model: {} (device={}, compute={})", model_name, device, compute_type)
        try:
            model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute_type,
            )
            load_time = _log_timing() - t0
            logger.info("Whisper model '{}' loaded in {:.1f}s (device={}, compute={})",
                         model_name, load_time, device, compute_type)
        except Exception as e:
            logger.exception("Failed to load Whisper model '{}': {}", model_name, e)
            raise
    else:
        logger.info("Connecting to Wyoming whisper service at {}:{}", wyoming_host, wyoming_port)
        t0 = _log_timing()
        wyoming_service = WyomingWhisperService(wyoming_host, wyoming_port)
        if not wyoming_service.connect():
            connect_time = _log_timing() - t0
            logger.error("Failed to connect to Wyoming service at {}:{} after {:.1f}s",
                         wyoming_host, wyoming_port, connect_time)
            return
        connect_time = _log_timing() - t0
        logger.info("Connected to Wyoming whisper service at {}:{} in {:.1f}s",
                     wyoming_host, wyoming_port, connect_time)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(1)

    logger.info("rt-whisper listening on {}", socket_path)

    # Initialize status manager for logging
    status = StatusManager(component_name="hybrid_whisper")
    service_name = "Wyoming Whisper" if use_wyoming else f"Local Whisper ({model_name})"
    mode = "wyoming" if use_wyoming else "local"
    status.log_info(f"Hybrid whisper initialized: mode={mode} {service_name} socket={socket_path}")

    while True:
        conn, _ = server.accept()
        logger.info("Client connected")
        status.log_info("Client connected")

        session: Optional[WhisperSession] = None
        wyoming_result_handler = None

        try:
            while True:
                header = conn.recv(4)
                if not header:
                    break

                (size,) = struct.unpack("<I", header)
                payload = conn.recv(size)

                # Control message
                if payload.startswith(b"{"):
                    msg = json.loads(payload.decode())
                    cmd = msg.get("cmd")

                    if cmd == "start":
                        session = WhisperSession(
                            model=model,
                            language=msg.get("language"),
                            use_wyoming=use_wyoming
                        )
                        
                        if use_wyoming and session.wyoming_service:
                            # Set up callback to handle Wyoming results
                            def create_result_handler(conn):
                                def handler(result):
                                    _handle_wyoming_result(conn, result)
                                return handler
                            
                            wyoming_result_handler = create_result_handler(conn)
                            session.wyoming_service.set_callback(wyoming_result_handler)
                            session.wyoming_service.start_recognition(msg.get("language"))
                        
                        status.set_status("Loading ASR model...")
                        status.log_info(f"Session started (lang={session.language})")

                    elif cmd == "stop" and session:
                        if use_wyoming:
                            if session.wyoming_service:
                                status.log_debug("Stopping Wyoming recognition session")
                                session.wyoming_service.stop_recognition()
                        else:
                            # Local processing
                            audio = session.consume()
                            if audio is not None:
                                audio_len_sec = len(audio) / SAMPLE_RATE
                                t0 = _log_timing()
                                transcribe_kwargs = dict(
                                    language=session.language,
                                    vad_filter=True,
                                    vad_parameters=dict(
                                        threshold=0.35,
                                        min_speech_duration_ms=100,
                                        min_silence_duration_ms=500,
                                        speech_pad_ms=800,
                                    ),
                                    no_speech_threshold=0.4,
                                    beam_size=beam_size,
                                    temperature=temperature,
                                )
                                if initial_prompt:
                                    transcribe_kwargs["initial_prompt"] = initial_prompt
                                segments, info = model.transcribe(audio, **transcribe_kwargs)
                                transcribe_time = _log_timing() - t0
                                segments_list = list(segments)
                                status.log_info(
                                    f"Transcribed {audio_len_sec:.1f}s audio "
                                    f"→ {len(segments_list)} segments "
                                    f"in {transcribe_time:.2f}s "
                                    f"(RTFX={audio_len_sec/transcribe_time:.1f}x)"
                                )
                                if info is not None and info.language:
                                    status.log_debug(
                                        f"Detected language={info.language} "
                                        f"probability={info.language_probability:.2f}"
                                    )
                                for s in segments_list:
                                    status.log_info(f"Recognized text: {s.text}")
                                    conn.sendall(
                                        (json.dumps({
                                            "type": "segment",
                                            "text": s.text,
                                            "start": s.start,
                                            "end": s.end,
                                            "final": True,
                                        }) + "\n").encode()
                                    )
                            else:
                                status.log_debug("Session stopped with no audio buffered")
                        session = None
                        status.log_info("Session stopped")

                # Audio payload
                else:
                    if session:
                        session.feed_audio(payload)
                        if not use_wyoming:
                            buf_len = len(session.buffer)
                            status.log_debug(
                                f"Audio chunk: size={len(payload)} "
                                f"buf_total={buf_len} ({buf_len/2/SAMPLE_RATE:.1f}s)"
                            )
                        else:
                            status.log_debug(f"Forwarded audio chunk size={len(payload)} to Wyoming")

        except Exception as e:
            logger.exception("Client error: {}", e)
            status.log_exception(f"Client error: {e}")
        finally:
            conn.close()
            status.log_debug("Client disconnected")


def _handle_wyoming_result(conn, result):
    """Handle result from Wyoming service and send to client."""
    try:
        # Forward Wyoming result to client
        text = result.get('text', '')
        if text:
            logger.debug(f"Wyoming result: text='{text[:80]}{'...' if len(text) > 80 else ''}' "
                         f"confidence={result.get('confidence', 'N/A')}")
        if text:
            conn.sendall(
                (json.dumps({
                    "type": "segment",
                    "text": text,
                    "start": result.get('start', 0),
                    "end": result.get('end', 0),
                    "final": True,
                }) + "\n").encode()
            )
    except BrokenPipeError:
        logger.error("Broken pipe sending Wyoming result — client disconnected")
    except Exception as e:
        logger.exception(f"Error sending Wyoming result to client: {e}")


def _cfg_get(key: str, default):
    """Read a dot-notation key from the user config file."""
    from pathlib import Path
    import yaml
    try:
        p = Path.home() / ".config" / "real-time-translator" / "config.yml"
        cfg = yaml.safe_load(p.read_text()) or {}
        for part in key.split("."):
            cfg = cfg.get(part, {})
        return cfg if cfg != {} else default
    except Exception:
        return default


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-path", default=get_runtime_config().get_hybrid_whisper_socket_path(), required=False)
    parser.add_argument("--model", default=None,
                        help="Whisper model size (reads models.whisper.model from config if omitted)")
    parser.add_argument("--device", default=None,
                        help="Device: cuda/cpu/auto (reads models.whisper.device from config if omitted)")
    parser.add_argument("--compute-type", default=None,
                        help="Compute type (reads models.whisper.compute_type from config if omitted)")
    parser.add_argument("--use-wyoming", action="store_true", help="Use Wyoming service instead of local model")
    parser.add_argument("--wyoming-host", default="localhost", help="Wyoming service host")
    parser.add_argument("--wyoming-port", type=int, default=10300, help="Wyoming service port")
    parser.add_argument("--beam-size", type=int, default=None, help="Beam search width")
    parser.add_argument("--temperature", type=float, default=None, help="Sampling temperature")
    parser.add_argument("--initial-prompt", type=str, default=None, help="Prompt text to bias recognition")
    args = parser.parse_args()

    # Priority: config file (UI override) > CLI arg (Nix default) > built-in fallback
    model       = _cfg_get("models.whisper.model",         None) or args.model        or "medium"
    device      = _cfg_get("models.whisper.device",        None) or args.device       or "cuda"
    compute     = _cfg_get("models.whisper.compute_type",  None) or args.compute_type or "float16"
    beam_size   = _cfg_get("models.whisper.beam_size",     None)
    if beam_size is None:
        beam_size = args.beam_size if args.beam_size is not None else 5
    temperature = _cfg_get("models.whisper.temperature",   None)
    if temperature is None:
        temperature = args.temperature if args.temperature is not None else 0.0
    prompt      = _cfg_get("models.whisper.initial_prompt", None)
    if prompt is None:
        prompt = args.initial_prompt if args.initial_prompt is not None else ""

    logger.info("hybrid_whisper_service starting: model={} device={} compute={} wyoming={}",
                model, device, compute, args.use_wyoming)

    run_server(
        socket_path=args.socket_path,
        model_name=model,
        device=device,
        compute_type=compute,
        use_wyoming=args.use_wyoming,
        wyoming_host=args.wyoming_host,
        wyoming_port=args.wyoming_port,
        beam_size=beam_size,
        temperature=temperature,
        initial_prompt=prompt,
    )


if __name__ == "__main__":
    main()