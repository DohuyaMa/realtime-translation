Ось верхньорівнева картина проекту:

```mermaid
graph TD
    MIC[Мікрофон / PipeWire\nrt_virtual_input] -->|аудіо| CAP[capture service\nUnix socket]

    CAP -->|PCM chunks| WH[whisper / hybrid-whisper\nWhisper → STT]

    WH -->|текст| TR[translate service\nLLM / deep-translator]

    TR -->|перекладений текст| TTS[tts service\nKokoro TTS]

    TTS -->|аудіо| PB[playback service\nrt_virtual_output]

    PB --> SPK[Динамік]

    subgraph Core
        TS[TranslationSystem\nipc clients]
        DA[DirectAdapter]
        DA --> TS
        TS -->|UnixSocket JSON| CAP
        TS -->|UnixSocket JSON| WH
        TS -->|UnixSocket JSON| TR
        TS -->|UnixSocket JSON| TTS
        TS -->|UnixSocket JSON| PB
    end

    subgraph UI Layer
        MW[MainWindow\nPySide6 / Qt6]
        UC[UIController]
        CC[ConcreteTranslatorController]
        MW --> UC --> CC --> DA
    end

    subgraph Config
        CFG[ConfigManager\n~/.config/real-time-translator/config.yml]
        RT[get_runtime_config\nSocket paths]
    end

    CC --> CFG
    TS --> RT
```

**Основний потік:**
`Mic → capture → whisper → translate → TTS (Kokoro) → playback → Speaker`

**Комунікація:** всі сервіси — окремі процеси (або systemd units), спілкуються через Unix socket (JSON IPC).

**UI:** Qt6 → UIController → DirectAdapter → TranslationSystem → сокети до сервісів.

**Проблема з todo.md:** kokoro (TTS) тягне torch → CUDA → libnvshmem, що при Nix-збірці з сорців падає по OOM. Варіанти: cachix бінарний кеш, docker, або замінити kokoro на piper-tts.
