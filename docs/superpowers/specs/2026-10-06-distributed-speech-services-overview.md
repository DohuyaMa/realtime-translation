# Distributed speech services (ASR / MT / TTS) — overview & shared contract

> **Статус 2026-10-22:** частково замінено. Scope MVP (без `rt-mt`, переклад через llama, лише цей репо) — `docs/superpowers/specs/2026-10-22-realtime-mvp-design.md`; план виконання — `docs/superpowers/plans/2026-10-22-realtime-mvp.md` (§10 spec: що KEEP/DROP/DEFER). Цей документ лишається джерелом деталей для задач, на які посилається новий план.

Дата: 2026-10-06
Статус: напрямок **A** затверджено користувачем. **Визначальне рішення: cluster-generic, розміщення сервісів по хостах — дані clusterops, не дизайн (§7 C0).** Цей документ — спільна база для 5 підпроєктних spec/plan. Узгодження між підпроєктами (reconciliation 2026-10-06) — §6 (порядок виконання) і §7 (рішення C1–C7).
Кожен підпроєктний spec **мусить** дотримуватись контракту нижче; відхилення — тільки явно, з поясненням у розділі "Deviations from overview".

## 1. Мета

Перевести real-time-translator з Home Manager user-юнітів (Unix sockets, одна машина) на модель,
ідентичну `llama-service` в інфрі `~/repos/swarm-nix/system-conf`:

- три **незалежні** compute-сервіси — **ASR** (транскрипція), **MT** (переклад), **TTS** (озвучка);
- кожен деплоїться deploy-rs як окремий профіль на будь-який хост з registry, незалежно від інших;
- **edge** (UI + capture + playback + PipeWire sinks) лишається user-level (Home Manager) на машині з мікрофоном;
- режим «тільки транскрипція» = edge + `rt-asr`; «повний переклад» = edge + `rt-asr` + `rt-mt` + `rt-tts`;
- ці ж сервіси пізніше споживає Django `engine` (batch, через WorkItem-чергу).

## 2. Ключові факти дослідження (2026-10-06)

### Інфра (swarm-nix/system-conf)
- Colmena = платформа, deploy-rs = застосунки. `applications/lib/mkApplication.nix`: `activate.custom` → `ln -sfn $PROFILE <stateDir>/current` → `systemctl restart <serviceName>` → health-check 20×1s.
- NixOS-модуль (`common/modules/llm/llama-service.nix`) визначає **system** systemd-юніт; `ExecStart` вказує на `${deployRsPath}`; `EnvironmentFile = "-${deployRsPath}/env"`.
- `applications/lib/mkDeployProfiles.nix`: `mergeEnvFromLlamaService` рендерить per-host `env` з `nixosConfigurations.<host>.config.services.llamaService` (через `common/modules/llm/lib/env-lines.nix`) — **LLM-специфічно**, треба узагальнити.
- `applications/registry.nix` і `machines/inventory.nix` — **AUTO-GENERATED clusterops NixGen** (Django DB). Custom-сервіс → `import ./services/<svc.name>.nix { inherit pkgs lib; }` (`django_api/clusterops/services/nix_generator/io.py:139-178`). Тека `applications/services/` ще не існує. Немає `inputs` у сигнатурі → наш flake підключати через **overlay** у `pkgs` (`flake.nix:48`, зараз без overlays).
- Per-host конфіг: `machines/<host>/modules/flake-services/*.nix` (агрегатор `default.nix` форвардить лише частину атрибутів — перевірити).
- Старі непідключені спроби: `machines/cyborg/modules/services/realtime-translation.nix`, `machines/sktop-note27-nixos/modules/realtime-translation.nix`, `common/modules/services/wyoming-faster-whisper.nix` (import закоментований). `system-conf/todo/todo.md` — застарілий (про path-input).
- Секрети llama: `bootstrapToken` plaintext у Nix (в store) — **не повторювати**; використовувати файл-секрети (sops-nix / colmena `deployment.keys`).

