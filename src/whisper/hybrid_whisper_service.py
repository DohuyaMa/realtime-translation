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


def run_server(socket_path: str, model_name: str, device: str, compute_type: str, use_wyoming: bool = False, wyoming_host: str = "localhost", wyoming_port: int = 10300):
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    # Initialize model only if not using wyoming
    model = None
    wyoming_service = None
    if not use_wyoming:
        logger.info("Loading Whisper model: {}", model_name)
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )
    else:
        logger.info("Using Wyoming whisper service at {}:{}", wyoming_host, wyoming_port)
        wyoming_service = WyomingWhisperService(wyoming_host, wyoming_port)
        if not wyoming_service.connect():
            logger.error("Failed to connect to Wyoming service")
            return

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(1)

    logger.info("rt-whisper listening on {}", socket_path)

    # Initialize status manager for logging
    status = StatusManager()
    service_name = "Wyoming Whisper" if use_wyoming else f"Local Whisper ({model_name})"
    status.log_info(f"Whisper service initialized with: {service_name}")

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
                                session.wyoming_service.stop_recognition()
                        else:
                            # Local processing
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


def _handle_wyoming_result(conn, result):
    """Handle result from Wyoming service and send to client."""
    try:
        # Forward Wyoming result to client
        if 'text' in result:
            conn.sendall(
                (json.dumps({
                    "type": "segment",
                    "text": result.get('text', ''),
                    "start": result.get('start', 0),
                    "end": result.get('end', 0),
                    "final": True,
                }) + "\n").encode()
            )
    except Exception as e:
        logger.error(f"Error sending Wyoming result to client: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-path", required=True)
    parser.add_argument("--model", default="medium")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--use-wyoming", action="store_true", help="Use Wyoming service instead of local model")
    parser.add_argument("--wyoming-host", default="localhost", help="Wyoming service host")
    parser.add_argument("--wyoming-port", type=int, default=10300, help="Wyoming service port")
    args = parser.parse_args()

    run_server(
        socket_path=args.socket_path,
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
        use_wyoming=args.use_wyoming,
        wyoming_host=args.wyoming_host,
        wyoming_port=args.wyoming_port
    )


if __name__ == "__main__":
    main()