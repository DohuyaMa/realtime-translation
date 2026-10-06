# Speech services API (ASR / MT / TTS) — design

Дата: 2026-10-06
Підпроєкт: **1 з 5** (див. `docs/superpowers/specs/2026-10-06-distributed-speech-services-overview.md`, далі — *overview*).
Класифікація: **architectural**. Напрямок **A** затверджено. Інтерактивного Q&A не було — рішення прийняті
агентом за делегуванням; припущення і відкриті питання — у розділах 13–14. Користувач переглядає spec пізніше.
Узгоджено з іншими підпроєктами 2026-10-06 (overview §6–§7): devShell — `nix/devshell.nix` (SP2 Task 1 першим); defaults моделей/beam-ів і CT2/ORT-рушії підключає SP5 Task 12a; `context` у MT не додається.

---

## 1. Мета і межі

### 1.1 Що будуємо
Три незалежні, stateless FastAPI-сервіси у `src/services/{asr,mt,tts}/` зі спільним `src/services/common/`:

| Сервіс | Пакет | Запуск (цей підпроєкт) | Порт |
|---|---|---|---|
| `rt-asr` | `src/services/asr/` | `python -m src.services.asr` | 8091 |
| `rt-mt`  | `src/services/mt/`  | `python -m src.services.mt`  | 8092 |
| `rt-tts` | `src/services/tts/` | `python -m src.services.tts` | 8093 |

Кожен сервіс: HTTP API за контрактом overview §4.3, `/health` (liveness), `/status` (readiness),
опційний bearer-токен з `RT_API_TOKEN_FILE`, конфіг лише з env (overview §4.2), фонове завантаження моделі,
backend-інтерфейс із fake-реалізацією для тестів.

### 1.2 Критерії успіху
1. `pytest tests/services` зелений у `nix develop` без GPU, без мережі і без завантаження моделей (fake backends).
2. Opt-in повільні тести (`RT_SLOW_TESTS=1 pytest -m slow tests/services`) проходять з реальними моделями
   (faster-whisper `tiny`, `facebook/nllb-200-distilled-600M`, `hexgrad/Kokoro-82M`) на CPU.
3. Кожен сервіс стартує командою `RT_PORT=… python -m src.services.<svc>`; `/health` відповідає **≤ 10 с після старту
   процесу навіть на найслабшому CPU-хості** (вимога deploy-rs health-check), бо важкі бібліотеки імпортуються лише у
   фоновому завантаженні (§4.1); `/status.ready` стає `true` після завантаження.
4. Жоден сервіс не має глобального мутабельного per-client стану: мова/голос/швидкість — параметри запиту.
5. `rt-tts` не імпортує `sounddevice` і нічого не відтворює.
6. Нові Python-залежності додані тільки через `nix/devshell.nix` (SP2 Task 1; fallback — `flake-global/dev/devshell.nix`, якщо SP2 Task 1 ще не виконано), без pip/venv; runtime-залежності bundle — SP2 `nix/packages/deps.nix`.

### 1.3 Поза межами (явно)
- Міграція edge-клієнта (`src/translation_system.py`, `DirectAdapter`) на HTTP/WS — підпроєкт 2.
- Видалення legacy-сервісів `src/whisper/*`, `src/translate/*`, `src/tts/*` і `src/common/ipc.py` — підпроєкт 2
  (поки edge ними користується, їх не чіпаємо; виняток — §10.3).
- Nix-пакети `rt-{asr,mt,tts}-*`, `bin/rt-*`, `pyproject` scripts, NixOS-модуль, HM-модуль — підпроєкт 2.
- Інфра (swarm-nix) — підпроєкт 3. Django worker — підпроєкт 4 (тут лише точка підключення, §9).
- CTranslate2 для MT, ORT-рушій Kokoro (`RT_TTS_ENGINE`), реєстр моделей (alias-и `RT_MODEL`), device-залежні defaults, VRAM-бюджет — підпроєкт 5 (тут лише swappable backend; підключення — SP5 plan Task 12a, overview §7 C2).
- Wyoming-проксі як ASR backend (див. §4.7 і Open questions).

---

## 2. Дослідження коду (факти, на які спирається дизайн)

| Факт | Де | Наслідок |
|---|---|---|
| `whisper_service.py` тримає **глобальний** rolling-context `_context: deque(maxlen=5)` між усіма клієнтами | `src/whisper/whisper_service.py:122` | Контекст стає per-session (WS) або `prompt` у запиті (HTTP). |
| `_filter_repetitions()` — корисний фільтр повторів | `src/whisper/whisper_service.py:30` | Переносимо у `src/services/asr/textfilter.py`, legacy імпортує звідти (§10.3). |
| Endpointing на edge: Silero (pysilero-vad) 512-семплові вікна, `SPEECH_PROB=0.5`, `SILENCE_SEC=0.7`, `MIN_SPEECH_SEC=0.4`, `MAX_CHUNK_SEC=8`, energy-fallback `RMS≥0.008` | `src/translation_system.py:349-510` | Ці значення — дефолти серверного endpointer-а (перевірені в експлуатації). |
| `pysilero_vad.SileroVoiceActivityDetector` — **stateful** (`reset()`), `process_chunk()` вимагає рівно 1024 байти PCM16 16 kHz | `pysilero-vad 3.3.x` | Один VAD-екземпляр на WS-сесію; VAD ≠ GPU, працює на CPU. |
| `translate_service._load_nllb` використовує `tokenizer.lang_code_to_id` | `src/translate/translate_service.py:126` | У devshell **transformers 5.5.4** цього атрибута немає → legacy NLLB-шлях зламаний. Новий backend: `tokenizer.convert_tokens_to_ids(flores)`. |
| `FLORES_CODES["ar"] = "ara_Arab"` | `src/translate/translate_service.py:33` | У NLLB-200 код — `arb_Arab`. Виправляємо в новій таблиці. |
| `set_languages` у MT перевантажує модель; Marian-моделі — по одній на пару мов | `translate_service.py:225` | Новий MT — **тільки NLLB** (одна модель, всі пари, мова — параметр запиту). Marian не переносимо. |
| `TTSEngine` імпортує `sounddevice`, відкриває `OutputStream`, має внутрішню чергу і `set_voice` | `src/models/tts_engine.py` | Не перевикористовуємо `TTSEngine`; новий `KokoroBackend` напряму обгортає `kokoro.KModel`/`KPipeline` (логіка з `TextToSpeech` у тому ж файлі). |
| `kokoro 0.9.4`: `KPipeline(lang_code, model=KModel|bool, device=…)`, `KModel(repo_id=…)`; голос → мова за першою літерою (`af_heart` → `a`) | devshell | Один `KModel`, кеш `KPipeline` на `lang_code` (lazy). |
| `faster-whisper 1.2.1` `transcribe(audio: np.ndarray | file, language, beam_size, temperature, initial_prompt, vad_filter, without_timestamps, condition_on_previous_text, no_speech_threshold, …)` | devshell | Backend-обгортка над цим API. |
| `src/models/whisper_recognition.py` — ollama-обгортка, не використовується | — | Не перевикористовуємо. |
| `libsndfile 1.2.2` у devshell підтримує MP3 | devshell | Декодування upload-ів через `soundfile` (WAV/FLAC/OGG/MP3) без PyAV. |
| У nixpkgs (lock репо) є: `fastapi 0.135.3`, `uvicorn 0.40.0`, `python-multipart 0.0.22`, `httpx 0.28.1`, `websockets 16.0`, `pytest-timeout 2.4.0` | `nix eval` | Додаємо в devshell. У devshell їх зараз **немає** (`import fastapi` → ModuleNotFoundError). |
| Root `flake.nix` (flake-utils) імпортує `flake-global/dev/devshell.nix`; у `flake-global/` немає власного `flake.nix` | `flake.nix:22` | Стан на момент дослідження. SP2 Task 1 (виконується першим, overview §6) замінює його на `nix/devshell.nix` — залежності додаємо туди. |
| `tests/` без `__init__.py`; root `conftest.py` ставить `TEST_MODE=true`, додає корінь у `sys.path` | `tests/conftest.py` | Нові тести в `tests/services/` з унікальними basename (`test_svc_*.py`), без `__init__.py`. |