### Django / engine
- Контракт pull-only: `POST /api/llm/register/` (bootstrap token → access token) → `GET /api/engine/work-items/next/?tier=N` → `POST /api/engine/work-items/{uuid}/complete/` `{raw_result, status, parsed_result?, error_message?, generation_seconds?}`.
- `next()` (`engine/views_work_item.py`) фільтрує **тільки за tier** → не-LLM worker хапав би LLM-задачі. Потрібна фільтрація за `artifact_type`/capability.
- Прецедент не-LLM задачі: `rerank_score` (JSON у `prompt`, executor за `artifact_type`, `knowledge/handlers/rerank.py`).
- `LLMService` без `kind`; реєстрація хардкодить `base_url=http://{REMOTE_ADDR}:8080`. `WorkerType` `audio` зарезервований у `engine/blueprints/worker_types.yaml`.
- Django health: тягне `GET {base_url}/status` і `/models` (`llm_services/services/health_service.py`).

### Цей репо
- IPC тільки `AF_UNIX` (`src/common/ipc.py`), `WhisperSocketClient` перевіряє `os.path.exists`.
- Глобальний мутабельний стан у сервісах (`set_voice`, `set_languages`) — несумісно з кількома клієнтами.
- `tts_service` має `play_audio` (грає на машині сервісу) — має переїхати на edge.
- Capture (sounddevice 48k→16k) уже в процесі UI (`src/translation_system.py`).
- Відома проблема збірки: `kokoro → torch → libnvshmem` source build (див. `plans/todo.md`) — **розв'язано** (§7 C2): окремий `cudaSupport`-інстанс + `cache.nixos-cuda.org`, без torch-bin; TTS default — ORT-рушій.

### Ринок протоколів
- `wyoming-faster-whisper` (nixpkgs, є NixOS-модуль) — **без partial transcripts, без auth, без ROCm** → не основний шлях.
- OpenAI-сумісні `/v1/audio/transcriptions`, `/v1/audio/speech` — де-факто стандарт (speaches, Kokoro-FastAPI, LocalAI, vLLM).
- NLLB-200-distilled-600M у **CTranslate2 int8**: ~0.6 GB VRAM (vs ~2.5 GB HF fp32); `ctranslate2` у nixpkgs з CUDA і `rocmSupport` (ROCm з CT2 4.7) — `rocmSupport` є в nixpkgs інфри (`7a0f122`, CT2 4.8.2), але **не** в старому lock цього репо (`89570f2`, 4.8.0) → bump lock (§7 C1).
- Kokoro-82M < 2 GB VRAM; `kokoro`/`misaki` є в nixpkgs.
- Streaming ASR (WhisperLive, WhisperLiveKit, SimulStreaming) — кожен зі своїм WS-протоколом, нема в nixpkgs.

## 3. Рішення (напрямок A)

Три власні FastAPI-сервіси в стилі llama-service. Кожен:
1. HTTP API (OpenAI-сумісний де можливо) + `/health` + `/status`;
2. stateless щодо клієнта: усі параметри (мова, голос, швидкість) — у запиті; дефолти — з env;
3. опційний Django-worker потік (вимкнений за замовчуванням; вмикається env-змінними) — фаза 4;
4. окремий deploy-rs профіль, окремий system-юніт, окремий system-user.

## 4. Спільний контракт

### 4.1 Імена, порти, шляхи

| Сервіс | Unit / user | Порт (default) | stateDir | deploy-rs symlink | Binary у bundle |
|---|---|---|---|---|---|
| ASR | `rt-asr` | 8091 | `/var/lib/rt-asr` | `/var/lib/rt-asr/current` | `bin/rt-asr` |
| MT  | `rt-mt`  | 8092 | `/var/lib/rt-mt`  | `/var/lib/rt-mt/current`  | `bin/rt-mt` |
| TTS | `rt-tts` | 8093 | `/var/lib/rt-tts` | `/var/lib/rt-tts/current` | `bin/rt-tts` |

