# Моделі: як скачати

## Переклад: Helsinki-NLP/opus-mt-uk-en

```bash
nix develop --command python3 -c "
from transformers import pipeline
pipe = pipeline('translation', model='Helsinki-NLP/opus-mt-uk-en')
print('DONE')
"
```

Кешується в `~/real-time-translator-cache/huggingface/hub/models--Helsinki-NLP--opus-mt-uk-en/`.

## TTS: Kokoro-82M

Вже закешовано. Якщо треба перекачати:

```bash
nix develop --command python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('hexgrad/Kokoro-82M')
print('DONE')
"
```

Кешується в `~/.cache/huggingface/hub/` (або `HF_HOME`).

## Whisper (локальний, без Wyoming)

Якщо `use_wyoming: false` в конфігу, whisper використовує `faster-whisper` з моделлю `small`:

```bash
nix develop --command python3 -c "
from faster_whisper import WhisperModel
model = WhisperModel('small', device='cpu', download_root='~/.cache/whisper/')
print('DONE')
"
```

Кешується в `~/.cache/whisper/`.

## en_core_web_sm (spaCy, для kokoro G2P)

Додана як Nix derivation в `flake-global/dev/devshell.nix` та `flake-global/parts/python/tts.nix`. Качається автоматично при `nix develop` або `nix build .#tts`. Не треба скачувати вручну.