---

## 3. Розглянуті підходи

**A1 (обрано): один FastAPI-процес на сервіс + один inference-потік з пріоритетною чергою.**
Модель живе в процесі; всі звернення до моделі серіалізуються через `InferenceWorker` (1 потік, `PriorityQueue`).
Event loop лише приймає запити, VAD і кодування аудіо. Просто, передбачувано по VRAM, легко тестувати.

**A2: uvicorn з кількома workers / процес-пул.** Кожен worker вантажить свою копію моделі → множить VRAM,
несумісно з бюджетом (overview: VRAM-обмеження). Відкинуто.

**A3: dynamic batching (збирати кілька запитів у батч для GPU).** Дає throughput, але ускладнює код і latency;
для одного edge + рідкого batch — передчасно. Інтерфейс backend-у (`translate(texts: list)`) не заважає додати це пізніше.

**Партіали ASR (окремий вибір):**
- **P1 (обрано): періодичний re-decode зростаючого буфера поточної репліки до endpoint-а**, latest-wins, низький пріоритет.
- P2: LocalAgreement-2 (whisper_streaming) — стабільний committed-префікс; складніше, потребує word timestamps. Кандидат на майбутнє.
- P3: без партіалів — суперечить контракту (`partial` у WS-протоколі).

---

## 4. Архітектура

### 4.1 Структура файлів

```
src/services/
  __init__.py
  common/
    __init__.py
    version.py        # SERVICE_VERSION = "0.1.0"
    env.py            # env_str/env_int/env_float/env_bool/env_csv + SettingsError
    settings.py       # BaseSettings (service, backend, RT_HOST, RT_PORT, RT_DEVICE, RT_MODEL, RT_COMPUTE_TYPE,
                      #   RT_MODEL_DIR, RT_API_TOKEN_FILE, RT_LOG_LEVEL, RT_MAX_PENDING) + base_from_env() + apply_model_dir()
    auth.py           # load_token(), require_bearer (HTTP dependency), check_ws_token()
    errors.py         # ApiError + OpenAI-style error handler
    logs.py           # setup_logging(level) — loguru + перехоплення stdlib logging (uvicorn)
    worker.py         # InferenceWorker, Priority, WorkerBusy
    runtime.py        # ServiceRuntime: стан моделі (loading/ready/failed), backend, worker, wait_ready()
    gpu.py            # vram_mb() — nvidia-smi per-PID, кеш 5 с; None для cpu/rocm або при помилці
    health.py         # build_health_router(runtime, settings) → /health, /status
    app.py            # create_service_app(settings, runtime, token, routers, ws_routers, lifespan_hooks) — lifespan, error handlers
    serve.py          # run_service(settings_loader, app_factory) -> exit code; uvicorn.run(workers=1)
    audio.py          # decode_audio_file(bytes) → float32 16k mono; pcm16_to_float(); float_to_pcm16(); resample()
  asr/
    __init__.py
    __main__.py       # main(): settings з env → create_app() → serve()
    settings.py       # AsrSettings
    backend.py        # AsrBackend Protocol, DecodeOptions, Segment, Transcription, FakeAsrBackend, make_backend()
    faster_whisper_backend.py
    textfilter.py     # filter_repetitions() (перенесено з legacy)
    vad.py            # VadModel Protocol, SileroVad, EnergyVad, make_vad()
    endpointer.py     # Endpointer — чистий state machine над послідовністю speech-prob
    stream.py         # StreamSession (sync, тестований) → PartialRequest/FinalRequest
    ws.py             # WS-handler /v1/audio/stream (async, планує decode-и через runtime)
    http.py           # POST /v1/audio/transcriptions
    app.py            # create_app(settings=None, backend_factory=None, vad_factory=None)
  mt/
    __init__.py, __main__.py, settings.py
    backend.py        # MtBackend Protocol, FakeMtBackend, make_backend()
    transformers_backend.py   # NLLB через HF transformers
    languages.py      # ISO 639-1 ↔ NLLB (FLORES-200) + назви
    http.py           # POST /v1/translate, GET /v1/languages
    app.py
  tts/
    __init__.py, __main__.py, settings.py
    backend.py        # TtsBackend Protocol, Voice, FakeTtsBackend, make_backend()
    kokoro_backend.py
    voices.py         # статичний каталог голосів Kokoro-82M v1.0
    encoding.py       # wav_bytes(), wav_stream_header(), pcm_bytes()
    http.py           # POST /v1/audio/speech, GET /v1/audio/voices
    app.py
tests/services/
  conftest.py         # slow-маркер, RT_SLOW_TESTS, фікстури TestClient з fake backends
  test_svc_*.py       # див. §11
```

**Lazy imports (жорстка вимога, крос-вимога інфри/deploy-rs).** Health-check deploy-rs робить 20×1 с спроб у 30-секундному
вікні підтвердження, тому `/health` має відповідати **≤ 10 с після `systemctl restart` на найслабшому CPU-хості**. Отже:
- `torch`, `ctranslate2`, `faster_whisper`, `transformers`, `kokoro`, `onnxruntime`, `huggingface_hub`, `sentencepiece` (і `scipy`, `soundfile`) імпортуються **лише всередині**
  `load()`/робочих методів реальних backend-ів або в helper-функціях (`resample`, `decode_audio_file`) — ніколи на рівні модуля;
- `load()` виконується у фоновому потоці, який стартує в lifespan, тобто **після** того, як uvicorn почав слухати порт;
- `make_backend()` імпортує модуль реального backend-у (`faster_whisper_backend.py` тощо), але ці модулі самі на рівні модуля
  імпортують лише `numpy`/`loguru`;
- тест `tests/services/test_svc_lazy_imports.py` у підпроцесі імпортує всі три `app`-модулі, створює застосунки з **реальними**
  backend-ами (дефолтний env), виконує `GET /health` і перевіряє, що жоден із перелічених модулів (плюс `sounddevice`, `scipy`)
  не потрапив у `sys.modules`.