- Моделі/кеш: `<stateDir>/models` (`HF_HOME=<stateDir>/models/hf`).
- Варіанти збірки (як `llm-cpu/gpu/rocm`): `cpu`, `gpu` (CUDA), `rocm`. Назви registry/deploy-rs профілів: `rt-asr-cpu`, `rt-asr-gpu`, `rt-asr-rocm`, `rt-mt-*`, `rt-tts-cpu`, `rt-tts-gpu`. **`rt-tts-rocm` не існує** (ORT ROCm/MIGraphX не перевірений — §7 C2); `rt-{asr,mt}-rocm` — «experimental» (апаратно: gfx90c під `HSA_OVERRIDE_GFX_VERSION`).
- Python-пакет сервісів: `src/services/{asr,mt,tts}/` (FastAPI app factory + `__main__`), спільне — `src/services/common/` (config з env, health/status, auth, logging).

### 4.2 Конфіг через env (однаково для всіх)

| Env | Значення |
|---|---|
| `RT_HOST` / `RT_PORT` | bind (default `127.0.0.1` / порт з таблиці) |
| `RT_DEVICE` | `cpu` \| `cuda` \| `rocm` |
| `RT_MODEL` | id/шлях моделі |
| `RT_COMPUTE_TYPE` | `int8`, `float16`, `int8_float16`… (ASR/MT); TTS — `float32`\|`float16`\|`int8` → вибір ONNX-файлу (SP5 D3) |
| `RT_MODEL_DIR` | `<stateDir>/models` |
| `RT_API_TOKEN_FILE` | опційний файл з bearer-токеном; якщо задано — всі `/v1/*` вимагають `Authorization: Bearer` (`/health` — ні) |
| `RT_LOG_LEVEL` | `info` default |
| Сервіс-специфічні | `RT_ASR_LANGUAGE`, `RT_ASR_BEAM_SIZE`, `RT_ASR_VAD`; `RT_MT_SOURCE_LANG`, `RT_MT_TARGET_LANG`, `RT_MT_NUM_BEAMS`; `RT_TTS_VOICE`, `RT_TTS_SPEED`, `RT_TTS_SAMPLE_RATE` |
| Django worker (фаза 4) | `RT_DJANGO_ENDPOINTS` (comma-sep), `RT_DJANGO_BOOTSTRAP_TOKEN_FILE`, `RT_WORKER_ENABLED` (`false` default), `RT_SERVICE_NAME` |
| Розширення (адитивні, задокументовані deviations) | SP1 D1/D2: `RT_*_BACKEND`, `RT_MAX_PENDING`, параметри endpointing `RT_ASR_*`, ліміти; SP5 D4: `RT_NUM_THREADS`, `RT_MODEL_DOWNLOAD`, `RT_MODEL_VERIFY`, `RT_HF_TOKEN_FILE`, `RT_TTS_ENGINE` (`onnx`\|`torch`, рушій усередині `RT_TTS_BACKEND=kokoro`); SP4 D2: `RT_WORKER_POLL_INTERVAL`, `RT_WORKER_MAX_DOWNLOAD_BYTES`. `RT_MODEL` = alias реєстру SP5 або абсолютний шлях (SP5 D5). Device-залежні defaults (модель, compute type, beams) — лише в Python (SP5 §4.8); Nix-модуль їх не дублює (`model`/`beamSize`/`numBeams` = `null`) |

### 4.3 HTTP API

Спільне для всіх:
- `GET /health` → `200 {"status":"ok"}` як тільки процес слухає (liveness; модель може ще вантажитись — завантаження у фоні, як у llama).
- `GET /status` → `{"service":"rt-asr","version":"…","device":"cuda","model":{"id":"…","loaded":true},"ready":true,"vram_mb":…}` (readiness).

ASR (`rt-asr`):
- `POST /v1/audio/transcriptions` — OpenAI-сумісний multipart (`file`, `language?`, `prompt?`, `temperature?`, `response_format` = `json|verbose_json|text`) → `{"text": "...", "language": "uk", "segments": [...]?}`.
- `WS /v1/audio/stream` — realtime: клієнт шле JSON `{"type":"start","language":"uk","sample_rate":16000}`, потім binary-фрейми PCM16 mono, `{"type":"stop"}`; сервер шле `{"type":"partial","text":…}` і `{"type":"final","text":…,"start":…,"end":…}`. VAD/endpointing — на сервері.

