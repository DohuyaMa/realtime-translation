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
from ..core.runtime import get_runtime_config

_log_timing = time.monotonic  # micro-optimization: local ref

PCM_DTYPE = np.int16
SAMPLE_RATE = 16000


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
    server.listen(1)

    logger.info("rt-whisper listening on {}", socket_path)

    # Initialize status manager for logging
    status = StatusManager(component_name="whisper")
    status.log_info(
        f"Whisper service initialized: model={model_name} "
        f"device={device} compute={compute_type} "
        f"load_time={load_time:.1f}s"
    )

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-path", default=get_runtime_config().get_whisper_socket_path(), required=False)
    parser.add_argument("--model", default="medium")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam search width")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature (0=deterministic)")
    parser.add_argument("--initial-prompt", type=str, default="", help="Prompt text to bias recognition")
    args = parser.parse_args()

    run_server(
        socket_path=args.socket_path,
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        temperature=args.temperature,
        initial_prompt=args.initial_prompt,
    )


if __name__ == "__main__":
    main()