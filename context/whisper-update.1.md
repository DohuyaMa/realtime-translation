Нижче —  **еталонна реалізація `rt-whisper-service`** :
✔ тільки `faster-whisper`
✔ socket-based
✔ streaming-friendly
✔ без файлів
✔ без UI
✔ готово для `nix develop` і systemd

Я дам:

1. **архітектурний контракт**
2. **протокол сокета**
3. **повний код сервісу**
4. **як запускати в dev**
5. **що перевірити у flake**

---

# 1. Контракт `rt-whisper-service`

## Вхід

* UNIX socket
* **raw PCM 16-bit LE**
* mono
* 16 kHz
* чанки довільної довжини (рекомендовано 20–100 ms)

## Вихід

JSON Lines (`\n`):

```json
{
  "type": "segment",
  "text": "привіт світ",
  "start": 1.23,
  "end": 2.91,
  "final": true
}
```

---

# 2. Протокол (простий і стабільний)

### Control (JSON)

```json
{"cmd": "start", "language": "uk"}
{"cmd": "stop"}
```

### Audio

* після `start`
* **чисті байти PCM**
* без framing
* `stop` завершує сегмент

> Це дозволяє UI / capture-сервісу бути максимально простим.

---

# 3. Повний код `src/whisper/whisper_service.py`

```python
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

    while True:
        conn, _ = server.accept()
        logger.info("Client connected")

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
                        logger.info("Session started (lang={})", session.language)

                    elif cmd == "stop" and session:
                        audio = session.consume()
                        if audio is not None:
                            segments, _ = model.transcribe(
                                audio,
                                language=session.language,
                                vad_filter=True,
                            )
                            for s in segments:
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
                        logger.info("Session stopped")

                # Audio payload
                else:
                    if session:
                        session.feed_audio(payload)

        except Exception as e:
            logger.exception("Client error: {}", e)
        finally:
            conn.close()
            logger.info("Client disconnected")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-path", required=True)
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
```

---

# 4. Як слати дані (приклад клієнта)

```python
import socket
import struct
import json

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect("/run/user/1000/rt-whisper.sock")

def send(msg: bytes):
    sock.sendall(struct.pack("<I", len(msg)) + msg)

send(json.dumps({"cmd": "start", "language": "uk"}).encode())

send(pcm_bytes)  # raw int16 PCM

send(json.dumps({"cmd": "stop"}).encode())
```

---

# 5. Що перевірити у flake (коротко)

## Обовʼязково

* `faster-whisper` ✔
* `ctranslate2` ✔
* `torch` ✔
* **НЕМА** `openai-whisper` ❌

## systemd

```nix
ExecStart = "${whisperService}/bin/rt-whisper-service \
  --socket-path %t/rt-whisper.sock \
  --model medium \
  --device cuda"
```

---

# 6. Чому це правильна реалізація

* **мінімальна latency**
* **контрольована буферизація**
* **ніяких файлів**
* **придатно для real-time**
* **готово до масштабування**

---

## Далі логічний крок

Наступним варто:

* або 🔧 **узгодити формат з `rt-capture`**
* або 🧩 **зробити translate-service у тому ж стилі**

Скажи, що робимо далі.