MT (`rt-mt`):
- `POST /v1/translate` `{"text": str | [str], "source_lang": "uk", "target_lang": "en", "num_beams"?: int}` → `{"translations": [str], "source_lang", "target_lang", "model"}`. Коди мов — ISO 639-1 на API, мапінг у NLLB-коди всередині.
- `GET /v1/languages`.

TTS (`rt-tts`):
- `POST /v1/audio/speech` — OpenAI-сумісний `{"input": str, "voice"?: str, "speed"?: float, "response_format": "wav|pcm", "stream"?: bool}` → аудіо (chunked при `stream=true`). Відтворення — **ніколи** на сервері.
- `GET /v1/audio/voices`.

### 4.4 Nix-виходи цього репо (flake)

- `packages.<system>.rt-{asr,mt}-{cpu,gpu,rocm}`, `rt-tts-{cpu,gpu}` — bundle (python env + app), з `$out/bin/rt-<svc>`. GPU (CUDA) — з окремого scoped інстансу nixpkgs споживача з `cudaSupport = true` і дефолтними `cudaCapabilities` (substituter `cache.nixos-cuda.org`), створеного всередині пакета; ROCm — `ctranslate2.override { rocmSupport = true; }`; **ніколи** torch → torch-bin (§7 C1/C2).
- `overlays.default` → `pkgs.rtSpeech.{asr,mt}.{cpu,gpu,rocm}`, `pkgs.rtSpeech.tts.{cpu,gpu}`, `pkgs.rtSpeech.lib` (alias `lib.rtSpeech`) — для інфри, бо registry бачить лише `{pkgs, lib}`; overlay **лише додає** `rtSpeech` (infra R1).
- `nixosModules.rt-speech` → опції `services.rtSpeech.{asr,mt,tts}.{enable, deployRsPath, host, port, device, model, computeType, openFirewall, firewallInterfaces, apiTokenFile, extraEnv, ...}` + сервіс-специфічні. Юніт: system, static user, hardening як у llama (GPU DeviceAllow за device), `EnvironmentFile = "-${deployRsPath}/env"`.
- `lib.rtSpeech.envLines` — єдина функція cfg → env-рядки (використовується модулем і інфрою для per-host `env` у bundle; аналог `env-lines.nix`).
- `homeManagerModules.rt-translator` → **тільки edge**: UI, capture, playback, virtual sinks, `rt-translator.endpoints.{asr,mt,tts}` (URL), `rt-translator.mode` = `transcribe|translate`, `apiTokenFile`.

### 4.5 Мережа і безпека
- Між машинами — Tailscale. `openFirewall` відкриває порт лише на `firewallInterfaces` (default `[ "tailscale0" ]`).
- Токен — тільки з файлу (sops-nix / colmena `deployment.keys`), ніколи не в Nix store.

### 4.5a Розповсюдження коду — ТІЛЬКИ через git (вимога користувача, 2026-10-06)
- Інфра підключає цей репо **виключно** як `realtime-translation.url = "github:DohuyaMa/realtime-translation"` (публічний), версія пінится в `system-conf/flake.lock`, який комітиться в git.
- **Жодних `path:` inputs і `--override-input … path:…`** у робочому процесі деплою: інфру використовують щонайменше 2 машини-контролери (cyborg, SKTOP-NOTE27-NIXOS мають `allowLocalDeployment`), а деплой іде на інші хости — усі мають бачити той самий код з git.
- Реліз: commit + `git push` тут → у swarm-nix `nix flake update realtime-translation` → commit + push `flake.lock` → `deploy`. Опційно — git-теги/ревізії (`?ref=`/`?rev=`) для фіксації релізів.
- Наслідок для flake цього репо: git-flake бачить **лише tracked-файли** — усе, що потрібне для збірки, має бути закомічене; нічого не посилатися на абсолютні локальні шляхи (`/home/...`), моделі не в репо (вони провіжняться в `<stateDir>/models`).