### 4.2 Потік запиту (усі сервіси)

```
HTTP/WS ──► FastAPI (event loop) ──► валідація, auth, декодування аудіо
                                    │
                                    ▼
                        ServiceRuntime.run(fn, priority)
                                    │  (asyncio.wrap_future)
                                    ▼
                InferenceWorker: 1 потік, PriorityQueue ──► backend.<method>()  (GPU/CPU)
```

### 4.3 `ServiceRuntime` і стан моделі

```python
class ModelState(str, Enum): LOADING = "loading"; READY = "ready"; FAILED = "failed"

class ServiceRuntime:
    def __init__(self, service: str, backend_factory: Callable[[], Backend], max_pending: int): ...
    backend: Backend | None
    state: ModelState
    error: str | None
    load_seconds: float | None
    worker: InferenceWorker
    def start(self) -> None          # запускає worker і фоновий потік load: backend = factory(); backend.load()
    def stop(self) -> None           # зупиняє worker (pending → cancel), join
    def wait_ready(self, timeout: float) -> bool
    def require_ready(self) -> Backend   # ApiError 503 model_not_ready (+Retry-After: 5) або model_failed
    async def run(self, fn, *args, priority: Priority = Priority.REQUEST, **kwargs) -> Any
    def submit_threadsafe(self, fn, *args, priority: Priority = Priority.REQUEST,
                          bypass_limit: bool = False, **kwargs) -> concurrent.futures.Future  # WS і Django worker (§9)
```

- Завантаження моделі — у **окремому** daemon-потоці (не в inference-потоці), як у llama: процес слухає порт одразу.
- Якщо `load()` падає: `state=FAILED`, `error=str(e)`, процес **не завершується** (рішення; див. Open questions Q3).
  `/v1/*` → 503 `model_failed`.

### 4.4 Модель конкурентності

| | ASR | MT | TTS |
|---|---|---|---|
| uvicorn workers | 1 (жорстко) | 1 | 1 |
| Inference-потоки | 1 (`InferenceWorker`) | 1 | 1 |
| Що в event loop | приймання WS-фреймів, VAD (Silero ~0.05 мс/вікно, CPU), endpointing, декодування upload | валідація | WAV-заголовок, черга стрім-чанків (ресемплінг і PCM16 — у job-і inference-потоку) |
| Пріоритети | `FINAL=0` (WS final) < `REQUEST=1` (HTTP) < `PARTIAL=2` | все `REQUEST` | все `REQUEST` |
| Обмеження черги | `RT_MAX_PENDING` (16): HTTP → 503 `server_busy`; partial → тихо відкинуто; final — **ніколи** не відкидається (обходить ліміт) | `RT_MAX_PENDING` → 503 | `RT_MAX_PENDING` → 503 |
| Ліміт сесій | `RT_ASR_MAX_SESSIONS` (4): понад — WS close `1013` | — | — |

Обґрунтування одного inference-потоку: faster-whisper/CTranslate2, torch-модель NLLB і Kokoro не гарантують
потокобезпечності одного екземпляра моделі; копія на потік множить VRAM. Серіалізація також дає детермінований
VRAM-пік (вимога підпроєкту 5). Черга **непреемптивна**: довгий HTTP-запит блокує realtime-фінали до свого завершення
(обмеження задокументоване; великі batch-задачі — окремий інстанс, Open questions Q5).

`InferenceWorker`:
```python
class Priority(IntEnum): FINAL = 0; REQUEST = 1; PARTIAL = 2

class WorkerBusy(Exception): ...

class InferenceWorker:
    def __init__(self, max_pending: int, name: str): ...
    def start(self) -> None
    def stop(self, timeout: float = 5.0) -> None
    @property
    def pending(self) -> int                       # у черзі + виконується
    def submit(self, fn, *args, priority: Priority = Priority.REQUEST,
               bypass_limit: bool = False) -> concurrent.futures.Future
        # raise WorkerBusy якщо pending >= max_pending і не bypass_limit
        # FIFO у межах одного пріоритету (seq-лічильник); скасований Future пропускається
```

Скасування: `Future.cancel()` до початку виконання → job пропускається (`set_running_or_notify_cancel()`).
Job, що вже виконується, не переривається; для TTS-стрімінгу є кооперативний `threading.Event` (§7.3).

### 4.5 Конфіг

Вся конфігурація — з env (жодного `~/.config/.../config.yml` у нових сервісах). `Settings.from_env(environ)`
повертає frozen dataclass; невалідне значення → `SettingsError` → `main()` друкує помилку і виходить з кодом 2.

Спільні (overview §4.2):

