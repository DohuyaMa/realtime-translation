# GPU Performance Benchmarks — 2026-05-28

## Hardware

- GPU: NVIDIA GeForce RTX 4060 Laptop (8 GB VRAM)
- Driver: 595.71.05 | CUDA: 13.2
- OS: NixOS 26.05 (generation 131)

## GPU Memory Allocation (at rest)

| Service | VRAM |
|---|---|
| rt-whisper (faster-whisper medium float16) | 2014 MiB |
| rt-translate (Helsinki-NLP opus-mt-uk-en) | 392 MiB |
| rt-tts (Kokoro af_heart) | 442 MiB |
| **Total** | **~2848 MiB / 8192 MiB (35%)** |

---

## rt-whisper — faster-whisper medium, CUDA float16

Raw log samples:

```
22:12:42 Transcribed 6.0s audio → 1 segments in 0.67s (RTFX=9.0x)   # перший (cold GPU)
22:12:57 Transcribed 6.0s audio → 0 segments in 0.23s (RTFX=26.4x)
22:13:12 Transcribed 6.0s audio → 1 segments in 0.22s (RTFX=27.7x)
22:13:27 Transcribed 6.0s audio → 0 segments in 0.27s (RTFX=22.2x)
22:14:27 Transcribed 6.0s audio → 2 segments in 0.01s (RTFX=566.7x)  # lang=uk явно
```

| Метрика | Значення |
|---|---|
| Завантаження моделі | 2.0s |
| Перша транскрипція (cold) | 0.67s → RTFX 9x |
| Типова транскрипція (warm) | 0.22–0.27s → RTFX 22–27x |
| З явним lang=uk | ~0.01s → RTFX 566x |

**До (CPU float32):** 4.43s → RTFX 1.4x → **прискорення ~20x**

---

## rt-translate — Helsinki-NLP/opus-mt-uk-en, CUDA

Raw log samples:

```
22:14:43 Translating (24 chars): Добре, ну шо ж, давай...
22:14:43 Translated in 98ms  → Okay, well, come on...

22:14:44 Translating (6 chars): Так...
22:14:44 Translated in 16ms  → Yes...

22:14:58 Translating (23 chars): переді мною керівництво
22:14:58 Translated in 31ms  → I've got leadership here.

22:15:58 Translating (15 chars): Доби сапаску...
22:15:58 Translated in 97ms  → Do as long as you can...

22:16:14 Translating (8 chars): Скачіть!
22:16:14 Translated in 20ms  → Shut up!
```

| Метрика | Значення |
|---|---|
| Завантаження моделі | 2.6s |
| Коротка фраза (6–8 символів) | 16–20 ms |
| Середня фраза (15–24 символи) | 31–98 ms |
| Типове середнє | ~50 ms |

---

## rt-tts — Kokoro af_heart, CUDA

Raw log samples:

```
22:13:03 TTS generated in 5903ms: 62 chars → 4.1s audio  # cold
22:13:28 TTS generated in 1169ms: 15 chars → 1.8s audio
22:14:44 TTS generated in 1304ms: 22 chars → 1.9s audio
22:14:45 TTS generated in  987ms:  6 chars → 1.4s audio
22:14:58 TTS generated in   51ms: 25 chars → 1.9s audio  # з кешу
22:16:00 TTS generated in 1227ms: 24 chars → 2.0s audio
22:16:15 TTS generated in 1015ms:  8 chars → 1.4s audio
```

| Метрика | Значення |
|---|---|
| Перший виклик (cold) | ~5.9s |
| Warm (з кешу) | 51 ms |
| Типова warm генерація | 0.99–1.3s |

---

## End-to-end Pipeline Latency

```
[Capture window]  6.00s  (фіксований розмір буфера)
[Whisper GPU]     0.22s  (warm, medium float16)
[Translate GPU]   0.05s  (типова фраза)
[TTS GPU]         1.20s  (warm, Kokoro)
[Playback]        ~0.0s
──────────────────────────
Загальна затримка ≈ 7.5s від початку мовлення до звуку
```

**Вузьке місце:** capture window (6s) та TTS (1.2s). Whisper і translate — не bottleneck.