### 4.6 Django (фаза 4)
- `artifact_type`: `asr_transcribe`, `mt_translate`, `tts_synthesize`.
- Аудіо в WorkItem — **посиланням** (URL на Django media / shared storage), не base64 у `prompt`.
- `next()` отримує фільтр за типом (напр. `?artifact_types=asr_transcribe`), worker передає свої типи.

## 5. Підпроєкти

| # | Підпроєкт | Репо | Spec | Plan | Залежить від |
|---|---|---|---|---|---|
| 1 | Speech services API (ASR/MT/TTS FastAPI, stateless, health/status) | this | `specs/2026-10-06-speech-services-api-design.md` | `plans/2026-10-06-speech-services-api.md` | 2 (лише Tasks 0–1: nixpkgs, `nix/devshell.nix`) |
| 2 | Nix packaging + NixOS module + edge HM module + edge client на HTTP/WS | this | `specs/2026-10-06-nix-packaging-design.md` | `plans/2026-10-06-nix-packaging.md` | 1 (entrypoints), 5 (Task 12a — робочі backend-и `rt-mt`/`rt-tts` у bundle) |
| 3 | Infra integration (flake input, overlay, `applications/services/rt-*.nix`, env-merge узагальнення, clusterops Service, per-host flake-services, cleanup) | swarm-nix | `specs/2026-10-06-infra-deploy-rs-design.md` | `plans/2026-10-06-infra-deploy-rs.md` | 2; 5 (admission check `system`-gate для будь-якого розміщення) |
| 4 | Django/engine integration (worker в сервісах, `next()` фільтр, artifact_types, реєстр сервісів за kind) | this + swarm-nix | `specs/2026-10-06-django-engine-integration-design.md` | `plans/2026-10-06-django-engine-integration.md` | 1, 3 |
| 5 | Model optimization (NLLB → CTranslate2 int8, VRAM-бюджет на хост, Kokoro без torch-source-build) | this | `specs/2026-10-06-model-optimization-design.md` | `plans/2026-10-06-model-optimization.md` | 1 (Task 12a), 2 (Tasks 0–1) |

Усі spec/plan лежать у цьому репо в `docs/superpowers/` (навіть для swarm-nix-частин) — щоб план був в одному місці.

## 6. Порядок виконання

Залежності між планами (task-гранулярність, де вона важлива). `→` — «після», `∥` — паралельно.

