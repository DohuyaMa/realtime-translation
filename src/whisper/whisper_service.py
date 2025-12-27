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
            pcm = np.frombuffer(self.buffer, dtype=PCM_DTYPE)
            self.buffer.clear()
            return pcm.astype(np.float32) / 32768.0


def run_server(socket_path: str, model_name: str, device: str, compute_type: str):
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    logger.info("Loading Whisper model: {}", model_name)
    model = WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
    )

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(1)

    logger.info("rt-whisper listening on {}", socket_path)

    # Initialize status manager for logging
    status = StatusManager()
    status.log_info(f"Whisper service initialized with model: {model_name}")

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
                            segments, _ = model.transcribe(
                                audio,
                                language=session.language,
                                vad_filter=True,
                            )
                            for s in segments:
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
                        session = None
                        status.log_info("Session stopped")

                # Audio payload
                else:
                    if session:
                        session.feed_audio(payload)
                        status.log_debug(f"Received audio chunk size={len(payload)}")

        except Exception as e:
            logger.exception("Client error: {}", e)
            status.log_error(f"Client error: {e}")
        finally:
            conn.close()
            logger.info("Client disconnected")
            status.log_info("Client disconnected")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-path", default=get_runtime_config().get_whisper_socket_path(), required=False)
    parser.add_argument("--model", default="medium")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="float16")
    args = parser.parse_args()

    run_server(
        socket_path=args.socket_path,
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
    )


if __name__ == "__main__":
    main()