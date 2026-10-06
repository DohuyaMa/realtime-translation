# Підпроєкт 5 — Model optimization & VRAM budget (design)

Дата: 2026-10-06
Статус: **draft, делеговано** (architectural path; партнер делегував рішення, без інтерактивних питань — припущення і відкриті питання в §9). Узгоджено з іншими підпроєктами 2026-10-06 — overview §7 (C1–C3: ROCm через nixpkgs `rocmSupport`, GPU-пакування за цим spec, VRAM-бюджет cyborg за цим spec).
Базовий документ: [`2026-10-06-distributed-speech-services-overview.md`](2026-10-06-distributed-speech-services-overview.md) (напрямок A, спільний контракт §4). Відхилення — §8.
План: [`../plans/2026-10-06-model-optimization.md`](../plans/2026-10-06-model-optimization.md).

---

## 1. Розуміння задачі

**Мета (сказано партнером / overview / memory):**
- Real-time переклад uk/pl → en: ASR → MT → TTS. Пріоритет — **VRAM-first**: усе, що на GPU, має вміщатись разом із llama-service на тому ж хості; швидкість важливіша за максимальну точність; квантизовані/дистильовані моделі — бажані.
- MT: перевести NLLB-200-distilled-600M з HF transformers (fp32 ≈ 2.5 GB) на CTranslate2 int8 (≈ 0.6 GB).
- TTS: Kokoro-82M без source-build torch (`kokoro → torch → libnvshmem`, `plans/todo.md`).
- Мати **перевірювані** рішення: benchmark harness (RTF, latency, VRAM) + пороги прийняття.
- Nix-only: жодного pip/venv.

**Критерії успіху:**
1. Для кожного класу пристроїв (cuda 8 GB із llama, rocm iGPU, cpu) є визначений default (модель, compute type, потоки), обґрунтований вимірюванням harness'у.
2. Сумарний пік VRAM на cyborg (llama завантажена + rt-asr + rt-mt під навантаженням) ≤ 7.4 GiB з 8 GiB.
3. Рантайм-закриття сервісів не потребує source-build torch/libnvshmem (усе з `cache.nixos.org` / `cache.nixos-cuda.org`).
4. Моделі провізіонуються в `<stateDir>/models` з pin'ом ревізії і sha256; після провізіонування сервіс працює з `HF_HUB_OFFLINE=1`.

**Поза скоупом:** HTTP API (підпроєкт 1), структура flake/NixOS-модуля (2) — окрім вимог, що випливають з вибору моделей (§4.5), інфра (3), Django (4).

---

## 2. Ключові факти (перевірено 2026-10-06)

Факти, перевірені локально проти **заблокованого nixpkgs `89570f24e97e614aa34aa9ab1c927b6578a43775`** (flake.lock цього репо):