| Крок | Що | Залежить від | Примітка |
|---|---|---|---|
| 0 | **SP2 Task 0** (bump nixpkgs ≥ ревізії інфри `7a0f122…`) → **SP2 Task 1** (git-гігієна, flake без flake-utils, `nix/devshell.nix`, `lib.rtSpeech.envLines`) | — | Першими: решта планів додає devShell-залежності вже в `nix/` (SP1 Task 1, SP5 Task 1) і бачить ту саму nixpkgs-ревізію, що й інфра |
| 1 | **SP1** Tasks 1–18 (FastAPI-сервіси, backend-протоколи, fake-backend-и) | 0 | Defaults SP1 (`small`, HF NLLB, `hexgrad/Kokoro-82M`) — тимчасові до кроку 2b |
| 1∥ | **SP2** Tasks 2–3, 6–13 (NixOS-модуль, VM-тест, edge-клієнт, UI, HM) ∥ **SP3** Phase 1 Tasks 1–3 (clusterops-фікси), Task 4 (cleanup) і Task 7a (CUDA substituter — незалежний, потрібен SP5 bench-gpu) ∥ **SP4** Part A (Django) ∥ **SP5** Tasks 1–12 (devShells, реєстр, рушії) | 0 | Не потребують коду SP1 (SP5 рушії — самостійні модулі з фейками); SP5 Task 1 — після SP2 Task 1 |
| 2a | **SP2** Task 4 (bundles cpu, overlay) → Task 5 (GPU/ROCm-варіанти, cache-hit) | 1 (`src/services/*/__main__.py` у git) | `httpx`, `huggingface-hub`, `pysilero-vad`, `onnxruntime` уже в `deps.nix` |
| 2b | **SP5 Task 12a** (CT2 MT backend default, ORT TTS engine, реєстр у backend-ах SP1) | 1 (SP1 Tasks 10, 13, 15), SP5 Tasks 2–12 | Без нього bundle `rt-mt` не має робочого backend-а (у bundle немає `transformers`) |
| 2c | **SP5** Phase D (harness, Tasks 13–17) → Phase E (Tasks 18–21: калібрування за класами заліза на прикладних хостах; `system`-gate параметризований `--host`) | 2b; для GPU-вимірів — SP3 Task 7a (substituter на `<cuda-host>`) | Admission check для будь-якого розміщення (SP3 Task 10) |
| 3 | **SP2** Tasks 14–16 (WS-клієнт опц., видалення легасі, docs, git-flake доказ) → **push** | 2a, 2b | Легасі `src/translate`, `src/tts` видаляються лише після 2b |
| 4 | **SP3** Phase 2: Task 5 (input + overlay, llama byte-identical) → Task 6 → Task 7 (per-host `rt-speech.nix` для `<host>`) → Task 7a (CUDA substituter на `<cuda-host>`, якщо ще не зроблено) → Task 8 (Service + registry) | 3 (push у GitHub), SP3 Tasks 1–3 | Тільки `github:`-input + закомічений lock (§4.5a) |
| 5 | **SP3** Task 9 (перший деплой на `<host>`) → Task 10 (додати/перенести сервіси за загальною процедурою + admission check SP5) → Task 11 (токен) → Task 12 (edge HM, e2e) | 4, 2c | Розміщення — дані clusterops (C0) |
| 6 | **SP4** Part B (воркер у сервісах) → Part C (живий e2e) | 1 (B), Part A, 5 (C) | Env воркера — через `extraEnv` модуля SP2 |
| 7 | **SP5** Task 22 (ROCm spike на `<rocm-host>`, опц.), Task 23 (ліцензія NLLB), Task 24 | 2c | GO у Task 22 — передумова для деплою будь-якого `rt-*-rocm` |

## 7. Узгоджені рішення (reconciliation 2026-10-06)

