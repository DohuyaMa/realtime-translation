import argparse
import json
import os
import re
import socket
import struct
import threading
import time
from collections import deque
from typing import List, Optional

import numpy as np
from faster_whisper import WhisperModel
from loguru import logger

from ..status_logger import StatusManager
from ..core.runtime import get_runtime_config

_log_timing = time.monotonic  # micro-optimization: local ref

PCM_DTYPE = np.int16
SAMPLE_RATE = 16000


def _normalize_word(w: str) -> str:
    """Strip leading/trailing punctuation for comparison."""
    return re.sub(r"^[^\w]+|[^\w]+$", "", w, flags=re.UNICODE).lower()


def _filter_repetitions(text: str) -> str:
    """Collapse consecutive repeated words or short phrases.

    Punctuation is stripped for comparison so "знаю, знаю" and "Добре. Добре"
    are caught, but the original token (with punctuation) is kept in output.

    Examples:
        "hello hello hello"      → "hello"
        "I think think think"    → "I think"
        "я не знаю, я не знаю"  → "я не знаю,"
        "Добре. Добре"           → "Добре."
    """
    if not text:
        return text
    words = text.split()
    if len(words) < 2:
        return text

    normed = [_normalize_word(w) for w in words]

    result: List[str] = []
    i = 0
    while i < len(words):
        result.append(words[i])
        moved = False
        for win in range(1, min(6, len(words) - i + 1)):
            seq_n = normed[i : i + win]
            j = i + win
            count = 0
            while j + win <= len(words) and normed[j : j + win] == seq_n:
                j += win
                count += 1
            if count > 0:
                if win > 1:
                    result.extend(words[i + 1 : i + win])
                i = j
                moved = True
                break
        if not moved:
            i += 1

    return " ".join(result)


class WhisperSession:
    def __init__(self, model: WhisperModel, language: Optional[str]):
        self.model = model
        self.language = language
        self.buffer = bytearray()
        self.lock = threading.Lock()

    def feed_audio(self, data: bytes):
        with self.lock:
            self.buffer.extend(data)

    def consume(self) -> Optional[np.ndarray]:
        with self.lock:
            if not self.buffer:
                return None
            pcm = np.frombuffer(self.buffer, dtype=PCM_DTYPE).copy()
            self.buffer = bytearray()
            return pcm.astype(np.float32) / 32768.0


def run_server(socket_path: str, model_name: str, device: str, compute_type: str,
               beam_size: int = 5, temperature: float = 0.0, initial_prompt: str = ""):
    if os.path.exists(socket_path):
        logger.debug(f"Removing stale socket: {socket_path}")
        os.unlink(socket_path)

    # Log model loading with timing
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

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(5)

    logger.info("rt-whisper listening on {}", socket_path)

    status = StatusManager(component_name="whisper")
    status.log_info(
        f"Whisper service initialized: model={model_name} "
        f"device={device} compute={compute_type} "
        f"load_time={load_time:.1f}s"
    )

    # Context buffer: accumulates the last N transcribed segments across sessions.
    # Fed back as initial_prompt so Whisper understands ongoing speech better.
    _context: deque = deque(maxlen=5)

    while True:
        conn, _ = server.accept()
        logger.info("Client connected")
        status.log_info("Client connected")

        session: Optional[WhisperSession] = None

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
                        )
                        status.set_status("Loading ASR model...")
                        status.log_info(f"Session started (lang={session.language})")

                    elif cmd == "stop" and session:
                        audio = session.consume()
                        if audio is not None:
                            audio_len_sec = len(audio) / SAMPLE_RATE
                            t0 = _log_timing()

                            # Build prompt: static initial_prompt + rolling context
                            ctx_text = " ".join(_context)
                            prompt = (
                                f"{initial_prompt} {ctx_text}".strip()
                                if initial_prompt else ctx_text
                            )

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
                                # Prevents within-chunk repetition loops in small models.
                                # Each window is transcribed independently; initial_prompt
                                # still seeds the first window with rolling context.
                                condition_on_previous_text=False,
                            )
                            if prompt:
                                transcribe_kwargs["initial_prompt"] = prompt

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
                                text = _filter_repetitions(s.text.strip())
                                if not text:
                                    continue
                                _context.append(text)
                                status.log_info(f"Recognized text: {text}")
                                conn.sendall(
                                    (json.dumps({
                                        "type": "segment",
                                        "text": text,
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
                        buf_len = len(session.buffer)
                        status.log_debug(
                            f"Audio chunk: size={len(payload)} "
                            f"buf_total={buf_len} ({buf_len/2/SAMPLE_RATE:.1f}s)"
                        )

        except Exception as e:
            logger.exception("Client error: {}", e)
            status.log_exception(f"Client error: {e}")
        finally:
            conn.close()
            status.log_debug("Client disconnected")


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
    parser.add_argument("--socket-path", default=get_runtime_config().get_whisper_socket_path(), required=False)
    parser.add_argument("--model", default=None,
                        help="Whisper model size (reads models.whisper.model from config if omitted)")
    parser.add_argument("--device", default=None,
                        help="Device: cuda/cpu/auto (reads models.whisper.device from config if omitted)")
    parser.add_argument("--compute-type", default=None,
                        help="Compute type: float16/int8/... (reads models.whisper.compute_type from config if omitted)")
    parser.add_argument("--beam-size", type=int, default=None,
                        help="Beam search width (reads models.whisper.beam_size from config if omitted)")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Sampling temperature (reads models.whisper.temperature from config if omitted)")
    parser.add_argument("--initial-prompt", type=str, default=None,
                        help="Prompt text to bias recognition (reads models.whisper.initial_prompt from config if omitted)")
    args = parser.parse_args()

    # Priority: config file (UI override) > CLI arg (Nix default) > built-in fallback
    # This lets the user change model from the UI without touching Nix files.
    model       = _cfg_get("models.whisper.model",         None) or args.model        or "medium"
    device      = _cfg_get("models.whisper.device",        None) or args.device       or "auto"
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

    logger.info("whisper_service starting: model={} device={} compute={}", model, device, compute)

    run_server(
        socket_path=args.socket_path,
        model_name=model,
        device=device,
        compute_type=compute,
        beam_size=beam_size,
        temperature=temperature,
        initial_prompt=prompt,
    )


if __name__ == "__main__":
    main()