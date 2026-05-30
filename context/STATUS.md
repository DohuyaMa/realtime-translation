# Real-Time Translator — Статус системи

**Дата фіксації:** 2026-05-28  
**NixOS generation:** 131  
**Гілка:** main

---

## Стан сервісів

| Сервіс | Статус | Пристрій | Примітки |
|---|---|---|---|
| rt-capture | active | CPU | Jabra EVOLVE 20 |
| rt-whisper | active | **CUDA float16** | faster-whisper medium |
| rt-translate | active | **CUDA** | opus-mt-uk-en |
| rt-tts | active | **CUDA** | Kokoro af_heart |
| rt-playback | active | CPU | |
| rt-hybrid-whisper | active | CPU | Wyoming proxy (резерв) |
| wyoming-faster-whisper | active | CPU (system) | small-int8, порт 10300 |

---

## Ключові показники продуктивності (GPU)

| Сервіс | Латентність | VRAM |
|---|---|---|
| Whisper medium (warm) | 0.22s / 6s аудіо → RTFX 26x | 2014 MiB |
| Translate opus-mt | ~50 ms / фразу | 392 MiB |
| Kokoro TTS (warm) | ~1.1s / фразу | 442 MiB |
| **End-to-end pipeline** | **~7.5s** | **~2.85 GB / 8 GB** |

Детальний бенчмарк: [`gpu-performance-2026-05-28.md`](gpu-performance-2026-05-28.md)

---

## Що зроблено (цієї сесії)

- **rt-hybrid-whisper**: виправлено ExecStart — вказував на `translator-ui` замість `translator-hybrid-whisper`
- **Nix build**: прибрано 785-пакетну збірку — overlay `torch → torch-bin`, `torchvision → torchvision-bin`
- **ctranslate2 CUDA**: додано `ctranslate2.override { withCUDA = true }` в overlay
- **rt-whisper**: змінено `--compute-type float32` → `--device cuda --compute-type float16`
- **home-manager-module.nix**: всі сервіси тепер використовують власні Nix пакети (не `translator-ui`)

---

## Відомі обмеження

| Проблема | Опис |
|---|---|
| Capture window 6s | Фіксований буфер — основний contributor до end-to-end латентності |
| TTS cold start | Перший виклик Kokoro ~6s (завантаження моделі) |
| Wyoming на CPU | `device = "cpu"` в конфігурації — не використовується в основному потоці |
| Мова Whisper | `lang=None` за замовчуванням — auto-detect повільніший ніж `lang=uk` явно |

---

## Подальші кроки (пріоритет)

1. Зменшити capture window або зробити його адаптивним (VAD)
2. Явно передавати `language=uk` у Whisper сесію з UI
3. Розглянути Wyoming `device = "cuda"` для резервного шляху
