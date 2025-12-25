Нижче — **мінімальний, еталонний ASR flake** як  **окремий модуль** , спеціально під  **dev (`nix develop`)** , з  **faster-whisper** ,  **socket-based** ,  **без UI** ,  **без PipeWire** , **без systemd** (systemd буде готовий логічно, але не активний).

Це  **базовий будівельний блок** , який ти потім:

* або імпортуєш у головний flake
* або підключаєш як input
* або запускаєш standalone

---

# 1. Архітектура модуля ASR

**Контракт модуля** (важливо):

```
INPUT:
  UNIX socket
  raw PCM 16kHz mono (float32 або int16)

OUTPUT:
  UNIX socket
  JSON events:
    { text, start, end, final }
```

❌ Немає:

* UI
* capture
* playback
* translation
* PipeWire
* файлів

---

# 2. Структура каталогу

```
asr/
├── flake.nix
├── src/
│   └── asr/
│       ├── __init__.py
│       └── service.py
└── README.md
```

---

# 3. `flake.nix` (мінімальний, правильний)

```nix
{
  description = "Minimal ASR module (faster-whisper, socket-based)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachSystem [ "x86_64-linux" ] (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        python = pkgs.python313;

        pythonEnv = python.withPackages (ps: with ps; [
          faster-whisper
          ctranslate2
          torch
          numpy
          soundfile
          loguru
        ]);

        asrService = pkgs.writeShellApplication {
          name = "rt-asr-service";
          runtimeInputs = [ pythonEnv ];
          text = ''
            exec ${pythonEnv.interpreter} -m asr.service "$@"
          '';
        };
      in
      {
        packages.default = asrService;

        apps.default = {
          type = "app";
          program = "${asrService}/bin/rt-asr-service";
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [
            pythonEnv
            pkgs.socat
          ];

          shellHook = ''
            export PYTHONPATH="$PWD/src:$PYTHONPATH"
            echo "ASR dev shell ready"
            echo "Run: rt-asr-service --in /tmp/asr-in.sock --out /tmp/asr-out.sock"
          '';
        };
      }
    );
}
```

---

# 4. ASR сервіс (`src/asr/service.py`)

Це  **ядро** , без зайвого.

```python
import argparse
import json
import socket
import struct
from loguru import logger
from faster_whisper import WhisperModel
import numpy as np

def read_pcm(conn):
    while True:
        header = conn.recv(4)
        if not header:
            break
        size = struct.unpack("!I", header)[0]
        data = conn.recv(size)
        yield np.frombuffer(data, dtype=np.float32)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_sock", required=True)
    parser.add_argument("--out", dest="out_sock", required=True)
    parser.add_argument("--model", default="medium")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="float16")
    args = parser.parse_args()

    logger.info("Loading Whisper model…")
    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type
    )

    in_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    out_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    in_srv.bind(args.in_sock)
    out_srv.bind(args.out_sock)

    in_srv.listen(1)
    out_srv.listen(1)

    logger.info("Waiting for connections…")
    in_conn, _ = in_srv.accept()
    out_conn, _ = out_srv.accept()

    logger.info("Connections established")

    for pcm in read_pcm(in_conn):
        segments, _ = model.transcribe(
            pcm,
            language="uk",
            vad_filter=True
        )

        for seg in segments:
            msg = {
                "text": seg.text,
                "start": seg.start,
                "end": seg.end,
                "final": True
            }
            out_conn.sendall(
                json.dumps(msg).encode("utf-8") + b"\n"
            )

if __name__ == "__main__":
    main()
```

---

# 5. Як тестувати в dev (`nix develop`)

```bash
nix develop
```

### Термінал 1

```bash
rt-asr-service \
  --in /tmp/asr-in.sock \
  --out /tmp/asr-out.sock
```

### Термінал 2 (фейковий клієнт)

```bash
socat - UNIX-CONNECT:/tmp/asr-in.sock < audio.raw
```

### Термінал 3

```bash
socat UNIX-CONNECT:/tmp/asr-out.sock -
```

---

# 6. Чому це ПРАВИЛЬНО

✔ ASR ізольований
✔ faster-whisper
✔ streaming
✔ socket-first
✔ nix-friendly
✔ systemd-ready
✔ легко імпортується у головний flake

---

# 7. Як інтегрувати далі

У головному flake:

```nix
inputs.asr.url = "path:./asr";
```

Або:

* замінити `rt-whisper-service`
* або використати як dependency

---

## Наступний логічний крок

1️⃣ додати **VAD tuning**
2️⃣ додати **partial results (final=false)**
3️⃣ обгорнути це у **systemd user service**

Скажи, що робимо далі — і рухаємось далі так само чисто.