- **C1 — ROCm CTranslate2.** «У nixpkgs немає `ctranslate2.rocmSupport`» було правдою лише для старого lock цього репо (`89570f2`, CT2 4.8.0). nixpkgs інфри (`7a0f122`, CT2 4.8.2) має `rocmSupport` + `rocmGpuTargets` + hip-бібліотеки; overlay будується з `pkgs` інфри. Рішення: SP2 Task 0 піднімає lock до ≥ ревізії інфри; ROCm = `ctranslate2.override { rocmSupport = true; rocmGpuTargets = [ … ]; }` (+ python `ctranslate2`/`faster-whisper` через scoped `packageOverrides`); власний `WITH_HIP` overrideAttrs і 2-год time-box прибрано. ROCm-варіанти — «experimental» лише з апаратних причин (напр. приклад gfx90c → `HSA_OVERRIDE_GFX_VERSION`). Зачеплено: SP2 spec §2.2/§5.5/D1, plan Tasks 0/4/5; SP5 spec F3/§3.3/D1, plan Task 22; SP3 spec §7/Q3.
- **C2 — TTS GPU і torch.** Пріоритет SP5: ніколи `torch → torch-bin`; GPU-варіанти **всіх** сервісів — з окремого інстансу `import pkgs.path { config = { allowUnfree = true; cudaSupport = true; }; }` (дефолтні `cudaCapabilities`, свіжий config), створеного всередині пакета (SP2 `nix/packages/variant-pkgs.nix`) — глобальний `pkgs` інфри недоторканий (R1). TTS default — тонкий ORT-рушій + misaki G2P (`KPipeline(model=False)`), torch `KPipeline` — fallback `RT_TTS_ENGINE=torch`. Інтеграція рушіїв у backend-протоколи SP1 — одна задача SP5 Task 12a (у SP1 дублюючих задач немає). `rt-tts-rocm` не експортується: ORT ROCm EP видалено в 1.23, MIGraphX на gfx90c не перевірений (можливий пізніше як experimental через `onnxruntime.rocmSupport`) — причина не torch.
- **C0 — ВИЗНАЧАЛЬНЕ (user override, 2026-10-06): cluster-generic, розміщення — це дані.** Жоден spec/plan не фіксує, який сервіс на якому хості. Розміщення = рядки clusterops (Service × deploy targets) + per-host `services.rtSpeech.<svc>.enable` у `machines/<host>/modules/flake-services/rt-speech.nix`; змінюється будь-коли без змін коду/spec. Будь-який хост може запускати будь-який сервіс у будь-якому варіанті, який підтримує його залізо. Імена хостів у документах — лише як **«приклад»**. Процедура (SP3 §7): клас заліза → дозволені варіанти (`cuda`→`gpu`, `rocm`→`rocm` experimental, будь-який→`cpu`); вибір моделі — за класом заліза / VRAM-бюджетом (SP5 §4.6), не за hostname; per-host overrides — опціями `services.rtSpeech.*`; **admission check** — SP5 harness `system`-gate, параметризований хостом (усі co-located сервіси + llama під навантаженням, пік ≤ 90 % VRAM пристрою / RAM, без OOM); додати/перенести сервіс — clusterops API → `generate_infra --regenerate applications/registry.nix` → per-host enable → colmena/nixos-rebuild → deploy-rs.
- **C3 — розміщення/VRAM (замінено C0).** Виміри SP5 (напр. приклад cyborg: дисплей на iGPU, KV llama @16384 ≈ 512 MiB, ASR+MT+llama ≈ 6.1 GiB з 8 GiB) лишаються як **приклад** і калібрування моделей за класом; конкретне розміщення не фіксується. Поріг admission — один для SP3 і SP5: ≤ 90 % VRAM пристрою (для 8188 MiB ≈ 7.2 GiB) з усіма co-located сервісами.
- **C4 — CUDA substituter.** `cache.nixos-cuda.org` (ключ `cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M=`) — у **системних** `nix.settings` CUDA-хостів (кожен хост класу `cuda`; приклад: cyborg), бо deploy-rs збирає на таргеті як `deploy`, а звичайні користувачі не trusted: SP3 Task 7a (`common/modules/nix/cuda-substituter.nix`) до першого `rt-*-gpu` деплою. У flake цього репо — лише `nixConfig`-підказка (SP2 Task 1).
- **C5 — крос-залежності.** `httpx` — runtime-залежність усіх bundle (SP2 `nix/packages/deps.nix`, спільні deps), SP4 OQ12 закрито. MT `context` у `/v1/translate` **не** додається (менша зміна): edge контекст не шле (SP2 OQ4 закрито). PCM16 WAV з TTS уже гарантовано SP1 §8.1 (SP2 A3/OQ5 закрито).
- **C6 — вимоги інфри R1–R7.** Після C1/C2 SP2 §15 їх задовольняє: R1 — overlay додає лише `rtSpeech`, CUDA-інстанс і ROCm-override scoped у пакеті (перевірка `checks.overlay` + SP3 Task 5 Step 7 drvPath); R2/R3 — `envLines` (безумовні `RT_HOST/PORT/DEVICE`, літеральний `RT_API_TOKEN_FILE`); R4 — `/health` ≤ 10 с: SP1 lazy imports (`test_svc_lazy_imports`, тепер і `onnxruntime`/`huggingface_hub`) + SP5 constraint; R5/R6 — модуль; R7 — git-only (`git clone`/`github:` збірка).
- **C7 — інші узгодження цього проходу.** `RT_MODEL` у per-host конфігах — alias-и реєстру SP5 (`whisper-large-v3-turbo`, `nllb-200-distilled-600M-ct2-int8`, `kokoro-82m-v1.0-onnx`), не HF id; `beamSize`/`numBeams` у NixOS-модулі — `nullOr int`, default `null` (device-залежні defaults у Python); devShell-и — `nix/devshell.nix` і `nix/bench-shells.nix` (не `flake-global/`, який SP2 Task 15 видаляє); SP4 env воркера — через `extraEnv`, bootstrap-токен — sops-файл з власником `rt-<svc>`; SP3 Task 5 Step 1 відкидає `mkBundle`/`edge` з `pkgs.rtSpeech` при переліку варіантів.