| # | Факт | Як перевірено |
|---|---|---|
| F1 | `ctranslate2` 4.8.0, `faster-whisper` 1.2.1, `onnxruntime` 1.26.0, `kokoro`/`misaki` 0-unstable-2025-06-16, `torch`/`torch-bin` 2.12.0, `huggingface-hub` 1.10.2, `transformers` 5.5.4, `sentencepiece` 0.2.1 | `nix eval` |
| F2 | `kokoro-onnx`, `espeakng-loader`, `phonemizer-fork` — **нема** в nixpkgs | `nix eval` (attr missing) |
| F3 | nixpkgs `ctranslate2` (старий lock `89570f2`, 4.8.0) має лише `withCUDA/withCuDNN/withMkl/withOneDNN/withOpenblas(=true)/withRuy(=true)` — без ROCm/HIP опції. **Але** nixpkgs інфри (`swarm-nix/system-conf` → `7a0f122`, CT2 4.8.2) має `rocmSupport ? config.rocmSupport`, `rocmGpuTargets`, hip-бібліотеки (`WITH_HIP`); overlay будується з `pkgs` інфри, а SP2 Task 0 піднімає lock цього репо (overview §7 C1) | `pkgs/by-name/ct/ctranslate2/package.nix` в обох ревізіях |
| F4 | Upstream CT2 має `-DWITH_HIP=ON` (ROCm з 4.7.0, 2026-02-03); HIP несумісний з `WITH_FLASH_ATTN`, AWQ, tensor-parallel; відомі "Memory access fault" на RDNA4 (issue #2021, відкритий) | CHANGELOG, CMakeLists.txt |
| F5 | CPU-збірка nixpkgs CT2 (OpenBLAS+Ruy, без MKL/oneDNN) підтримує `{int8, int8_float32, float32}` на x86 | `ctranslate2.get_supported_compute_types('cpu')` у nix shell |
| F6 | CPU-стек (`torch`, `kokoro`, `misaki`, `onnxruntime`, `ctranslate2`, `faster-whisper`, `phonemizer`, `spacy`) — **у cache.nixos.org** | `nix path-info --store https://cache.nixos.org` |
| F7 | З `config.cudaSupport = true` (глобально, дефолтні `cudaCapabilities`) — `ctranslate2`, `onnxruntime`, `python3Packages.torch` і навіть `cudaPackages.libnvshmem` **є в `cache.nixos-cuda.org`** (libnvshmem також у `cache.flox.dev`). У `cuda-maintainers.cachix.org` — нема | `nix path-info` по кешах |
| F8 | `torch-bin` на x86_64-linux — завжди wheel `cu130` + cudaPackages; override `kokoro.override { torch = torch-bin; }` змінює хеші → промах кешу | `torch/bin/binary-hashes.nix` |
| F9 | `misaki.en` імпортує `torch` і `transformers` **на рівні модуля**; `misaki.espeak` — ні. nixpkgs `misaki.optional-dependencies.en` включає `torch`, `transformers` | upstream `misaki/en.py`, nixpkgs `misaki/default.nix` |
| F10 | `kokoro.KPipeline(lang_code, model=False)` — "quiet" режим: тільки G2P + чанкінг (≤510 фонем), `output=None` | upstream `kokoro/pipeline.py` |
| F11 | ONNX Runtime **видалив ROCm EP у 1.23** (рекомендовано MIGraphX EP) | ORT 1.23 release notes |
| F12 | faster-whisper alias `large-v3-turbo` → `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, що тепер редіректить на `dropbox-dash/faster-whisper-large-v3-turbo` | `faster_whisper/utils.py`, HF API |
| F13 | faster-whisper VAD = Silero v6 (ONNX, CPU); `VadOptions` defaults: threshold 0.5, min_silence 2000 ms, speech_pad 400 ms, max_speech ∞ | `faster_whisper/vad.py` |
| F14 | cyborg: RTX 4060 Laptop **8188 MiB**; дисплей на Intel iGPU (kwin на NVIDIA — 7 MiB); llama-service завантажує модель ліниво (зараз 114 MiB, модель не завантажена) | `nvidia-smi` на cyborg |
| F15 | llama на cyborg: `Qwen3.5-4B.Q4_K_M.gguf`, `nGpuLayers=-1`, `ctxSize=16384`. Qwen3.5-4B — гібрид (32 шари, full-attention кожен 4-й ⇒ 8 шарів з KV; 4 KV-heads × head_dim 256) ⇒ KV f16 @16384 = 8·2·4·256·16384·2 B = **512 MiB** | `machines/cyborg/.../llama-service.nix`, HF `config.json` |
| F16 | SKTOP-NOTE17-NIXOS: Ryzen 7 5700U (8 thr), Vega 8 **gfx90c** (HSA override → gfx900), **0.5 GiB carve-out + 7.5 GiB GTT**, 14 GiB RAM; llama ROCm `nGpuLayers=-1`, ctx 4096, модель ≤ ~1 GiB | `machines/SKTOP-NOTE17-NIXOS/README.md` |
| F17 | SKTOP-NOTE27-NIXOS: NVIDIA (модель/VRAM **невідомі** — коментар у конфігу), llama `nGpuLayers=-1`, ctx 16384, "~4GB system RAM this box is short on" | `machines/sktop-note27-nixos/.../llama-service.nix` |
| F18 | kira-kiosk, sktop-nixos-top: CPU-only, llama 2 потоки, Tier-1 (rerank) | їхні `llama-service.nix` |
| F19 | nixpkgs ROCm `clr.gpuTargets` включає `gfx900` | `rocm-modules/clr/default.nix` |

Факти з HF Hub API (ревізії/розміри/sha256 — §4.7):

| Репо | Ревізія (sha) | Ліцензія | Основний файл |
|---|---|---|---|
| `Systran/faster-whisper-small` | `536b0662742c02347bc0e980a01041f333bce120` | MIT | `model.bin` 483.5 MB (fp16) |
| `Systran/faster-whisper-medium` | `08e178d48790749d25932bbc082711ddcfdfbc4f` | MIT | `model.bin` 1527.9 MB |
| `dropbox-dash/faster-whisper-large-v3-turbo` | `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` | MIT | `model.bin` 1617.9 MB |
| `JustFrederik/nllb-200-distilled-600M-ct2-int8` | `302d78f00e6fdb50a1064059df7c392b735e9d05` | CC-BY-NC-4.0 | `model.bin` 622.6 MB; `sentencepiece.bpe.model` sha256 **ідентичний** `facebook/nllb-200-distilled-600M` |
| `facebook/nllb-200-distilled-600M` | `f8d333a098d19b4fd9a8b18f94170487ad3f821d` | CC-BY-NC-4.0 | `pytorch_model.bin` 2460.5 MB (тільки pickle, без safetensors) |
| `onnx-community/Kokoro-82M-v1.0-ONNX` | `1939ad2a8e416c0acfeecc08a694d14ef25f2231` | Apache-2.0 | `onnx/model.onnx` 325.5 MB, `model_fp16` 163.2 MB, `model_quantized` 92.4 MB; `voices/*.bin` по 522 240 B |
| `hexgrad/Kokoro-82M` | `f3ff3571791e39611d31c381e3a41a3af07b4987` | Apache-2.0 | (fallback torch-рушій) |
| `Helsinki-NLP/opus-mt-tc-big-zle-en` | `09a40f722d6d8b76aaad6fe51a06c914622a13d1` | CC-BY-4.0 | 477.8 MB fp32 (~119M параметрів); uk→en flores101-devtest BLEU 39.2 / chrF 0.650 |
| `Helsinki-NLP/opus-mt-tc-big-zlw-en` | `5f2b1d3591f4b835b20bd7f2c70ad193df2379b9` | CC-BY-4.0 | 471.9 MB fp32 |
| `nvidia/parakeet-tdt-0.6b-v3` | — | CC-BY-4.0 | 25 європейських мов incl. uk/pl; FLEURS WER uk 6.79 %, pl 7.31 % |

---

## 3. Розглянуті підходи

### 3.1 Де відбувається конвертація NLLB → CT2

| Варіант | Плюси | Мінуси |
|---|---|---|
| **N1. Nix derivation** (FOD на `pytorch_model.bin` 2.46 GB + derivation з `ct2-transformers-converter`) | Повна відтворюваність; модель — частина closure | CC-BY-NC-4.0 ⇒ `meta.license` unfree, будь-який push у binary cache = редистрибуція NC-артефакту; inventory має `buildOnTarget = true` ⇒ **кожен хост** тягне 2.46 GB + torch CPU closure і конвертує; життєвий цикл моделі прив'язаний до деплою коду; суперечить overview §4.1 (`<stateDir>/models`) |
| **N2. Конвертація при першому старті сервісу** | Нуль ручних кроків | Рантайм-closure сервісу тягне `torch`+`transformers` (саме те, що ми прибираємо); +2.5 GB RAM/диску на старті; повільний cold start |
| **N3. Завантаження попередньо конвертованого CT2-репо (pinned revision + sha256) у `<stateDir>/models`; окремий offline-інструмент конвертації для перевірки й альтернатив** | Рантайм MT = `ctranslate2 + sentencepiece` (без torch!); 623 MB замість 2.46 GB; однаковий механізм для ASR/MT/TTS; CT2 `model.bin` — не pickle (нема виконання коду при завантаженні) | Довіра до третьої сторони ⇒ компенсуємо pin'ом sha256 + **gate якості** проти власної конвертації |

**Рішення: N3.** Default-джерело NLLB — `JustFrederik/nllb-200-distilled-600M-ct2-int8@302d78f…` з pin'ом sha256 усіх файлів. Gate: harness порівнює його з власною конвертацією (`scripts/model_tools/convert_nllb_ct2.py`, devShell `model-tools` з CPU-torch з cache.nixos.org) на FLORES+ devtest; |ΔchrF| ≤ 0.5 — інакше default переводиться на власну конвертацію (локальний шлях або приватне дзеркало, §9 OQ5).

### 3.2 Пакування Kokoro (TTS)

| Варіант | Збірка | VRAM / CPU | Оцінка |
|---|---|---|---|
| **T1. `torch-bin` + cachix** (поточна спроба у `plans/todo.md`) | `torch-bin` (cu130) все одно тягне cudaPackages; override ламає хеші ⇒ промах кешу; `cuda-maintainers.cachix.org` не має потрібних шляхів (F7, F8) | torch CUDA ctx ~0.4–0.6 GB + модель | **Відхилено** |
| **T2. nixpkgs `kokoro` (torch) як є** | CPU: усе в cache.nixos.org (F6). GPU: з глобальним `cudaSupport=true` усе (включно з libnvshmem) у `cache.nixos-cuda.org` (F7) — source-build **не потрібен** | CPU — ок; GPU — найбільший VRAM-оверхед (torch CUDA context) | Робочий fallback (вже є код `KPipeline`) |
| **T3. PyPI `kokoro-onnx`** | Нема в nixpkgs (F2); його залежності `espeakng-loader`/`phonemizer-fork` теж нема ⇒ пакувати 3 пакети; G2P лише espeak (гірше за misaki) | ORT | Відхилено (більше пакування, гірший G2P) |
| **T4. Власний тонкий ORT-рушій + G2P з `kokoro.KPipeline(model=False)`** | Моделі: `onnx-community/Kokoro-82M-v1.0-ONNX`. Залежності: `onnxruntime` + `kokoro` (для misaki G2P; torch лише імпортується, CPU, з кешу) | ORT CUDA ctx менший за torch; на CPU ORT зазвичай швидший за eager torch | **Рекомендовано** (default), T2 — fallback `RT_TTS_ENGINE=torch` |

**Рішення: T4 default, T2 fallback**, остаточно підтверджується harness'ом (§6.4: ONNX CPU RTF ≤ torch CPU RTF і round-trip WER не гірший на > 2 п.п.; якщо ні — default = T2). Обидва рушії використовують **той самий** G2P (misaki через KPipeline), тож порівнюється лише синтез. Головне правило пакування (для підпроєкту 2, прийняте — overview §7 C2): **ніколи не override'ити torch → torch-bin; GPU-варіант будувати з окремого інстансу `import <nixpkgs споживача> { config = { allowUnfree = true; cudaSupport = true; }; }` з дефолтними `cudaCapabilities` і substituter `cache.nixos-cuda.org`.** Інстанс створюється всередині package-функції/overlay (SP2 `nix/packages/variant-pkgs.nix`, `import pkgs.path {…}`), тож глобальний `pkgs` інфри не змінюється (infra R1). Рушій TTS вибирається `RT_TTS_ENGINE` всередині backend-у `RT_TTS_BACKEND=kokoro` підпроєкту 1 (Task 12a плану).

### 3.3 ROCm для ASR/MT

| Варіант | Оцінка |
|---|---|
| R1. CT2 `rocmSupport = true`, `rocmGpuTargets = [ "gfx900" ]` для NOTE17 (штатна опція nixpkgs ≥ `7a0f122`, F3) | Пакування — не проблема (C1); ризики апаратні: gfx90c офіційно не підтримується (потрібен `HSA_OVERRIDE_GFX_VERSION=9.0.0`), gfx900 не в списку протестованих upstream/форків; відомі memory faults; iGPU має 0.5 GiB carve-out, вже зайнятий llama; source-build на таргеті (ROCm-варіанти не в кеші) | 
| R2. CPU int8 на ROCm-хостах | 5700U 8 потоків тягне whisper-small int8 та NLLB-600M int8 з запасом (оцінка, перевіряється harness'ом); нуль ризику збірки |

**Рішення: R2 default; R1 — time-boxed апаратний spike (≤ 4 год) у плані**, приймається лише якщо RTF(rocm) ≤ 0.66·RTF(cpu) і 30-хв soak без faults. `rt-asr-rocm`/`rt-mt-rocm` пакуються (SP2, той самий nixpkgs-override) як «experimental», але жоден хост їх не деплоїть, доки spike не дав GO (§8 D1).

---

## 4. Дизайн

### 4.1 Компоненти

```
src/services/common/
  model_registry.py   # ModelSpec/FileSpec + REGISTRY (pinned repo, revision, sha256, license)
  model_store.py      # ensure()/verify(): <RT_MODEL_DIR>/rt/<alias>, manifest, lock, atomic install
  hf_env.py           # HF_HOME, HF_HUB_OFFLINE, token з файлу
  device.py           # RT_DEVICE → бекенд-девайс, default compute type, потоки
  models_cli.py       # `rt-models list|fetch|verify|path`
src/services/asr/whisper_engine.py   # WhisperEngine (faster-whisper) + VAD/decoding профілі
src/services/mt/nllb_codes.py        # ISO 639-1 ↔ FLORES-200
src/services/mt/sentence_split.py    # розбиття на речення перед батчем
src/services/mt/ct2_translator.py    # Ct2NllbTranslator (ctranslate2 + sentencepiece, БЕЗ transformers)
src/services/tts/g2p.py              # KokoroG2P (KPipeline(model=False))
src/services/tts/kokoro_onnx.py      # KokoroOnnxSynthesizer (onnxruntime)
scripts/model_tools/                 # convert_nllb_ct2.py, pin_model.py (offline, devShell model-tools)
scripts/bench/                       # harness: asr|mt|tts|matrix|fetch-data, thresholds.yml, matrix.yml
nix/bench-shells.nix                 # devShells: bench-cpu, bench-gpu, model-tools (структура flake — SP2)
src/services/mt/ctranslate2_backend.py  # Task 12a: SP1 MtBackend поверх Ct2NllbTranslator (+ зміни SP1 settings/backends: defaults, RT_TTS_ENGINE)
```

Кожен юніт має одну відповідальність і тестується окремо з фейками (`ctranslate2.Translator`, `WhisperModel`, `onnxruntime.InferenceSession`, G2P), без справжніх моделей. Engine-класи — це те, що FastAPI-застосунки підпроєкту 1 створюють у фоновому завантажувачі; HTTP тут не реалізується. Підключення до протоколів SP1 (`MtBackend`, `AsrBackend`, `TtsBackend`) — тонкі адаптери в Task 12a плану: `RT_MT_BACKEND=ctranslate2` (новий default), `FasterWhisperBackend` → `WhisperEngine`, `KokoroBackend` → `create_synthesizer(RT_TTS_ENGINE)` (§8 D8).

Потік завантаження (усередині фонового loader'а сервісу):
`env → device.resolve() → model_registry.get(RT_MODEL) → model_store.ensure() (verify/download) → hf_env.set_offline() → Engine(path, device, compute_type, threads) → warmup → ready=true`.

### 4.2 MT: NLLB-200-distilled-600M на CTranslate2

**Модель:** `nllb-200-distilled-600M-ct2-int8` (alias у реєстрі). Ваги збережені int8; тип обчислень задається при завантаженні (`ctranslate2.Translator(path, device, compute_type)`):

| RT_DEVICE | CT2 device | default `RT_COMPUTE_TYPE` | Примітка |
|---|---|---|---|
| `cpu` | `cpu` | `int8` | `intra_threads=RT_NUM_THREADS`, `inter_threads=1` |
| `cuda` | `cuda` | `int8_float16` | sm_89 (RTX 4060) підтримує |
| `rocm` | `cuda` (HIP-збірка CT2 зберігає ім'я) | `int8_float16` | лише після spike R1 |

**Токенізація без transformers:** `sentencepiece.SentencePieceProcessor(model_file=<dir>/sentencepiece.bpe.model)`.
- source tokens = `[src_code] + sp.encode(text, out_type=str) + ["</s>"]` (новий, не-legacy порядок NLLB-токенізатора); шматки, яких нема у словнику моделі (перевірено: `—` → HF дає `<unk>`), явно замінюються на `<unk>`;
- `target_prefix = [[tgt_code]]` для кожного елемента батчу;
- вихід: `hypotheses[0][1:]` (відкинути мовний токен) → `sp.decode(tokens)`.
- Еквівалентність з HF `AutoTokenizer(src_lang=…)` перевіряється **golden-фікстурою** (генерується один раз у devShell `model-tools` скриптом, комітиться як JSON; тест у CI не потребує transformers).

**Мапінг мов:** API — ISO 639-1; усередині FLORES-200 (`uk→ukr_Cyrl`, `pl→pol_Latn`, `en→eng_Latn`, + поточна таблиця з `src/translate/translate_service.py`: de, fr, es, it, pt, ru, ja, zh→zho_Hans, ko, ar). Невідомий код → `UnsupportedLanguage` (сервіс віддає 400). Код, що вже має вигляд `xxx_Xxxx`, пропускається як є, якщо він є у словнику NLLB.

**Батчинг і декодування:**
- Вхід `str | list[str]`; кожен текст ділиться на речення (`sentence_split`: регекс по `[.!?…]+` + пробіл + велика літера/лапки/цифра; речення > 400 sp-токенів ріжуться по комах/жорстко). NLLB навчений на реченнях; довгі багатореченнєві входи деградують.
- Усі речення всіх текстів — **один** виклик `translate_batch(max_batch_size=16, batch_type="examples")`, потім збирання назад (join через пробіл).
- Параметри: `beam_size` = `RT_MT_NUM_BEAMS` (default 4 на cuda, 2 на cpu), `max_input_length=512`, `max_decoding_length=256`, `repetition_penalty=1.1`, `no_repeat_ngram_size=0`, `return_scores=False`.
- Порожні/пробільні тексти повертаються як `""` без виклику моделі.
- Пост-обробка (капіталізація/крапка з поточного `_post_process`) — **не** в рушії (рішення API, підпроєкт 1).

**Пам'ять CT2 на GPU:** CT2 використовує caching allocator; для обмеження "зарезервованого, але не використаного" — env `CT2_CUDA_CACHING_ALLOCATOR_CONFIG=4,3,12,209715200` (max cached 200 MiB). Ефект вимірюється harness'ом (VRAM з/без); якщо виграш < 100 MiB — не вмикаємо.

**Якість/латентність (uk→en, pl→en):** FLORES+ devtest (перші 200 речень `ukr_Cyrl`, `pol_Latn`, `eng_Latn`), метрика chrF2 (sacrebleu) і p50/p95 латентності одного речення. Гейти — §6.4.

**Альтернативи (коротко):**

| Модель | Параметри | Ліцензія | CT2 int8 на диску | VRAM (int8_float16, оцінка, з CUDA ctx) | Коментар |
|---|---|---|---|---|---|
| **NLLB-200-distilled-600M** | 615M | CC-BY-NC-4.0 | 0.62 GB | ~0.9–1.0 GB | **default**; одна модель на всі напрямки |
| NLLB-200-distilled-1.3B | 1.37B | CC-BY-NC-4.0 | ~1.4 GB | ~1.7–1.9 GB | +якість, але не вміщається поряд з turbo+llama на cyborg |
| opus-mt-tc-big-zle-en + -zlw-en (Marian) | ~119M кожна | **CC-BY-4.0** | ~0.13 GB кожна | ~0.4–0.5 GB обидві в одному процесі | uk→en BLEU 39.2 (flores101); комерційно дозволено; потрібен окремий `Ct2MarianTranslator` (без мовних токенів, source/target.spm) — опційне завдання плану, якщо OQ1 = "комерційне використання" |
| MADLAD-400-3B-MT | 3B | Apache-2.0 | ~3 GB | ~3.5 GB | не вміщається |
| opus-mt-uk-en (Marian base, поточний код) | ~75M | CC-BY-4.0 | ~0.08 GB | ~0.3 GB | слабша якість; лише як аварійний fallback |

### 4.3 ASR: faster-whisper за класом пристрою

**Вибір моделі:**

| Клас | Default `RT_MODEL` | `RT_COMPUTE_TYPE` | VRAM/RAM (оцінка, з ctx) | Обґрунтування |
|---|---|---|---|---|
| cuda, бюджет ≥ 1.6 GiB (cyborg) | `whisper-large-v3-turbo` | `int8_float16` | ~1.2–1.5 GB VRAM | точність близька до large-v3 для uk/pl, декодер 4 шари ⇒ швидкість близька до medium |
| cuda, бюджет 0.8–1.6 GiB | `whisper-small` | `int8_float16` | ~0.5–0.7 GB | компроміс; medium (~1.0–1.3 GB) не дає виграшу над turbo за розміром |
| rocm (NOTE17) | `whisper-small` на **cpu** | `int8` | ~0.9–1.2 GB RAM | §3.3 R2 |
| cpu ≥ 8 потоків | `whisper-small` | `int8` | ~0.9–1.2 GB RAM | `medium` int8 — тільки якщо harness покаже RTF ≤ 0.5 |
| cpu ≤ 4 потоки | `whisper-small` | `int8` | — | **не real-time**; лише batch (фаза 4) |

`large-v3` (1.55B, ~2–2.5 GB int8) і `distil-large-v3*` (тільки англійська) — не розглядаються для uk/pl real-time. Точність для uk/pl: публічні FLEURS-оцінки дають порядок large-v3 ≥ turbo > medium ≫ small; конкретні WER на нашому домені **вимірює harness** (власні записи + FLEURS test), пороги — §6.4.

**Декодування (default):** `language` завжди явний, якщо відомий (uk часто детектиться як ru — автодетект вимкнений, коли мова задана); `condition_on_previous_text=False` (менше петель/галюцинацій у стрімінгу); `temperature=(0.0, 0.2, 0.4)` (обмежений fallback); `no_speech_threshold=0.6`; `compression_ratio_threshold=2.4`; `log_prob_threshold=-1.0`; `beam_size` = `RT_ASR_BEAM_SIZE` (default 1 на cpu, 3 на cuda) для final; для partial — завжди `beam_size=1`, `temperature=0.0` і `without_timestamps=True`. `initial_prompt` — лише з запиту.

**VAD:**

| Профіль | Використання | Параметри |
|---|---|---|
| `STREAMING` | endpointing у `WS /v1/audio/stream` (підпроєкт 1 реалізує цикл; тут — константи й рушій) | `threshold=0.5`, `neg_threshold=0.35`, `min_speech_duration_ms=250`, `min_silence_duration_ms=400`, `speech_pad_ms=200`, `max_speech_duration_s=12` (примусовий final) |
| `FILE` | `vad_filter=True` у `/v1/audio/transcriptions` | `threshold=0.5`, `min_silence_duration_ms=500`, `speech_pad_ms=300`, `max_speech_duration_s=30` |

Поточні значення (`threshold=0.35`, `speech_pad_ms=800`) завищують чутливість і підклеюють тишу — замінюються. `min_silence_duration_ms` напряму визначає затримку фіналізації; harness робить sweep 300/400/600 мс і фіксує вибір (§6.3).

**CPU/GPU параметри:** `WhisperModel(path, device, compute_type, cpu_threads=RT_NUM_THREADS, num_workers=1)`. `BatchedInferencePipeline` — не default (росте VRAM), лише для batch-файлів у фазі 4.

**Альтернатива на майбутнє:** `parakeet-tdt-0.6b-v3` (CC-BY-4.0; uk 6.79 % / pl 7.31 % FLEURS WER; дуже швидкий) — потребує NeMo або ONNX-рантайму (`onnx-asr`/`sherpa-onnx`, нема в нашому стеку). Окремий spike після цього підпроєкту (OQ6).

### 4.4 TTS: Kokoro-82M

**Рушій `onnx` (default):**
- Модель: `kokoro-82m-v1.0-onnx` (`onnx-community/Kokoro-82M-v1.0-ONNX`); файли: `onnx/model.onnx`, `onnx/model_fp16.onnx`, `onnx/model_quantized.onnx`, `tokenizer.json`, `voices/*.bin`.
- `RT_COMPUTE_TYPE` для TTS: `float32` → `model.onnx` (default, cpu і cuda), `float16` → `model_fp16.onnx` (лише cuda), `int8` → `model_quantized.onnx` (лише cpu). Default змінюється тільки за результатом harness'у.
- G2P: `KPipeline(lang_code="a"|"b", model=False)` → для кожного чанку `phonemes` (≤510); `input_ids = [0] + [vocab[c] for c in phonemes if c in vocab] + [0]` (vocab з `tokenizer.json` → `model.vocab`).
- Голос: `voices/<voice>.bin` = float32 `(510, 1, 256)`; `style = pack[len(phonemes) - 1]` (форма `(1, 256)`).
- ORT inputs: `input_ids` int64 `[1, N]`, `style` float32 `[1, 256]`, `speed` float32 `[1]` → `waveform` float32 @ **24 000 Гц**.
- Провайдери: cpu → `["CPUExecutionProvider"]` (`intra_op_num_threads=RT_NUM_THREADS`); cuda → `["CUDAExecutionProvider", "CPUExecutionProvider"]` з `arena_extend_strategy=kSameAsRequested` (менше VRAM-overhead).
- Стрімінг: генератор по чанках KPipeline → перший чанк віддається одразу (time-to-first-audio).

**Рушій `torch` (fallback, `RT_TTS_ENGINE=torch`):** поточний підхід (`KPipeline` з `KModel`), моделі з `kokoro-82m-v1.0-torch` (`hexgrad/Kokoro-82M`), файли передаються локальними шляхами (`KModel(config=…, model=…)`, `load_voice("<dir>/voices/x.pt")`).

**Голоси (en):** default `RT_TTS_VOICE=af_heart` (найвища оцінка якості в Kokoro VOICES.md); також `af_bella`, `am_michael`, `am_fenrir` (чоловічі), `bf_emma` (британська, `lang_code="b"`). Поточний `am_adam` з `config/default.yml` має низьку оцінку у v1.0 — рекомендація для edge-конфігу (підпроєкт 2). Мова голосу визначає `lang_code` за першою літерою (`a*`→`a`, `b*`→`b`); інші мови TTS — поза скоупом (вихід завжди en).

**CPU fallback:** на всіх хостах, крім випадку виміряного запасу VRAM ≥ 1.0 GiB, TTS працює на CPU (VRAM-first). Гейт латентності CPU — §6.4.

### 4.5 Вимоги до Nix, що випливають з вибору моделей (для підпроєкту 2)

1. **Два інстанси nixpkgs**: CPU — `pkgs` споживача як є; CUDA — `import pkgs.path { config = { allowUnfree = true; cudaSupport = true; }; }` (без зміни `cudaCapabilities` — інакше промах кешу), створений **всередині** package-функції/overlay (SP2 `variant-pkgs.nix`), не глобально; `rocm` — `ctranslate2.override { rocmSupport = true; }` у `pkgs` споживача (D1). Для devShells bench — те саме в `nix/bench-shells.nix`.
2. **Substituter** `https://cache.nixos-cuda.org` / `cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M=` у системних `nix.settings` CUDA-хостів (інфра, підпроєкт 3, Task 7a — до першого `rt-*-gpu` деплою). На cyborg `trusted-users = root deploy`, тож `--option extra-substituters` від звичайного користувача **ігнорується** — без системного налаштування `bench-gpu`/`rt-*-gpu` почнуть source-build. `nixConfig.extra-substituters` у flake — лише підказка.
3. **Заборонено** override `torch → torch-bin` (F8).
4. Рантайм-залежності за сервісом:
   - `rt-asr`: `faster-whisper` (тягне `ctranslate2`, `onnxruntime` для Silero), `numpy`, `huggingface-hub` (+ `pysilero-vad` для серверного endpointing підпроєкту 1);
   - `rt-mt`: `ctranslate2`, `sentencepiece`, `huggingface-hub` — **без `transformers`/`torch`**;
   - `rt-tts`: `onnxruntime`, `kokoro` (+ `en_core_web_sm` wheel, як зараз у `tts.nix`), `numpy`, `huggingface-hub`.
   - усі: спільні залежності SP1 (`fastapi`, `uvicorn`, `soundfile`, `scipy`, …) і `httpx` (воркер SP4). Повний перелік — SP2 spec §5.4 (`nix/packages/deps.nix`).
5. `rt-models` CLI — entry point у тому ж bundle кожного сервісу (`src.services.common.models_cli:main`).
6. `model-tools` (конвертер; `torch` CPU, `transformers`, `ctranslate2`) — **лише devShell**, не входить у bundle.

### 4.6 VRAM-бюджет за хостами

Оцінки — до вимірювання; інтеграційні завдання плану (Phase E) замінюють їх виміряними піками і фіксують тут.

**cyborg — RTX 4060 Laptop, 8188 MiB (дисплей на Intel iGPU)**

| Компонент | Варіант | VRAM (оцінка) |
|---|---|---|
| llama-service (коли модель завантажена) | Qwen3.5-4B Q4_K_M, ctx 16384, all layers | ~3.9 GiB (ваги ~2.7 + KV 0.5 (F15) + compute ~0.4 + ctx ~0.3) |
| rt-asr-gpu | whisper-large-v3-turbo, int8_float16 | ~1.3 GiB |
| rt-mt-gpu | NLLB-600M, int8_float16 | ~0.9 GiB |
| rt-tts-cpu | Kokoro ONNX fp32, 4 потоки | 0 |
| **Сума** | | **~6.1 GiB**, запас ≥ 1.3 GiB; **гейт: ≤ 7.4 GiB виміряно** |

Драбина деградації, якщо гейт не пройдено: (1) ASR → `whisper-small` (−~0.7 GiB); (2) MT → cpu (−~0.9 GiB); (3) запропонувати інфрі llama `ctxSize` 16384 → 8192 (−256 MiB; рішення не наше). TTS → GPU лише якщо після всіх вимірів лишається ≥ 1.0 GiB.

Цей бюджет — **узгоджене розміщення** (overview §7 C3): SP3 §7 ставить на cyborg `rt-asr-gpu` **і** `rt-mt-gpu` за умови PASS gate `system-cyborg` (≤ 7.4 GiB = 7577 MiB, без OOM; план Task 19 Step 5). Крок (2) драбини = SP3-fallback `rt-mt-cpu` на іншому хості (kira-kiosk) або `rt-mt-gpu` на SKTOP-NOTE27-NIXOS (за його VRAM-правилом нижче).

**SKTOP-NOTE27-NIXOS — NVIDIA, VRAM невідомий (F17)**

Правило вибору за виміряними `V` (total), `L` (пік llama): `F = V − L − 0.6 GiB`.

| F | ASR | MT | TTS |
|---|---|---|---|
| ≥ 2.3 GiB | gpu, large-v3-turbo | gpu | cpu |
| 1.0–2.3 GiB | gpu, small | gpu, якщо `F − 0.6 ≥ 0.9`, інакше cpu | cpu |
| < 1.0 GiB | cpu, small | cpu | cpu |

Застереження: мало RAM (~4 GB за коментарем) — CPU-варіанти ASR+MT+TTS разом (~3–3.5 GB RSS) можуть не вміститись (OQ2).

**SKTOP-NOTE17-NIXOS — Vega 8 iGPU (0.5 GiB carve-out + 7.5 GiB GTT), 14 GiB RAM**

| Компонент | Варіант | Ресурс |
|---|---|---|
| llama | ROCm, ≤1 GiB GGUF | carve-out + GTT |
| rt-asr-cpu | whisper-small int8, `RT_NUM_THREADS=4` | ~1.0 GB RAM |
| rt-mt-cpu | NLLB-600M int8, `RT_NUM_THREADS=2` | ~0.9 GB RAM |
| rt-tts-cpu | Kokoro ONNX, `RT_NUM_THREADS=2` | ~1.0–1.3 GB RAM (torch імпортується для misaki) |

Сумарно ~3.2 GB RAM з 14 GiB. Ризик — CPU-конкуренція з інтерактивним використанням (власник свідомо тримає llama на GPU, щоб звільнити CPU) — OQ4.

**CPU-бокси (kira-kiosk, sktop-nixos-top — 2-потокова llama, Tier-1)**

Real-time ролі не мають. Допустимо лише batch (фаза 4): `rt-mt-cpu` (NLLB int8, `RT_NUM_THREADS=2`, ~0.9 GB RAM) і `rt-tts-cpu`. ASR — лише batch `whisper-small` int8.

### 4.7 Провізіонування моделей

**Розкладка `<stateDir>/models` (= `RT_MODEL_DIR`):**
```
<RT_MODEL_DIR>/
  hf/                         # HF_HOME (внутрішній кеш huggingface_hub)
  rt/<alias>/                 # snapshot_download(local_dir=…), стабільний шлях для рушіїв
  rt/<alias>/.rt-manifest.json
  rt/<alias>.lock             # fcntl-lock (сервіс і CLI не гоняться)
```

**Реєстр (`model_registry.py`):** `ModelSpec(alias, service, repo_id, revision, files: tuple[FileSpec(path, size, sha256)], license)`. Додаються лише перелічені alias'и:

| alias | service | repo@revision | файли (тут sha256 скорочено; повні 64-hex значення LFS-файлів — у плані, Task 2; sha256 малих не-LFS файлів обчислює `scripts/model_tools/pin_model.py`) |
|---|---|---|---|
| `whisper-small` | asr | `Systran/faster-whisper-small@536b066…` | `model.bin` (3e305921…d671), `config.json`, `tokenizer.json`, `vocabulary.txt` |
| `whisper-medium` | asr | `Systran/faster-whisper-medium@08e178d…` | `model.bin` (9b45e100…51ae), … |
| `whisper-large-v3-turbo` | asr | `dropbox-dash/faster-whisper-large-v3-turbo@0a363e9…` | `model.bin` (e76620f8…18da), `config.json`, `preprocessor_config.json`, `tokenizer.json`, `vocabulary.json` |
| `nllb-200-distilled-600M-ct2-int8` | mt | `JustFrederik/nllb-200-distilled-600M-ct2-int8@302d78f…` | `model.bin` (ed1beaf7…707b), `sentencepiece.bpe.model` (14bb8dfb…555a), `shared_vocabulary.txt`, `config.json` |
| `kokoro-82m-v1.0-onnx` | tts | `onnx-community/Kokoro-82M-v1.0-ONNX@1939ad2…` | `onnx/model.onnx` (8fbea51e…34cb), `onnx/model_fp16.onnx` (ba4527a8…334a), `onnx/model_quantized.onnx` (fbae9257…1478), `tokenizer.json`, англійські голоси `voices/a?_*.bin`, `voices/b?_*.bin` (28) |
| `kokoro-82m-v1.0-torch` | tts | `hexgrad/Kokoro-82M@f3ff357…` | `kokoro-v1_0.pth`, `config.json`, англійські `voices/a?_*.pt`, `voices/b?_*.pt` |

**`RT_MODEL`:** alias з реєстру **або** абсолютний шлях до директорії (напр. власна конвертація). Довільні HF repo id не приймаються (цілісність).

**`ensure(spec)`:**
1. Взяти lock.
2. Якщо `rt/<alias>/.rt-manifest.json` існує і `revision` збігається, а розміри файлів збігаються (quick) / sha256 збігаються (`RT_MODEL_VERIFY=full`) → повернути шлях.
3. Якщо нема/невалідно і `RT_MODEL_DOWNLOAD=true` → `snapshot_download(repo_id, revision=<sha>, local_dir=rt/<alias>.partial, allow_patterns=<files>, token=<з RT_HF_TOKEN_FILE або None>)` → sha256 **кожного** файла проти реєстру → `os.replace(partial, rt/<alias>)` → записати manifest (`alias, repo_id, revision, files{path: {size, sha256}}, verified_at`).
4. Інакше → `ModelNotProvisioned("run: rt-models fetch <alias>")`.
5. Помилка sha256 → `ModelIntegrityError` (partial видаляється), сервіс `ready=false`, повтор з backoff 60 s → … → 30 хв (логіка повторів — в loader'і підпроєкту 1; тут — винятки).

Для абсолютного шляху: якщо є manifest — quick-verify; якщо нема — warning "unmanaged model", використовується як є.

**Offline:** після успішного `ensure()` усіх моделей сервісу loader викликає `hf_env.set_offline()` → `HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1` **до** першого імпорту `faster_whisper`/`kokoro`/`huggingface_hub`-залежних модулів рушія (рушії імпортують їх ліниво). `HF_HOME=<RT_MODEL_DIR>/hf` встановлюється завжди.

**HF token:** `RT_HF_TOKEN_FILE` (опційно, напр. `/run/secrets/hg-token`, як `hfApiTokenFile` у llama); читається з файлу і передається явно `token=`; ніколи не в env/Nix store. Обрані моделі не gated — токен потрібен лише для FLORES+ (bench, dev-машина).

**CLI `rt-models`:** `list` (alias, service, ліцензія, стан), `fetch <alias>... [--model-dir]`, `verify [--full] [<alias>...]`, `path <alias>`. Exit-коди: 0 ok, 2 не провізіоновано, 3 integrity error.

### 4.8 Env (доповнення до overview §4.2)

| Env | Default | Сервіси | Значення |
|---|---|---|---|
| `RT_MODEL_DIR` | (overview) модуль ставить `<stateDir>/models`; без env (dev) — `~/.cache/rt-speech/models` | всі | корінь моделей |
| `RT_MODEL` | asr: `whisper-large-v3-turbo` якщо `RT_DEVICE=cuda`, інакше `whisper-small`; mt: `nllb-200-distilled-600M-ct2-int8`; tts: `kokoro-82m-v1.0-onnx` | всі | alias або абсолютний шлях |
| `RT_COMPUTE_TYPE` | §4.2 / §4.3 / §4.4 за device | всі (TTS — розширення) | CT2 compute type; для TTS `float32|float16|int8` |
| `RT_NUM_THREADS` | `min(4, os.cpu_count())` | всі | CPU-потоки рушія |
| `RT_MODEL_DOWNLOAD` | `true` | всі | дозволити завантаження при старті |
| `RT_MODEL_VERIFY` | `quick` | всі | `quick` (розміри) / `full` (sha256) при старті |
| `RT_HF_TOKEN_FILE` | — | всі | файл з HF токеном |
| `RT_TTS_ENGINE` | `onnx` | tts | `onnx` / `torch` |
| `RT_ASR_BEAM_SIZE` | 1 (cpu) / 3 (cuda) | asr | (overview) default уточнено |
| `RT_MT_NUM_BEAMS` | 2 (cpu) / 4 (cuda) | mt | (overview) default уточнено |
| `RT_TTS_VOICE` | `af_heart` | tts | (overview) default уточнено |

---

## 5. Обробка помилок

| Ситуація | Поведінка |
|---|---|
| Модель не провізіонована, download вимкнено | `ModelNotProvisioned`; `/status.ready=false`, `model.error` з командою `rt-models fetch …` |
| sha256 mismatch | `ModelIntegrityError`, partial видалено, модель не завантажується |
| Мережа недоступна при download | виняток `huggingface_hub` загортається в `ModelNotProvisioned(cause)` |
| `compute_type` не підтримується пристроєм | `device.resolve_compute_type()` перевіряє `ctranslate2.get_supported_compute_types(device)`; fallback-ланцюг `int8_float16 → int8 → float32` з warning (CT2 і сам конвертує, але ми логуємо явно) |
| `RT_DEVICE=cuda`, але CUDA недоступна | помилка старту (не мовчазний CPU-fallback — бюджет VRAM/CPU планується явно) |
| Невідома мова (MT) | `UnsupportedLanguage` |
| Невідомий голос (TTS) | `UnknownVoice` (перелік з `voices/`) |
| Порожній текст/аудіо | порожній результат без виклику моделі |

---

## 6. Тестування і benchmark harness

### 6.1 Unit-тести (pytest, без моделей)
- `model_registry`: унікальність alias, валідні sha256 (64 hex), ревізії — 40 hex, кожен spec має ≥ 1 файл.
- `model_store`: ensure з фейковим downloader'ом (створює файли), quick/full verify, mismatch → виняток і чистка partial, manifest round-trip, вже-провізіоновано → без download, download вимкнено → `ModelNotProvisioned`.
- `hf_env`: set_offline виставляє env; token з файлу (strip), відсутній файл → помилка.
- `device`: таблиця defaults, fallback compute type з фейковим `get_supported_compute_types`.
- `nllb_codes`, `sentence_split`: таблично.
- `ct2_translator`: фейковий Translator фіксує `source`/`target_prefix`/параметри; фейковий SP; golden-фікстура токенізації (якщо файл є).
- `whisper_engine`: фейковий `WhisperModel` фіксує kwargs (VAD-профіль, language, beam).
- `kokoro_onnx`: фейкова ORT-сесія і G2P → правильні `input_ids`/`style`-індекс/конкатенація.
- `scripts/bench/metrics`: RTF, перцентилі, WER-нормалізація, chrF; `thresholds`: оцінка pass/fail.

### 6.2 Harness: структура
```
python -m scripts.bench asr   --model <alias|path> --device cpu|cuda|rocm --compute-type … --threads N --beam N --vad-silence-ms N --data bench-data/asr --langs uk,pl --out bench-results/<host>/<name>.json
python -m scripts.bench mt    --model … --device … --compute-type … --beams N --data bench-data/mt --pairs uk-en,pl-en --out …
python -m scripts.bench tts   --engine onnx|torch --model … --device … --compute-type … --voice af_heart --data bench-data/tts/sentences.txt --roundtrip-asr <alias> --out …
python -m scripts.bench system --duration 300 --out … --gate system-cyborg   # llama завантажена + ASR + MT паралельно
python -m scripts.bench matrix --matrix scripts/bench/matrix.yml --profile cyborg|note17|cpu-box --out-dir bench-results/<host>/
python -m scripts.bench fetch-data --out bench-data [--hf-token-file F] [--fleurs-limit 100]
```
- Кожен кейс матриці запускається в **окремому підпроцесі** (свіжий CUDA context — VRAM виміряється чесно, включно з контекстом).
- Ресурси: семплер 20 Гц — NVML `nvmlDeviceGetComputeRunningProcesses` (per-PID `usedGpuMemory`) і загальний `nvmlDeviceGetMemoryInfo`; ROCm — `/sys/class/drm/card*/device/mem_info_{vram,gtt}_used` (delta від baseline); CPU — `psutil` peak RSS, CPU %.
- Метрики: load time; warmup виключається; ASR — RTF = Σ processing / Σ audio, латентність на сегмент p50/p95, WER (jiwer; нормалізація: lowercase, без пунктуації, схлопнути пробіли); MT — chrF2 (sacrebleu), латентність на речення p50/p95, throughput (речень/с) батчем 16; TTS — RTF, time-to-first-chunk p50/p95, sanity (не тиша: RMS > 0.01, без NaN, тривалість у [0.5×, 2×] від `слова/2.5 с`), round-trip WER (TTS → ASR `whisper-small` → WER з вхідним текстом).
- Вихід: JSON на кейс + `summary.md` (таблиця) + exit 1, якщо будь-який кейс з `gate: true` не пройшов пороги.

### 6.3 Дані
- `bench-data/asr/{uk,pl}/<id>.wav` (16 кГц mono) + `<id>.txt`: (а) FLEURS test (`google/fleurs` parquet `uk_ua`/`pl_pl`, `--fleurs-limit 100`), (б) власні записи з мікрофона edge-машини (≥ 20 на мову) — реалістичний домен.
- `bench-data/mt/flores_devtest.{ukr_Cyrl,pol_Latn,eng_Latn}.txt` — перші 200 рядків FLORES+ devtest (`openlanguagedata/flores_plus`, gated auto-approve, CC-BY-SA-4.0).
- `bench-data/tts/sentences.txt` — 20 англійських речень 5–25 слів (комітиться в `scripts/bench/data/tts_sentences.txt`).
- `bench-data/`, `bench-results/` — у `.gitignore`.

### 6.4 Пороги прийняття (`scripts/bench/thresholds.yml`)

| Сервіс / профіль | Пороги |
|---|---|
| ASR cuda (turbo, int8_float16, beam 3) | RTF ≤ 0.10; латентність сегмента ≤ 10 с p95 ≤ 0.6 с; WER FLEURS uk ≤ 12 %, pl ≤ 10 %; VRAM peak ≤ 1.6 GiB |
| ASR cpu (small, int8, 4 потоки, beam 1) | RTF ≤ 0.35; p95 ≤ 2.0 с; WER FLEURS uk ≤ 25 %, pl ≤ 20 %; RSS ≤ 1.5 GiB |
| MT cuda (NLLB int8_float16, beam 4) | chrF2 ≥ floor (uk→en 52, pl→en 48 — ловить зламану токенізацію/мовні коди); p95 речення ≤ 150 мс; VRAM ≤ 1.1 GiB |
| MT cpu (NLLB int8, 4 потоки, beam 2) | ті самі chrF-floors; p95 ≤ 700 мс; RSS ≤ 1.5 GiB |
| MT gate джерела | \|chrF2(JustFrederik) − chrF2(власна конвертація)\| ≤ 0.5 на кожній парі |
| MT gate квантизації (одноразово, dev) | chrF2(CT2 int8) ≥ chrF2(HF fp32, transformers) − 1.0 |
| TTS cpu (onnx fp32, 4 потоки) | RTF ≤ 0.35; first-chunk p95 ≤ 0.8 с; sanity 100 %; round-trip WER ≤ 15 % |
| TTS cuda (onnx) | RTF ≤ 0.05; first-chunk p95 ≤ 0.25 с; VRAM ≤ 0.9 GiB |
| TTS engine choice | onnx лишається default, якщо RTF(onnx, cpu) ≤ RTF(torch, cpu) і round-trip WER(onnx) ≤ WER(torch) + 2 п.п. |
| Система cyborg | llama завантажена + ASR + MT під 5-хв навантаженням (паралельні запити): `nvidia-smi` used ≤ 7.4 GiB, жодного OOM |

WER-пороги — початкові; якщо large-v3-turbo не проходить на FLEURS, рішення (поріг vs модель) — за власником, з цифрами з `summary.md`.

---

## 7. Чого цей підпроєкт **не** робить
- HTTP/WS ендпоїнти, loader-потік, `/status` (підпроєкт 1) — лише рушії, реєстр, провізіонування.
- Bundle/NixOS-модуль/`rt-*-{cpu,gpu,rocm}` пакети (підпроєкт 2) — лише вимоги §4.5 і devShells для harness'у.
- Розгортання на хостах, sops-секрети, substituter на хостах (підпроєкт 3).
- Видалення старих `src/translate`, `src/whisper`, `src/tts` сервісів — після міграції на нові (підпроєкти 1/2).

---

## 8. Deviations from overview

| # | Overview каже | Цей spec | Причина |
|---|---|---|---|
| D1 | §2: "`ctranslate2` у nixpkgs з CUDA і `rocmSupport`" | **Пакувальна частина знята (overview §7 C1):** у старому lock `89570f2` опції не було, у nixpkgs інфри (`7a0f122`) є, SP2 Task 0 піднімає lock. Лишається апаратна: `rt-asr-rocm`/`rt-mt-rocm` пакуються як «experimental», але не деплояться, доки spike R1 (gfx90c + `HSA_OVERRIDE_GFX_VERSION`) не дав GO; ROCm-хости використовують `-cpu` | Перевірено в джерелах обох ревізій nixpkgs |
| D2 | §4.1: варіанти `cpu/gpu/rocm` для всіх сервісів | `rt-tts-rocm` **не надається**; ROCm-хости → `rt-tts-cpu` | ORT ROCm EP видалено (F11); MIGraphX EP (`onnxruntime.rocmSupport` у nixpkgs) на gfx90c не перевірений; 82M модель на iGPU не варта ризику. Причина — ORT, не torch |
| D3 | §4.2: `RT_COMPUTE_TYPE` для ASR/MT | Також для TTS (`float32|float16|int8` → ONNX-файл) | Один механізм вибору точності |
| D4 | §4.2: перелік env | Додано `RT_NUM_THREADS`, `RT_MODEL_DOWNLOAD`, `RT_MODEL_VERIFY`, `RT_HF_TOKEN_FILE`, `RT_TTS_ENGINE`; уточнено defaults `RT_ASR_BEAM_SIZE`, `RT_MT_NUM_BEAMS`, `RT_TTS_VOICE` | Потрібні для провізіонування/CPU-хостів |
| D5 | §4.2: `RT_MODEL` = "id/шлях" | alias з реєстру або абсолютний шлях; довільні HF id не приймаються | Цілісність (pin ревізії + sha256) |
| D6 | §4.1: `<stateDir>/models` (`HF_HOME=<stateDir>/models/hf`) | Плюс `<stateDir>/models/rt/<alias>/` для snapshot'ів з manifest | Стабільні шляхи для рушіїв, rsync-придатність |
| D7 | §2 (через `plans/todo.md`): torch source-build — блокер | Не блокер: глобальний `cudaSupport=true` + `cache.nixos-cuda.org` (F7); override torch→torch-bin заборонено | Перевірено `nix path-info` |
| D8 | §4.1: `src/services/{asr,mt,tts}/` — FastAPI app | Цей spec додає туди модулі рушіїв (`whisper_engine.py`, `ct2_translator.py`, `kokoro_onnx.py`, `g2p.py`) і `common/model_*.py`, а також адаптери до протоколів SP1 (`mt/ctranslate2_backend.py`, зміни в SP1 `*_backend.py`/`settings.py` — план Task 12a). Env: SP1 `RT_*_BACKEND` вибирає backend, SP5 `RT_TTS_ENGINE` — рушій усередині `kokoro`-backend-у | Підпроєкти пишуться паралельно; одна інтеграційна задача замість дублювання |
| D9 | §2: "Kokoro-82M < 2 GB" | Оцінка ~0.6–0.9 GiB на GPU, але **default — CPU** на всіх поточних хостах | VRAM-first |

---

## 9. Відкриті питання та припущення

**Припущення:**
- A1. Вихід завжди англійський; TTS — лише `lang_code` `a`/`b`.
- A2. Налаштування llama на хостах не змінюються цим підпроєктом (лише рекомендації в драбині деградації).
- A3. На cyborg дисплей лишається на Intel iGPU (PRIME offload), NVIDIA VRAM — для llama + rt-*.
- A4. Edge (UI) не використовує GPU для моделей після міграції (UI-процес тримає лише capture/playback/VAD Silero на CPU).
- A5. ROCm-збірка CT2 (nixpkgs `rocmSupport`) використовує device-рядок `cuda` (перевіряється у spike R1).

**Відкриті питання:**
- **OQ1 (важливе). Ліцензія NLLB — CC-BY-NC-4.0.** Якщо сервіси використовуватимуться комерційно (Django engine для компанії), NLLB заборонений. Альтернатива з близькою якістю для uk→en — `opus-mt-tc-big-zle-en` + `-zlw-en` (CC-BY-4.0); план містить опційне завдання `Ct2MarianTranslator`. Потрібне рішення власника.
- OQ2. NOTE27: фактичний VRAM GPU і модель llama; мало RAM (~4 GB?) — чи вміщаються CPU-fallback'и.
- OQ3. llama вантажить модель ліниво. Для системного виміру план викликає `POST http://127.0.0.1:8080/models/load?model_name=Qwen3.5-4B.Q4_K_M.gguf` (знайдено в `common/modules/llm/app/api/routes_models.py`) + один chat-запит; чи потрібна авторизація на цьому ендпоїнті — перевірити на хості.
- OQ4. NOTE17: чи прийнятно навантажувати CPU (ASR+MT+TTS ~8 потоків) з огляду на те, що власник тримає llama на GPU, щоб звільнити CPU.
- OQ5. NLLB-джерело: лишати pinned сторонній репо (якщо gate пройдено) чи створити приватне дзеркало в HF-організації.
- OQ6. Spike `parakeet-tdt-0.6b-v3` (ONNX) як ASR для uk/pl — після цього підпроєкту.
- OQ7. Доступ `rt-*` system-user'ів до `/run/secrets/hg-token` (sops owner/group) — підпроєкт 3; для обраних моделей не обов'язково.
- OQ8. CPU-бекенд CT2: OpenBLAS+Ruy (кеш) vs `withOneDNN=true` (source-build ~10–20 хв) — harness може порівняти int8 на x86; за замовчуванням — кешований.

---

## 10. Джерела
- [CTranslate2 CHANGELOG](https://github.com/OpenNMT/CTranslate2/blob/master/CHANGELOG.md), [CMakeLists.txt (WITH_HIP)](https://github.com/OpenNMT/CTranslate2/blob/master/CMakeLists.txt), [quantization docs](https://opennmt.net/CTranslate2/quantization.html), [Transformers/NLLB guide](https://opennmt.net/CTranslate2/guides/transformers), [issue #2021 ROCm fault](https://github.com/OpenNMT/CTranslate2/issues/2021), [ctranslate2-rocm notes](https://github.com/paralin/ctranslate2-rocm/blob/rocm/ROCM.md)
- [faster-whisper utils.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/utils.py), [vad.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/vad.py)
- [kokoro pipeline.py](https://github.com/hexgrad/kokoro/blob/main/kokoro/pipeline.py), [misaki en.py](https://github.com/hexgrad/misaki/blob/main/misaki/en.py), [misaki espeak.py](https://github.com/hexgrad/misaki/blob/main/misaki/espeak.py), [onnx-community/Kokoro-82M-v1.0-ONNX](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX)
- [ONNX Runtime 1.23 release (ROCm EP removed)](https://newreleases.io/project/pypi/onnxruntime/release/1.23.0)
- [NixOS CUDA cache / wiki](https://wiki.nixos.org/wiki/CUDA), [Nix + Flox + NVIDIA CUDA redistribution](https://discourse.nixos.org/t/nix-flox-nvidia-opening-up-cuda-redistribution-on-nix/69189)
- [opus-mt-tc-big-zle-en benchmark_results](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-zle-en/raw/bc43e02d5641dd41d82f7396c8447fa013c26f07/benchmark_results.txt), [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [Qwen/Qwen3.5-4B config](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/config.json)