| Env | Default | Примітка |
|---|---|---|
| `RT_HOST` | `127.0.0.1` | |
| `RT_PORT` | 8091 / 8092 / 8093 | |
| `RT_DEVICE` | `cpu` | `cpu`\|`cuda`\|`rocm`. `rocm` → бібліотекам передається `"cuda"` (ROCm-збірки torch і CTranslate2 використовують ім'я `cuda`). |
| `RT_MODEL` | ASR `small`; MT `facebook/nllb-200-distilled-600M`; TTS `hexgrad/Kokoro-82M` | **тимчасово** — SP5 Task 12a замінює на alias-и реєстру (SP5 §4.8, D5): ASR `whisper-large-v3-turbo` (cuda) / `whisper-small`; MT `nllb-200-distilled-600M-ct2-int8`; TTS `kokoro-82m-v1.0-onnx`; довільні HF id тоді не приймаються |
| `RT_COMPUTE_TYPE` | ASR: `int8` на cpu, `float16` на gpu; MT: `float32` на cpu, `float16` на gpu | MT/transformers: `float16`/`int8_float16` → `torch.float16`; інше → `float32` (+warning для `int8*`). Після SP5 Task 12a: ASR/MT cpu `int8`, cuda/rocm `int8_float16`; TTS `float32`. |
| `RT_MODEL_DIR` | не задано | якщо задано і `HF_HOME` не задано → `HF_HOME=$RT_MODEL_DIR/hf` до імпорту HF-бібліотек |
| `RT_API_TOKEN_FILE` | не задано | §5 |
| `RT_LOG_LEVEL` | `info` | |
| `RT_MAX_PENDING` | `16` | **додано** (Deviations D2) |

ASR: `RT_ASR_BACKEND` (`faster-whisper`\|`fake`), `RT_ASR_LANGUAGE` (не задано = autodetect), `RT_ASR_BEAM_SIZE` (5; після SP5 — 1 cpu / 3 cuda),
`RT_ASR_VAD` (`true` — `vad_filter` для HTTP-транскрипції), `RT_ASR_VAD_THRESHOLD` (0.5),
`RT_ASR_MIN_SPEECH_MS` (400), `RT_ASR_ENDPOINT_SILENCE_MS` (700), `RT_ASR_MAX_UTTERANCE_S` (10),
`RT_ASR_PREROLL_MS` (300), `RT_ASR_PARTIALS` (`true`), `RT_ASR_PARTIAL_INTERVAL_MS` (600),
`RT_ASR_MAX_SESSIONS` (4), `RT_ASR_MAX_UPLOAD_MB` (25).

MT: `RT_MT_BACKEND` (`transformers`\|`fake`; `ctranslate2` додає підпроєкт 5 і робить **default** — у bundle `transformers`/`torch` немає), `RT_MT_SOURCE_LANG` (`uk`),
`RT_MT_TARGET_LANG` (`en`), `RT_MT_NUM_BEAMS` (4; після SP5 — 2 cpu / 4 cuda), `RT_MT_MAX_LENGTH` (256), `RT_MT_REPETITION_PENALTY` (1.2; CT2-бекенд SP5 — 1.1),
`RT_MT_MAX_BATCH` (64), `RT_MT_MAX_CHARS` (2000).

TTS: `RT_TTS_BACKEND` (`kokoro`\|`fake`), `RT_TTS_ENGINE` (додає підпроєкт 5: `onnx` default \| `torch` — рушій усередині `kokoro`-backend-у), `RT_TTS_VOICE` (`af_heart`), `RT_TTS_SPEED` (1.0),
`RT_TTS_SAMPLE_RATE` (24000), `RT_TTS_MAX_CHARS` (4096).

### 4.6 Спільні HTTP-ендпоінти

`GET /health` → `200 {"status":"ok"}` завжди, коли процес слухає (без auth).

`GET /status` (без auth — Django health-service і deploy-rs читають його без токена):
```json
{"service":"rt-asr","version":"0.1.0","device":"cuda","backend":"faster-whisper",
 "model":{"id":"small","loaded":true,"state":"ready","load_seconds":3.2,"error":null},
 "ready":true,"vram_mb":812,"pending":0}
```
Завжди HTTP 200 (readiness — у полі `ready`, як у контракті). `vram_mb` — `nvidia-smi --query-compute-apps=pid,used_memory`
для власного PID при `RT_DEVICE=cuda`, кеш 5 с; інакше `null`. Поля `backend`, `model.state`, `model.load_seconds`,
`model.error`, `pending` — **додані** (D3).

Формат помилок (OpenAI-стиль) для всіх `/v1/*`:
```json
{"error":{"message":"…","type":"invalid_request_error","code":"unsupported_language"}}
```
Коди: 400 `invalid_request` / `invalid_audio` / `unsupported_language` / `unknown_voice` / `unsupported_format`,
401 `unauthorized`, 413 `payload_too_large`, 422 (валідація FastAPI, стандартний формат FastAPI), 503 `model_not_ready` /
`model_failed` / `server_busy` (з `Retry-After`).

### 4.7 Backend-інтерфейси

Усі backend-и: атрибути `name: str` (`"faster-whisper"`, `"fake"`, …), `model_id: str`; метод `load() -> None`
(блокуючий, викликається один раз у фоновому потоці); робочі методи — синхронні, викликаються **лише** з inference-потоку.

```python
# ASR
@dataclass(frozen=True)
class DecodeOptions:
    language: str | None
    prompt: str | None
    temperature: float = 0.0
    beam_size: int = 5
    vad_filter: bool = False
    without_timestamps: bool = False

@dataclass(frozen=True)
class Segment: start: float; end: float; text: str
@dataclass(frozen=True)
class Transcription: text: str; language: str | None; duration: float; segments: list[Segment]

class AsrBackend(Protocol):
    name: str; model_id: str
    def load(self) -> None: ...
    def transcribe(self, audio: np.ndarray, opts: DecodeOptions) -> Transcription: ...   # float32, 16 kHz, mono

# MT
class MtBackend(Protocol):
    name: str; model_id: str
    def load(self) -> None: ...
    def translate(self, texts: list[str], src: str, tgt: str, num_beams: int) -> list[str]: ...   # src/tgt — NLLB-коди

# TTS
@dataclass(frozen=True)
class Voice: id: str; language: str; gender: str
class TtsBackend(Protocol):
    name: str; model_id: str; sample_rate: int      # нативна частота backend-у (Kokoro: 24000)
    def load(self) -> None: ...
    def voices(self) -> list[Voice]: ...
    def prepare_voice(self, voice: str) -> None: ...   # будує G2P-пайплайн мови; UnsupportedVoiceLanguage якщо неможливо
    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[np.ndarray]: ...   # float32 mono чанки
```

Fake-реалізації (у `backend.py` кожного сервісу, використовуються тестами і `RT_*_BACKEND=fake` для розробки edge
у підпроєкті 2 без GPU):
- `FakeAsrBackend(text_fn=None, load_delay=0.0, fail_on_load=None)`: за замовчуванням `text = f"speech {duration:.2f}s"`,
  `language = opts.language or "en"`, один сегмент; записує всі виклики у `calls: list[DecodeOptions]`.
- `FakeMtBackend`: `f"[{tgt}] {text}"`; `calls`.
- `FakeTtsBackend(unsupported_prefixes=())`: `prepare_voice` кидає `UnsupportedVoiceLanguage` для заданих префіксів; на кожне речення (split по `[.!?]`) — синус 440 Гц тривалістю `0.05 с × len(речення) / speed`,
  амплітуда залежить від голосу (детерміновано, щоб тест міг розрізнити голоси); `sample_rate=24000`.

`make_backend(settings)` — фабрика за `RT_*_BACKEND`; невідоме значення → `SettingsError`.

**Сумісність з рушіями підпроєкту 5 (overview §7 C2).** Протоколи вище — точка розширення, яку SP5 Task 12a реалізує без змін HTTP-шару: `Ct2MtBackend` (`RT_MT_BACKEND=ctranslate2`, default) поверх `Ct2NllbTranslator` (приймає NLLB-коди як є); `FasterWhisperBackend` делегує в `WhisperEngine` (VAD-профіль `FILE_VAD` замість параметрів §6.5); `KokoroBackend` делегує в `create_synthesizer(RT_TTS_ENGINE)` — ORT-рушій (default; misaki G2P через `KPipeline(model=False)`) або torch `KPipeline` (fallback). `TtsBackend` для обох рушіїв однаковий: `sample_rate = 24000`, `synthesize()` — float32-чанки, `prepare_voice()` кидає `UnsupportedVoiceLanguage` для мов, яких рушій не підтримує (SP5: лише `a`/`b`). Моделі — через `prepare_model()` реєстру SP5 (`<RT_MODEL_DIR>/rt/<alias>`).

---

## 5. Автентифікація

- `RT_API_TOKEN_FILE` не задано → auth вимкнено.
- Задано → файл за **буквальним шляхом** з env (напр. `/run/credentials/rt-asr.service/api-token` — systemd `LoadCredential`,
  або шлях sops-nix) читається **один раз при старті** (`strip()`); жодних підстановок/пошуку. Файл відсутній, нечитабельний
  або порожній → `SettingsError` (fail closed, код 2).
- HTTP: залежність `require_bearer` на `APIRouter(prefix="/v1")`; порівняння `hmac.compare_digest`; помилка → 401
  `{"error":{"code":"unauthorized"}}` + `WWW-Authenticate: Bearer`.
- WS `/v1/audio/stream`: токен із заголовка `Authorization: Bearer …` handshake-у (Python-клієнти можуть його
  передати); невалідний → `close(code=1008)` **до** `accept()`. Query-параметр токена **не** підтримується (потрапляє в логи).
- `/health`, `/status` — без auth.
- Токен ніколи не логується.

---

## 6. ASR (`rt-asr`)

### 6.1 `POST /v1/audio/transcriptions` (OpenAI-сумісний, multipart)
Поля: `file` (обов'язково), `model` (приймається й ігнорується — модель одна на інстанс), `language`, `prompt`,
`temperature` (0.0), `response_format` = `json` (default) | `verbose_json` | `text`.

1. Розмір > `RT_ASR_MAX_UPLOAD_MB` → 413.
2. `decode_audio_file(bytes)` (`soundfile` → mono mean → `resample_poly` до 16 kHz → float32); помилка/0 семплів → 400 `invalid_audio`.
3. `DecodeOptions(language=req.language or RT_ASR_LANGUAGE, prompt=req.prompt, temperature, beam_size=RT_ASR_BEAM_SIZE,
   vad_filter=RT_ASR_VAD)` → `runtime.run(backend.transcribe, …, priority=REQUEST)`.
4. Текст сегментів проходить `filter_repetitions`. Відповіді:
   - `json`: `{"text": "...", "language": "uk"}` (поле `language` — розширення OpenAI, є в контракті);
   - `verbose_json`: `{"text","language","duration","segments":[{"id","start","end","text"}]}`;
   - `text`: `text/plain`.

### 6.2 `WS /v1/audio/stream` — протокол

Клієнт → сервер:
- `{"type":"start","language":"uk"|null,"sample_rate":16000,"prompt"?:str,"partials"?:bool}` — першим повідомленням;
  `sample_rate` ≠ 16000 → `error unsupported_sample_rate` + close 1003 (ресемплінг — на edge);
- binary-фрейми PCM16LE mono 16 kHz довільної довжини;
- `{"type":"stop"}`.

Сервер → клієнт:
- `{"type":"ready","session_id":"<uuid4>"}` — після валідного `start` (**додано**, D4);
- `{"type":"partial","utterance_id":3,"text":"…","start":12.48}`;
- `{"type":"final","utterance_id":3,"text":"…","start":12.48,"end":15.02,"language":"uk"}`;
- `{"type":"error","code":"…","message":"…"}`;
- `{"type":"done"}` — після `stop`, коли всі фінали надіслані; потім сервер закриває з'єднання кодом 1000.

`start`/`end` — секунди від початку аудіо сесії (лічильник семплів). `utterance_id` — монотонний int у межах сесії.
Порожній фінальний текст (після фільтрів) не надсилається; partial для вже фіналізованої репліки не надсилається.
Помилки протоколу (binary до `start`, невалідний JSON, невідомий `type`) → `error` + close 1003.
Ліміт сесій перевищено → close 1013 до `accept()`. Модель не готова → `error model_not_ready` + close 1013.

### 6.3 Server-side VAD і endpointing

`VadModel` Protocol: `prob(window: bytes) -> float` (рівно 512 семплів PCM16), `reset()`.
- `SileroVad` — `pysilero_vad.SileroVoiceActivityDetector`, **один на сесію** (stateful);
- `EnergyVad(threshold_rms=0.008)` — fallback, якщо `pysilero_vad` не імпортується (як на edge), і детермінований VAD для тестів.
- `make_vad()` повертає Silero, при `ImportError` — Energy з warning.

`Endpointer` — чистий state machine (без аудіо), крок = одне 32-мс вікно:
```
IDLE ──speech(prob≥thr)──► SPEECH (utterance починається з pre-roll ≤ RT_ASR_PREROLL_MS)
SPEECH: speech_ms += 32 на speech, silence_ms += 32 на тиші (скидається speech-вікном)
  silence_ms ≥ ENDPOINT_SILENCE і speech_ms ≥ MIN_SPEECH  → END(reason="silence") → IDLE
  silence_ms ≥ ENDPOINT_SILENCE і speech_ms <  MIN_SPEECH  → DISCARD (шум) → IDLE
  довжина utterance ≥ MAX_UTTERANCE                        → END(reason="max_length"); нова utterance одразу (стан SPEECH)
stop() у SPEECH: speech_ms ≥ MIN_SPEECH → END(reason="stop"), інакше DISCARD
```
Розріз по `max_length` — без overlap (свідомий компроміс: overlap дає дублікати тексту; edge-версія мала 0.35 с overlap,
бо різала без VAD-контексту). Ризик втрати слова на межі — див. Open questions Q2.

### 6.4 Стратегія партіалів (P1) і latency-цілі

`StreamSession` (sync, без asyncio) приймає байти, ріже на 512-семплові вікна, веде pre-roll ring buffer і буфер
поточної репліки, повертає дії:
- `PartialRequest(utterance_id, audio, start)` — коли в стані SPEECH, `speech_ms ≥ MIN_SPEECH`, partials увімкнено,
  і з моменту попереднього `PartialRequest` цієї репліки додалося ≥ `RT_ASR_PARTIAL_INTERVAL_MS` аудіо;
  `audio` — **весь** буфер репліки від початку (re-decode зростаючого буфера);
- `FinalRequest(utterance_id, audio, start, end)` — на `END`.

WS-handler (`ws.py`) виконує дії:
- **Partial — latest-wins:** якщо для сесії вже є partial у черзі/виконанні — новий **пропускається** (природний backoff:
  на повільному CPU партіали рідшають автоматично). Інакше `submit(priority=PARTIAL)`; при `WorkerBusy` — пропуск.
  Декодування партіалу: `beam_size=1`, `temperature=0`, `without_timestamps=True`, `vad_filter=False`, `prompt` = контекст сесії.
- **Final:** скасовує pending partial цієї репліки (`Future.cancel()`), `submit(priority=FINAL, bypass_limit=True)`,
  `beam_size=RT_ASR_BEAM_SIZE`, `vad_filter=False` (VAD вже відрізав), `prompt` = контекст сесії.
  Фінали надсилаються **строго по порядку** окремою sender-корутиною (черга futures), receive-цикл не блокується.
- **Контекст сесії** (заміна глобального `_context`): `start.prompt` + останні 3 фінали сесії, обрізано до 200 символів з кінця.
  Живе в об'єкті сесії, зникає з з'єднанням.

Latency-цілі (вимірюються від моменту надходження аудіо на сервер; мережа не враховується):

| Метрика | GPU (`small`/`medium`, float16) | CPU (`small`, int8) |
|---|---|---|
| Перший partial після початку мовлення | ≤ 1.0 с (MIN_SPEECH 0.4 + decode ≤ 0.3 с) | best-effort (≈ 1.5–3 с) |
| Каденс партіалів | ~600 мс | ≥ час decode (latest-wins) |
| Final після кінця мовлення | ≤ 1.0 с p50 (ENDPOINT_SILENCE 0.7 + decode ≤ 0.3 с для ≤ 10 с аудіо) | ≤ 0.7 с + 1×довжина репліки |
| VAD-обробка 1 вікна | ≤ 1 мс | ≤ 1 мс |

Цілі — орієнтири для підпроєкту 5; у цьому підпроєкті не є automated-gate (тільки slow-тест логує час).

### 6.5 `FasterWhisperBackend`
- `load()`: `WhisperModel(RT_MODEL, device=map_device(RT_DEVICE), compute_type=RT_COMPUTE_TYPE)`.
- `transcribe()`: `segments, info = model.transcribe(audio, language, beam_size, temperature, initial_prompt=prompt or None,
  vad_filter, vad_parameters=dict(threshold=0.35, min_speech_duration_ms=100, min_silence_duration_ms=500, speech_pad_ms=800)
  (лише якщо vad_filter), no_speech_threshold=0.4, condition_on_previous_text=False, without_timestamps)`;
  `list(segments)`; `text = " ".join(s.text.strip())`; `duration = len(audio)/16000`. `filter_repetitions` застосовує
  API-шар (HTTP і WS), а не backend — однаково для всіх backend-ів. Параметри — перенесені з `whisper_service.py`.

---

## 7. MT (`rt-mt`)

### 7.1 `POST /v1/translate`
Запит: `{"text": str | [str], "source_lang"?: str, "target_lang"?: str, "num_beams"?: int (1..8)}`.
Дефолти мов — `RT_MT_SOURCE_LANG`/`RT_MT_TARGET_LANG`; `num_beams` — `RT_MT_NUM_BEAMS`.
Відповідь: `{"translations": [str], "source_lang": "uk", "target_lang": "en", "model": "facebook/nllb-200-distilled-600M"}`
— `translations` **завжди список** (і для одиночного `text`), порядок = порядок входу.

Валідація: > `RT_MT_MAX_BATCH` елементів або елемент > `RT_MT_MAX_CHARS` символів → 413; невідома мова → 400
`unsupported_language`; `source_lang == target_lang` → повертає вхід без виклику моделі; порожні рядки → `""` без моделі.

### 7.2 Мови
`languages.py`: таблиця ISO 639-1 → `(nllb_code, english_name)` (~55 мов, включно з виправленим `ar → arb_Arab`).
API приймає ISO 639-1 **або** прямий NLLB/FLORES-код (`^[a-z]{3}_[A-Z][a-z]{3}$`) — розширення (D5). `"auto"` → 400
(NLLB потребує явну мову джерела). Відповідь відлунює коди так, як їх передав клієнт.
`GET /v1/languages` → `{"languages":[{"code":"uk","nllb":"ukr_Cyrl","name":"Ukrainian"}, …]}` (сортовано за `code`).

### 7.3 `TransformersNllbBackend`
- `load()`: `AutoTokenizer.from_pretrained(model)`, `AutoModelForSeq2SeqLM.from_pretrained(model, dtype=…)` → `.to(device).eval()`.
- `translate(texts, src, tgt, num_beams)`: мікробатчі по 16; перед токенізацією `tokenizer.src_lang = src`
  (мутація токенізатора безпечна — виклики серіалізовані inference-потоком); `forced_bos_token_id = tokenizer.convert_tokens_to_ids(tgt)`;
  `generate(num_beams, max_length=RT_MT_MAX_LENGTH, repetition_penalty=RT_MT_REPETITION_PENALTY, early_stopping=num_beams>1)`;
  `batch_decode(skip_special_tokens=True)`; `strip()`.
- Пост-обробку legacy (велика літера + крапка) **не переносимо** — це презентаційна логіка edge (Open questions Q6).
- Swappable: `make_backend` вибирає клас за `RT_MT_BACKEND`; підпроєкт 5 додає `ctranslate2_backend.py` (SP5 plan Task 12a), реалізуючи
  той самий `MtBackend` (NLLB-коди однакові), без змін у `http.py`, і робить його default.
- Поле `context` (попередні фрази) **не** входить у контракт (overview §7 C5): edge його не шле.

---

## 8. TTS (`rt-tts`)

### 8.1 `POST /v1/audio/speech`
Запит (JSON): `{"input": str, "voice"?: str, "speed"?: float (0.25..4.0), "response_format"?: "wav"|"pcm" (default "wav"),
"stream"?: bool (false), "model"?: str (ігнорується)}`.

- Порожній `input` → 400; > `RT_TTS_MAX_CHARS` → 413; невідомий голос → 400 `unknown_voice`; формат не `wav|pcm` → 400
  `unsupported_format`.
- Вихід: **завжди** PCM16LE mono (гарантія для edge — SP2 A3), частота `RT_TTS_SAMPLE_RATE` (ресемплінг `resample_poly`, якщо ≠ `backend.sample_rate`).
  Заголовки: `Content-Type: audio/wav` | `audio/pcm`, `X-Sample-Rate: 24000`.
- Перед синтезом `await runtime.run(backend.prepare_voice, voice)` — помилка мови голосу (400) повертається **до** початку
  відповіді, також і для `stream=true`.
- `stream=false`: job у inference-потоці збирає всі чанки (ресемплінг + PCM16) → одна відповідь (WAV з коректним заголовком).
- `stream=true`: `StreamingResponse` (chunked). Job у inference-потоці ітерує `backend.synthesize()` і передає чанки в
  `asyncio.Queue` через `loop.call_soon_threadsafe`; для `wav` першим чанком іде заголовок з розмірами `0xFFFFFFFF`
  (стрімінговий WAV, як у Kokoro-FastAPI); для `pcm` — сирі байти. Якщо клієнт відключився — генератор відповіді
  ставить `cancel_event`, job перевіряє його між чанками і завершується.
- Відтворення на сервері — **немає**; `sounddevice` не імпортується (тест).

### 8.2 `GET /v1/audio/voices`
`{"voices":[{"id":"af_heart","language":"en-us","gender":"female"}, …], "default":"af_heart"}`.
Каталог — статичний список Kokoro-82M v1.0 (54 голоси) у `voices.py`; мова з префікса (`a`=en-us, `b`=en-gb, `e`=es,
`f`=fr, `h`=hi, `i`=it, `j`=ja, `p`=pt-br, `z`=zh), стать — друга літера.

### 8.3 `KokoroBackend`
- `load()`: `KModel(repo_id=RT_MODEL).to(device).eval()`; одразу створює `KPipeline(lang_code=voice[0], model=kmodel)`
  для `RT_TTS_VOICE` (прогрів + перевірка G2P).
- `synthesize(text, voice, speed)`: якщо `voice[0]` немає в `self._pipelines` — створити
  `KPipeline(lang_code=voice[0], model=self._model)` і закешувати (кеш G2P-пайплайнів — внутрішній ресурс backend-у,
  не per-client стан) — це робить `prepare_voice(voice)`; далі `for r in pipeline(text, voice=voice, speed=speed): if r.audio is not None: yield r.audio.numpy().astype(float32)`.
- Помилка створення пайплайна для мови (напр. відсутні G2P-залежності для `j`/`z`) → backend кидає
  `UnsupportedVoiceLanguage` (визначено в `tts/backend.py`); `http.py` мапить її на 400 `unsupported_voice_language`.
  Backend-и не знають про HTTP-типи (`ApiError`).
- `set_voice` / глобальна швидкість — відсутні.

---

## 9. Точка підключення Django worker (підпроєкт 4)

Не реалізуємо, лише гарантуємо:
1. `ServiceRuntime.submit_threadsafe(fn, *args, priority)` — потокобезпечний вхід у ту саму inference-чергу,
   що й HTTP (Django-потік не обходить серіалізацію GPU).
2. `create_service_app(..., lifespan_hooks: Sequence[Callable[[ServiceRuntime], Callable[[], None]]] = ())` у
   `common/app.py` (і прохідний параметр `lifespan_hooks` у кожному `<svc>/app.py:create_app`): кожен hook викликається
   на старті з runtime і повертає функцію зупинки, яку lifespan викликає при shutdown. Підпроєкт 4 додасть
   `src/services/common/django_worker.py` і передасть hook, якщо `RT_WORKER_ENABLED=true`.
3. Робочі функції (`transcribe`, `translate`, `synthesize`) викликаються через backend-інтерфейси, а не через HTTP-шар,
   тому worker зможе виконати `asr_transcribe` / `mt_translate` / `tts_synthesize` без self-HTTP.

Env `RT_WORKER_ENABLED`, `RT_DJANGO_*` у цьому підпроєкті **не читаються**.

---

## 10. Залежності, запуск, сумісність

### 10.1 Devshell (Nix-only, без pip/venv)
У `nix/devshell.nix` (SP2 Task 1; fallback — `flake-global/dev/devshell.nix`), у `python313.withPackages`, мають бути: `fastapi`, `uvicorn`, `python-multipart`,
`httpx` (потрібен `fastapi.testclient`), `websockets` (WS-транспорт uvicorn), `pytest-timeout` (pytest.ini вже має `timeout = 30`).
Root `flake.nix` цей підпроєкт не змінює. Наявні `faster-whisper`, `transformers`, `torch`, `kokoro`, `pysilero-vad`, `soundfile`,
`scipy`, `loguru`, `numpy` — використовуються як є.

### 10.2 Запуск
```bash
nix develop
RT_PORT=8091 RT_ASR_BACKEND=fake python -m src.services.asr
RT_DEVICE=cuda RT_MODEL=medium python -m src.services.asr
python -m src.services.mt
python -m src.services.tts
```
`main()` у кожному `__main__.py` — майбутній target для `bin/rt-<svc>` (підпроєкт 2).

### 10.3 Зміни в legacy-коді
Єдина: `src/whisper/whisper_service.py` замінює локальні `_normalize_word`/`_filter_repetitions` на
`from ..services.asr.textfilter import filter_repetitions as _filter_repetitions` (одне джерело правди; `src/services/asr/__init__.py`
порожній, тож імпорт не тягне FastAPI). Решта legacy не змінюється.

---

## 11. Тестування

Розташування: `tests/services/`, файли `test_svc_<area>.py` (унікальні basename, без `__init__.py`).
`tests/services/conftest.py`: реєструє маркер `slow`; тести з `@pytest.mark.slow` пропускаються, якщо `RT_SLOW_TESTS != "1"`;
фікстури `asr_client`, `mt_client`, `tts_client` — `TestClient(create_app(settings, backend_factory=Fake…))` як
context manager + `runtime.wait_ready(5)`.

| Файл | Що перевіряє |
|---|---|
| `test_svc_env_settings.py` | парсинг env, дефолти, `rocm→cuda`, `SettingsError`, `apply_model_dir` |
| `test_svc_worker.py` | пріоритети, FIFO, `WorkerBusy`, `bypass_limit`, скасування, виняток у job, stop |
| `test_svc_auth.py` | токен з файлу, fail closed, 401/200, WS 1008 |
| `test_svc_runtime_health.py` | `/health` під час завантаження; `/status` loading→ready; failed → 503 `model_failed`; lifespan hooks; `VramProbe` |
| `test_svc_audio.py` | WAV/FLAC decode, stereo→mono, 48k→16k, невалідні байти, PCM round-trip |
| `test_svc_lazy_imports.py` | створення всіх трьох app з реальними backend-ами + `/health` не імпортує torch/ctranslate2/faster_whisper/transformers/kokoro/sounddevice/scipy |
| `test_svc_asr_textfilter.py` | приклади з docstring legacy + legacy `_filter_repetitions` — той самий об'єкт |
| `test_svc_asr_vad.py` | `EnergyVad`, Silero за замовчуванням, fallback |
| `test_svc_asr_endpointer.py` | silence-endpoint, discard шуму, коротка пауза, max_length split, finish |
| `test_svc_asr_stream_session.py` | Partial/Final дії, pre-roll, інтервал партіалів, timestamps, нарізка довільними чанками, finish з хвостом |
| `test_svc_asr_backend.py` | `make_backend`; мапінг опцій `FasterWhisperBackend` через підмінений модуль `faster_whisper` |
| `test_svc_asr_http.py` | json/verbose_json/text, мова запиту vs env, `auto`, 413, 400 invalid_audio, auth |
| `test_svc_asr_ws.py` | ready→partial→final→done; порядок фіналів + контекст; stop-flush; незалежні сесії з різними мовами; помилки протоколу; ліміт сесій; model_not_ready; WS-токен |
| `test_svc_mt.py` | мапінг мов, `arb_Arab`, FLORES pass-through, `auto` відхиляється, single/batch, порожні рядки, same-lang, per-request мови, ліміти, 422, `/v1/languages` |
| `test_svc_mt_backend.py` | `make_backend` |
| `test_svc_tts.py` | WAV-заголовки, каталог голосів, wav/pcm, ресемплінг, stream wav/pcm, голос per-request, помилки валідації, `/v1/audio/voices`, `sounddevice` не імпортується |
| `test_svc_tts_kokoro_backend.py` | кеш `KPipeline` на мову, `UnsupportedVoiceLanguage` через підмінений модуль `kokoro` |
| `test_svc_{asr,mt,tts}_real.py` (`slow`) | реальні моделі на CPU: TTS (`af_heart`) ≥0.5 с аудіо; ASR `tiny` транскрибує згенероване Kokoro «Hello world, this is a test.» → текст містить `hello`; MT `uk→en` дає непорожній ASCII, і кожен NLLB-код з `languages.py` є у словнику токенізатора |

Команди:
```bash
nix develop -c pytest tests/services -q
RT_SLOW_TESTS=1 nix develop -c pytest tests/services -m slow -q
```

Перевірка дизайну (2026-10-06): код плану було прогнано в ізольованому scratch-дереві (поза репо) — 117 швидких тестів
зелені (Python 3.13, fastapi 0.135.3 з nixpkgs lock-у), slow-тести TTS (Kokoro `af_heart`) і ASR (`tiny`) пройшли в
`nix develop` на CPU; `python -m src.services.asr` з `RT_ASR_BACKEND=fake` віддає `/health`/`/status`, невалідний env → exit 2.
Slow-тест MT **не** запускався (NLLB-600M відсутній у локальному HF-кеші) — див. Q9.

---

## 12. Deviations from overview

| # | Overview каже | Spec робить | Чому |
|---|---|---|---|
| D1 | Сервіс-специфічні env: `RT_ASR_LANGUAGE`, `RT_ASR_BEAM_SIZE`, `RT_ASR_VAD`; `RT_MT_*` (3); `RT_TTS_*` (3) | Додано: `RT_ASR_BACKEND`, `RT_ASR_VAD_THRESHOLD`, `RT_ASR_MIN_SPEECH_MS`, `RT_ASR_ENDPOINT_SILENCE_MS`, `RT_ASR_MAX_UTTERANCE_S`, `RT_ASR_PREROLL_MS`, `RT_ASR_PARTIALS`, `RT_ASR_PARTIAL_INTERVAL_MS`, `RT_ASR_MAX_SESSIONS`, `RT_ASR_MAX_UPLOAD_MB`, `RT_MT_BACKEND`, `RT_MT_MAX_LENGTH`, `RT_MT_REPETITION_PENALTY`, `RT_MT_MAX_BATCH`, `RT_MT_MAX_CHARS`, `RT_TTS_BACKEND`, `RT_TTS_MAX_CHARS` | Endpointing/партіали/ліміти мусять бути налаштовні без перезбірки; `*_BACKEND` — swappable backend (MT→CT2 у п.5) і `fake` для розробки edge без GPU. Підпроєкт 2 має віддзеркалити їх у `extraEnv`/опціях модуля. Чисто адитивно. |
| D2 | — | Спільний `RT_MAX_PENDING` | Захист від необмеженої черги до GPU. Адитивно. |
| D3 | `/status`: `service, version, device, model{id,loaded}, ready, vram_mb` | + `backend`, `model.state`, `model.load_seconds`, `model.error`, `pending` | Діагностика збою завантаження і завантаженості. Адитивно (існуючі поля без змін). |
| D4 | WS-повідомлення сервера: `partial`, `final` | + `ready`, `error`, `done`; у `partial`/`final` — `utterance_id`, у `final` — `language`; `start` клієнта приймає `prompt`, `partials` | Клієнту потрібні підтвердження старту, сигнал завершення і кореляція partial↔final. Адитивно. |
| D5 | MT: «Коди мов — ISO 639-1 на API» | Також приймаються прямі NLLB-коди | Доступ до мов поза таблицею без зміни коду. ISO 639-1 лишається основним. |
| D6 | TTS `response_format = wav|pcm` | default `wav` (OpenAI default — `mp3`, не підтримуємо) | Контракт не визначає default; MP3-енкодер — зайва залежність. |
| D7 | `/v1/*` під auth, `/health` — ні | `/status` теж без auth | Django health-service (`GET {base_url}/status`) і deploy-rs не мають токена; `/status` не містить чутливих даних. |
| D8 | Контракт не уточнює `sample_rate` WS | Підтримується лише 16000 | Ресемплінг на edge вже є (48k→16k); серверний потоковий ресемплінг дав би артефакти на межах фреймів. |

---

## 13. Припущення

1. Один інстанс сервісу = одна модель; зміна моделі = перезапуск з іншим `RT_MODEL` (як у llama-service).
2. Клієнтів небагато (1–4 edge + Django worker); throughput не є ціллю, latency realtime — є.
3. Edge шле аудіо приблизно в реальному часі; сервер не обмежує швидкість (TCP backpressure достатньо).
4. Kokoro не має української — TTS для `target_lang=uk` неможливий (існуюче обмеження, не регресія).
5. CPU-only середовище CI/devshell достатнє для всіх non-slow тестів.
6. Версія сервісів `0.1.0` (константа); підпроєкт 2 може підставити версію з Nix.

## 14. Open questions

- **Q1. Wyoming backend.** `hybrid_whisper_service`/`wyoming_client` не переносяться: overview визнав Wyoming «не основний шлях»
  (без партіалів/auth/ROCm), а пакет `wyoming` навіть відсутній у devshell. Інтерфейс `AsrBackend` дозволяє додати
  `WyomingAsrBackend` (лише `transcribe`, без партіалів) пізніше. Чи потрібен він взагалі?
- **Q2. Розріз по `MAX_UTTERANCE` без overlap.** Можлива втрата/обрізання слова на межі 10-секундного розрізу. Альтернатива —
  різати в точці мінімальної VAD-ймовірності в останніх 2 с. Вирішити після slow-тестів на реальній мові.
- **Q3. Падіння завантаження моделі.** Зараз процес живе з `state=failed` (видно в `/status`). deploy-rs health-check
  (overview: 20×1 с) дивиться `/health` і не помітить зламаної моделі. Варіанти: (a) health-check у підпроєкті 3 читає
  `/status.model.state != "failed"`; (b) `RT_EXIT_ON_LOAD_FAILURE=true` → exit 1 для systemd-restart. Рекомендація: (a).
- **Q4. Дефолтна ASR-модель** — ~~`small`~~ вирішено підпроєктом 5 (SP5 §4.3/§4.8): `whisper-large-v3-turbo` на cuda, `whisper-small` на cpu/rocm (alias-и реєстру; SP5 Task 12a).
- **Q5. Непреемптивна черга.** Довгий HTTP-файл (хвилини аудіо) блокує realtime-фінали. Для Django batch (п.4) — окремий
  інстанс `rt-asr` або chunked-обробка з поступкою пріоритету. Вирішити в підпроєкті 4.
- **Q6. Пост-обробка перекладу** (велика літера + крапка) прибрана з сервера. Edge (п.2) має повторити її локально, якщо UX цього потребує.
- **Q7. Kokoro G2P для `j`/`z`/`e`…** потребує додаткових залежностей (espeak-ng, pyopenjtalk, jieba). Каталог `/v1/audio/voices`
  показує всі 54 голоси; невідтворювана мова дає 400 при синтезі. Чи фільтрувати каталог за реально доступними мовами (дорожче на старті)?
- **Q8. `vram_mb` для ROCm** — `null` (немає `rocm-smi`-інтеграції). Підпроєкт 5 може додати.
- **Q9. Таблиця ISO→NLLB** складена вручну (виправлено `ar→arb_Arab`, Persian `pes_Arab`, Latvian `lvs_Latn`, Albanian
  `als_Latn`, Uzbek `uzn_Latn`, Swahili `swh_Latn`, Azerbaijani `azj_Latn`); підтверджується лише slow-тестом
  `test_every_mapped_code_exists_in_tokenizer`, який ще не запускався. Невідповідні коди прибрати або виправити за його виводом.
