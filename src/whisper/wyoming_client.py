"""
Wyoming Protocol Client for connecting to wyoming-faster-whisper service.
Uses the proper Wyoming protocol (JSON-line + binary payload) via the `wyoming` library.
"""
import asyncio
import threading
import time
from concurrent.futures import Future
from typing import Optional, Callable, Any

from loguru import logger

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient

# Wyoming ASR protocol flow:
#   Client → Transcribe (model name, language)
#   Client → AudioStart (rate, width, channels)
#   Client → AudioChunk+ (raw PCM audio bytes in payload)
#   Client → AudioStop
#   Server → Transcript+ (recognition results)


class WyomingWhisperClient:
    """Proper Wyoming protocol client bridging async ⟷ sync via background event loop thread."""

    def __init__(self, host: str = "localhost", port: int = 10300):
        self.host = host
        self.port = port

        # Async infrastructure
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._loop_thread.start()
        self._loop_ready.wait()  # ensure loop is running before any call

        self._client: Optional[AsyncClient] = None
        self._connected = False

        # Callback fired on each Transcript event (called from async thread)
        self.on_result: Optional[Callable[[dict], None]] = None

        # Read loop task handle (cancelled on disconnect)
        self._read_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Async loop management
    # ------------------------------------------------------------------

    def _run_async_loop(self) -> None:
        """Background thread entry — run the event loop forever."""
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    def _run_coro(self, coro) -> Any:
        """Schedule a coroutine on the async loop and wait for the result (blocking)."""
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def _call_soon(self, fn, *args) -> None:
        """Schedule a sync callable on the async loop thread (fire-and-forget)."""
        self._loop.call_soon_threadsafe(fn, *args)

    # ------------------------------------------------------------------
    # Public API — synchronous
    # ------------------------------------------------------------------

    def connect(self, retries: int = 3, retry_delay: float = 1.0) -> bool:
        """Connect to the Wyoming TCP service."""
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                self._client = AsyncClient.from_uri(
                    f"tcp://{self.host}:{self.port}"
                )
                self._run_coro(self._client.connect())
                self._connected = True

                # Start the background read loop
                self._read_task = asyncio.run_coroutine_threadsafe(
                    self._read_loop(), self._loop
                )

                logger.info(
                    "Connected to Wyoming at {}:{} (attempt {})",
                    self.host, self.port, attempt,
                )
                return True

            except (ConnectionRefusedError, OSError, TimeoutError) as e:
                last_error = f"{type(e).__name__}: {e} (attempt {attempt}/{retries})"
                logger.warning(
                    "Wyoming connection failed {}:{} — {}",
                    self.host, self.port, last_error,
                )
                if self._client is not None:
                    self._run_coro(self._client.disconnect())
                    self._client = None
                if attempt < retries:
                    time.sleep(retry_delay)

        logger.error(
            "Failed to connect to Wyoming at {}:{} after {} attempts: {}",
            self.host, self.port, retries, last_error,
        )
        self._connected = False
        return False

    def disconnect(self) -> None:
        """Disconnect from the Wyoming service."""
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None
        if self._client is not None:
            try:
                self._run_coro(self._client.disconnect())
            except Exception:
                pass
            self._client = None
        self._connected = False

    def set_callback(self, callback: Callable[[dict], None]) -> None:
        """Set callback for Transcript events (called from background thread)."""
        self.on_result = callback

    def start_recognition(self, language: Optional[str] = None) -> bool:
        """Send Transcribe + AudioStart to begin a recognition session."""
        if not self._connected or self._client is None:
            logger.error("Not connected to Wyoming — cannot start recognition")
            return False

        try:
            # 1. Transcribe event (tells server to start ASR with optional language)
            transcribe = Transcribe(language=language)
            self._run_coro(self._client.write_event(transcribe.event()))

            # 2. AudioStart event (tells server the audio format)
            audio_start = AudioStart(rate=16000, width=2, channels=1)
            self._run_coro(self._client.write_event(audio_start.event()))

            logger.debug("Wyoming recognition started (language={})", language)
            return True
        except Exception as e:
            logger.exception("Failed to start Wyoming recognition: {}", e)
            self._connected = False
            return False

    def send_audio(self, audio_data: bytes) -> bool:
        """Send a chunk of raw PCM audio (s16le 16 kHz mono)."""
        if not self._connected or self._client is None:
            logger.error("Not connected to Wyoming — cannot send audio")
            return False

        try:
            chunk = AudioChunk(
                rate=16000, width=2, channels=1, audio=audio_data,
            )
            self._run_coro(self._client.write_event(chunk.event()))
            return True
        except ConnectionError:
            logger.error("Connection lost sending audio to Wyoming")
            self._connected = False
            return False
        except Exception as e:
            logger.exception("Failed to send audio ({}) to Wyoming: {}", len(audio_data), e)
            self._connected = False
            return False

    def stop_recognition(self) -> bool:
        """Send AudioStop — server will process and respond with Transcript event(s)."""
        if not self._connected or self._client is None:
            logger.error("Not connected to Wyoming — cannot stop recognition")
            return False

        try:
            audio_stop = AudioStop()
            self._run_coro(self._client.write_event(audio_stop.event()))
            logger.debug("Wyoming recognition stopped")
            return True
        except Exception as e:
            logger.exception("Failed to stop Wyoming recognition: {}", e)
            self._connected = False
            return False

    # ------------------------------------------------------------------
    # Async internals
    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Background coroutine — reads Wyoming events until disconnect."""
        try:
            while self._connected and self._client is not None:
                event = await self._client.read_event()
                if event is None:
                    logger.debug("Wyoming read loop: connection closed")
                    break
                self._handle_event(event)
        except asyncio.CancelledError:
            pass  # normal on disconnect
        except Exception as e:
            logger.exception("Wyoming read loop error: {}", e)
        finally:
            self._connected = False

    def _handle_event(self, event) -> None:
        """Process a received Wyoming event."""
        if Transcript.is_type(event.type):
            transcript = Transcript.from_event(event)
            if transcript.text:
                logger.debug(
                    "Wyoming transcript: '{}' (language={})",
                    transcript.text[:80], transcript.language,
                )
                if self.on_result:
                    self.on_result({
                        "type": "segment",
                        "text": transcript.text,
                        "language": transcript.language or "uk",
                        "final": True,
                    })
        else:
            logger.debug("Wyoming received event: type={} data={}", event.type, event.data)


class WyomingWhisperService:
    """
    Wrapper that exposes the same interface as the existing whisper service,
    but connects to wyoming-faster-whisper via TCP.
    """
    def __init__(self, host: str = "localhost", port: int = 10300):
        self.client = WyomingWhisperClient(host, port)
        self.on_result: Optional[Callable[[dict], None]] = None

    def connect(self) -> bool:
        return self.client.connect()

    def disconnect(self) -> None:
        self.client.disconnect()

    def start_recognition(self, language: Optional[str] = None) -> bool:
        return self.client.start_recognition(language)

    def stop_recognition(self) -> bool:
        return self.client.stop_recognition()

    def send_audio(self, audio_data: bytes) -> bool:
        return self.client.send_audio(audio_data)

    def set_callback(self, callback: Callable[[dict], None]) -> None:
        self.on_result = callback
        self.client.set_callback(callback)

    def run_server(self, *args, **kwargs) -> None:
        """Compatibility method — Wyoming client connects to external service."""
        pass
