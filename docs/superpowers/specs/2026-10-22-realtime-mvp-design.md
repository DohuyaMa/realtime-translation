# Realtime MVP: rt-asr + rt-tts + llama-переклад, готовність до deploy-rs

> **Оновлення 2026-10-31:** LLM-частина (lease через Django, `LeaseKeeper`) замінена: discovery сервісів через Django + app-сесія напряму у воркера, failover по вузлах. Див. `docs/USAGE.md` §7.

Дата: 2026-10-22
Статус: затверджено користувачем (сесія 2026-10-22). **Цей документ має пріоритет** над specs від 2026-10-06 там, де вони розходяться. Ті specs лишаються джерелом деталей для частин, позначених тут «KEEP» (§10).

## 1. Мета і межі

**Ціль 1 (зараз).** Переклад мови користувача (польська, українська, російська) англійською в реальному часі на новій розподіленій архітектурі. Легасі на сокетах давав прийнятний результат лише 3 рази з 10: слова різались посередині, і далі по пайплайну виходила «каша».

**Ціль 2 (підготувати, але не вмикати).** Ті самі сервіси згодом використовує Django engine. Його воркери будуть на tier 2, роутинг змінять пізніше. Workflow у engine поки немає.

**Головний результат цього етапу:**
- усе в цьому репо готове до деплою через deploy-rs (пакети, NixOS-модуль, HM-модуль);
- є документ «як цим користуватись».

**Межі:**
- **Змінюємо лише цей репо.** Зміни в `swarm-nix/system-conf` і Django описуються документами:
  - lease воркера (Django + llama) — `2026-10-22-llm-worker-lease-design.md`;
  - інтеграція в інфру — розділ у документі користувача (§9).
- **Розміщення по хостах — це дані** (рішення C0 з overview). Конкретні хости в документах наводяться лише як приклад.
- **Код потрапляє в інфру тільки через git** (overview §4.5a).
- **Особистий проєкт, не продакшн:** реалізуємо одразу цільовий дизайн. Легасі видаляється на початку, без перехідних режимів.

## 2. Режими роботи

| Режим | Що потрібно | Хто ініціює |
|---|---|---|
| `transcribe` — тільки транскрипція | edge + `rt-asr` | ПК користувача (мікрофон) або engine (файли, пізніше) |
| `translate` — переклад наживо | edge + `rt-asr` + llama-воркер на lease через Django + `rt-tts` | ПК користувача (один із двох) |
| `speak` — тільки озвучка | `rt-tts` | engine (пізніше), текст приходить з Django; результат іде в Django і на відтворення на ПК (пізніше) |

- Кожен сервіс працює самостійно (окремий юніт, окремий deploy-rs профіль). Пайплайн складає edge.
- Режим edge за замовчуванням (`auto`): `translate`, якщо налаштовано джерело LLM (Django або direct-URL) і TTS-endpoint-и, інакше `transcribe`.

## 3. Архітектура

```
 ПК (edge, Home Manager)                         хости кластера (NixOS, deploy-rs)
 ┌───────────────────────────────┐               ┌──────────────────────────┐
 │ capture 48k → 16k, Silero VAD │──HTTP POST───▶│ rt-asr :8091 (faster-    │
 │ PauseSegmenter (§5)           │  /v1/audio/   │  whisper, BATCH-interleave)│
 │ EndpointPool (§6)             │  transcriptions└──────────────────────────┘
 │ LeaseKeeper ──────────────────│──lease───────▶ Django /api/llm/leases/ (вибір воркера,
 │                               │               пауза next() для нього, токен воркера)
 │ LlmClient ────────────────────│──HTTP POST───▶ наявний llama-воркер :8080 (на час lease
 │                               │  /v1/chat/completions   не бере WorkItem)
 │ TtsClient ────────────────────│──HTTP POST───▶ rt-tts :8093 (Kokoro ONNX)
 │ playback → rt_virtual_output  │  /v1/audio/speech
 └───────────────────────────────┘
```

- `rt-mt` **видалено** (порт 8092 не використовується), тож NLLB, Marian і CT2-перекладач більше не потрібні.
- Переклад наживо робить наявний llama-воркер, взятий на lease через Django: Django обирає воркер, видає його токен і ставить воркер на паузу в `next()`. Запити йдуть напряму на воркер, бо черга WorkItem додала б інтервал опитування (3 с) до кожної фрази.
- Протокол lease — spec `2026-10-22-llm-worker-lease-design.md`.
- Engine перекладає наявними LLM-сервісами (поза цим етапом).

## 4. Сервіси rt-asr і rt-tts

Основа — SP1 spec (`2026-10-06-speech-services-api-design.md`) з такими змінами.

### 4.1 Спільне (`src/services/common/`)

- **Імена, порти, шляхи** — як в overview §4.1, без MT:

  | Сервіс | Порт | Юніт / користувач | stateDir |
  |---|---|---|---|
  | rt-asr | 8091 | `rt-asr` | `/var/lib/rt-asr` |
  | rt-tts | 8093 | `rt-tts` | `/var/lib/rt-tts` |

- **Конфіг: три рівні.** Пріоритет: запит > runtime override > env.
  1. **env** (bootstrap): `Environment=` через Colmena і `EnvironmentFile=-<deployRsPath>/env` через deploy-rs. Так само, як у llama.
  2. **runtime override** через `/config` (§4.2): змінюється без деплою і без рестарту.
  3. **параметри запиту** (form/JSON-поля): мають найвищий пріоритет.
- **Таблиця полів (field-spec).** Кожне поле налаштувань має одне визначення: env-ключ, тип, default, межі та клас:
  - `BOOTSTRAP` — лише env: `host`, `port`, `api_token_file`, `model_dir`, `config_state`;
  - `TUNABLE` — застосовується з наступного запиту;
  - `RELOAD` — зміна перезавантажує модель: `model`, `device`, `compute_type`, `backend`, `engine`.
  
  З цієї таблиці будуються і парсинг env, і валідація `/config`.
- **InferenceWorker** (SP1 Task 3) отримує:
  - пріоритет `BATCH = 3` (нижчий за `PARTIAL`);
  - метод `submit_iter(gen_factory, ...)`. Він крокує генератор по одному елементу за job, і між кроками черга обслуговує запити з вищим пріоритетом.
  
  Довга задача займає один слот `pending`. Скасування — через `threading.Event` між кроками.
- **`/health`** повертає `{"status":"ok"}` (без авторизації) і відповідає за ≤ 10 с після старту. Модель вантажиться у фоні.
- **`/status`** — як у SP1, плюс `pending` і `config_version`.

### 4.2 Runtime config `/config` (дзеркало llama `routes_config.py`)

- `GET /config` → `{"values": {...}, "source": {field: "override"|"bootstrap"}, "bootstrap": {...}, "version": int}`.
- `POST /config` з тілом `{"version": int, "values": {field: value}, "reset": [field, ...]}`:
  - відповідає тим самим об'єктом плюс `"reloaded": bool`;
  - валідація all-or-nothing, помилка → 422 `{"detail": "..."}`;
  - невідомі ключі ігноруються (сумісність уперед);
  - `version` лише інформаційна, як у llama.
- Обидва маршрути вимагають bearer-токен, якщо задано `RT_API_TOKEN_FILE`.
- Зміна RELOAD-поля синхронно перезавантажує backend: стан LOADING → нова модель → READY. Якщо перезавантаження не вдалося, лишається попередня модель, а відповідь — 500 з причиною.
- **Відхилення від llama — збереження стану.** Якщо задано `RT_CONFIG_STATE` (модуль ставить `<stateDir>/runtime-config.json`), overrides атомарно записуються у файл і відновлюються під час старту. У llama overrides тримає Django (pull під час старту). Коли з'явиться Django pull (SP4), версія від Django матиме пріоритет над локальним файлом.

### 4.3 rt-asr

- **Модель:** SP5 registry. На `cuda` — `whisper-large-v3-turbo` `int8_float16`, на `cpu`/`rocm` — `whisper-small` `int8`. Ліцензія MIT.
- **Мови.** `RT_ASR_LANGUAGES=pl,uk,ru,en` (TUNABLE). Якщо `language` не задано, мова визначається через `detect_language` з вибором argmax лише серед дозволених мов. Це прибирає типову плутанину uk↔ru. Якщо мову явно передано в запиті, береться вона.
- **`POST /v1/audio/transcriptions`** (OpenAI-сумісний multipart) приймає нові поля:
  - `chunk_seconds`: float, 30…900. Default `RT_ASR_CHUNK_SECONDS=600` (TUNABLE). Аудіо, довше за `chunk_seconds`, ріжеться на шматки: межа шукається в паузі між мовленням (faster-whisper VAD speech timestamps) у вікні `[chunk_seconds − lookback, chunk_seconds]`, де `lookback = RT_ASR_CHUNK_LOOKBACK_SECONDS=30`. Якщо паузи немає, жорсткий розріз іде на межі. Таймстемпи сегментів зсуваються на початок шматка. `prompt` наступного шматка — хвіст (≤ 200 символів) тексту попереднього.
  - `priority`: `auto|realtime|batch`, default `auto`. `auto` означає `realtime`, якщо тривалість ≤ 30 с, інакше `batch`. Realtime — один job із пріоритетом `REQUEST`. Batch — `submit_iter` з пріоритетом `BATCH` через лінивий генератор сегментів faster-whisper, тож realtime-запит чекає щонайбільше одне 30-секундне вікно декодування.
- **Ліміт завантаження:** `RT_ASR_MAX_UPLOAD_MB=100` (TUNABLE).
- **Відкладено разом з WS:** WebSocket `/v1/audio/stream`, серверний Endpointer і `StreamSession` (SP1 Tasks 8, 9, 12), а також WS-поля `AsrSettings`.

### 4.4 rt-tts

- **Модель:** Kokoro-82M ONNX (SP5 Tasks 11–12a), ORT-рушій, G2P через misaki. Ліцензія Apache-2.0.
- **Голоси лише англійські:** 28 голосів з префіксами `a`/`b`. Default `af_heart`. `/v1/audio/voices` повертає тільки їх.
- **TUNABLE:** `voice`, `speed`, `sample_rate`, `max_chars`.
- **Рушій torch** (`RT_TTS_ENGINE=torch`) відкладено.
- **Відтворення ніколи не відбувається на сервері.**

## 5. Сегментація на edge (виправлення «слова пополам»)

Спільний чистий модуль `src/segmentation/` (лише numpy). Він потрапляє і в speech-бандл, і в edge. У ньому два компоненти.

### 5.1 `PauseSegmenter` (потоковий, edge)

- Отримує вікна по 32 мс (512 семплів, 16 кГц) разом з імовірністю мовлення Silero. Емітить `Segment(audio, start_s, end_s, reason)`.
- `SegmenterConfig`:

  | Параметр | translate | transcribe |
  |---|---|---|
  | `min_speech_ms` | 400 | 400 |
  | `end_silence_ms` (звичайне завершення фрази; `None` = вимкнено) | 700 | `None` |
  | `soft_max_ms` (після нього завершуємо на короткій паузі) | 8 000 | 300 000 (`chunk_min_s`) |
  | `soft_silence_ms` | 250 | 500 |
  | `hard_max_ms` (обов'язковий розріз) | 20 000 | 600 000 (`chunk_max_s`) |
  | `lookback_ms` (де шукати найтихіше вікно для жорсткого розрізу) | 1 500 | 5 000 |
  | `keep_silence_ms` (довші паузи всередині сегмента вирізаються) | `None` | 2 000 |
  | `preroll_ms` | 300 | 300 |
  | `threshold` | 0.5 | 0.5 |

- **Інваріанти:**
  - жодного розрізу в момент, коли ймовірність мовлення ≥ `threshold`, якщо є тихіше вікно в `lookback`;
  - хвіст після розрізу переноситься в наступний сегмент, тобто аудіо не губиться і не дублюється;
  - `flush()` під час зупинки віддає незавершений сегмент, якщо він має ≥ `min_speech_ms` мовлення.
- **Параметри застосовуються наживо:** `reconfigure(cfg)` діє з наступного вікна.

### 5.2 `plan_chunks` (офлайн, сервер)

`plan_chunks(speech_spans, total_samples, chunk_samples, lookback_samples) -> list[tuple[int, int]]` — межі шматків для довгого файлу (§4.3).

- Шматки суміжні й покривають усе аудіо.
- Межа ставиться в середину найдовшої паузи у вікні lookback, а за відсутності паузи — на `chunk_samples`.

## 6. Edge

Основа — SP2 Tasks 6–13 з такими змінами.

### 6.1 Налаштування `EdgeSettings`

- Endpoint-и задаються списками:
  - `asr_urls: tuple[str, ...]`;
  - `tts_urls: tuple[str, ...]`;
  - `llm: LlmSettings(django_urls=(), django_token_file=None, service=None, lease_ttl_s=60, direct_url=None, direct_token_file=None, model="local", max_tokens_cap=512, temperature=0.1, context_pairs=3, timeout_s=15)`.
- **Звідки береться LLM:**
  - `django_urls` задано → lease (основний режим);
  - інакше `direct_url` → режим розробки (локальний `llama-server` або будь-який OpenAI-сумісний сервер; токен з `direct_token_file`).
- Три різні файли токенів:
  - `api_token_file` — для rt-*;
  - `llm.django_token_file` — DRF-токен користувача;
  - `llm.direct_token_file` — для dev.
- `mode`: `auto|transcribe|translate`.
- `source_language`: `auto|pl|uk|ru`.
- `segmentation.translate` і `segmentation.transcribe` (§5.1). Їх можна змінювати в `config.yml` і в UI без перезапуску.
- **Пріоритет:** env `RT_EDGE_*` > `config.yml` > defaults.
- **Env:** `RT_EDGE_MODE`, `RT_EDGE_ASR_URLS`, `RT_EDGE_TTS_URLS`, `RT_EDGE_DJANGO_URLS` (через кому), `RT_EDGE_DJANGO_TOKEN_FILE`, `RT_EDGE_LLM_SERVICE`, `RT_EDGE_LLM_DIRECT_URL`, `RT_EDGE_LLM_DIRECT_TOKEN_FILE`, `RT_EDGE_LLM_MODEL`, `RT_EDGE_API_TOKEN_FILE`.

### 6.2 `LeaseKeeper` і `LlmClient`

**`LlmTarget`** — звідки брати адресу й токен:
- метод `current() -> (base_url, bearer)`;
- метод `invalidate(kind)`;
- реалізації: `StaticTarget` (dev: `direct_url` + файл токена) і `LeaseKeeper`.

**`LeaseKeeper`** (клієнт протоколу lease, §5 spec lease):
- **`start()`:** acquire на першому доступному Django, потім дзеркальні acquire на решті з `service = <name>`.
- **Фоновий heartbeat** кожні `heartbeat_interval_s`. При 410/404 — новий acquire.
- **`invalidate(kind)`:**
  - `auth` → heartbeat, щоб отримати свіжий `worker_token`;
  - `unreachable`/`timeout`/503 → release і acquire іншого воркера.
- **`stop()`:** release на всіх Django.
- **Стан для UI:** ім'я воркера, модель, `expires_at`, остання помилка.

**`LlmClient.translate(text, source_lang, context=()) -> str`** надсилає `POST /v1/chat/completions` з тілом:
- `model`;
- `messages`:
  - system: `"You are a professional interpreter. Translate the user's {Polish|Ukrainian|Russian} speech transcript into natural English. Output only the English translation, nothing else. Fix obvious speech-recognition errors using context."`;
  - потім `context` як пари user (джерело) / assistant (переклад);
  - потім user: `text`;
- `temperature`;
- `max_tokens = min(cap, 32 + 3 × len(text.split()))`;
- `chat_template_kwargs: {"enable_thinking": false}`.

Обробка відповіді:
- `Authorization: Bearer <token>` з `target.current()`. При помилці `auth`/`unreachable`/`timeout`/503 → `target.invalidate(kind)` і **один** повтор;
- відповідь — `choices[0].message.content`, з якого видаляється `<think>…</think>`;
- порожня або некоректна відповідь → `ServiceError(kind="http")`;
- якщо `source_lang == "en"`, LLM не викликається і текст повертається як є.

### 6.3 `EndpointPool`

- `pick()` обирає endpoint із `ready`, а серед них — з мінімальним `pending`; при рівності вирішує порядок у списку.
- Endpoint закріплюється (`pin`) на всю сесію.
- При `ServiceError` типу `unreachable`/`timeout`/503 endpoint позначається як недоступний на 30 с, сесія закріплюється за наступним, і той самий запит повторюється **один раз**. Це відхилення від SP2 §7 «без повторів».
- Пули — лише для `rt-asr` і `rt-tts` (`ready`, `pending` з `/status`).
- LLM не має пулу: воркер обирає Django (lease). Стан LLM для UI береться з `LeaseKeeper` і з `/status` воркера (`ready = model.loaded`, з тим самим bearer).

### 6.4 `TranslationSystem`

**translate:**
1. `PauseSegmenter(translate)` віддає сегмент.
2. ASR з `priority=realtime`, `language` = вибрана мова або None, `prompt` = останні 3 розпізнані фрази.
3. LLM (через `LeaseKeeper`) з контекстом останніх 3 пар. Lease береться під час старту режиму `translate` і звільняється при зупинці або зміні режиму.
4. TTS повними реченнями.
5. Відтворення (`_playback_queue`) у вибраний вихід.

**transcribe:**
- `PauseSegmenter(transcribe)` віддає шматки по 5–10 хв. ASR викликається з `priority=batch` і timeout `max(60, 1.0 × тривалість_с)`.
- Текст іде в UI і в журнал.
- `stop()` робить `flush()` і чекає, доки черга обробиться, тобто втрати даних немає.

**Точка розширення:** `enqueue_playback(audio, sample_rate)` для майбутньої озвучки з engine. Зараз її викликає лише TTS-шлях.

### 6.5 Пакети

`homeManagerModules.rt-translator`:
- `endpoints.asr`, `endpoints.tts` (`listOf str`);
- `llm = { djangoEndpoints; djangoTokenFile; service; directUrl; directTokenFile; model; }`;
- `mode` (nullOr enum, default null = auto);
- `apiTokenFile`;
- `autostart`;
- `virtualSinks.enable`.

Параметри сегментації — лише в `config.yml`/UI, не в HM: вони змінюються без деплою.

## 7. Nix і деплой (дзеркало llama)

Основа — SP2 Tasks 0–5 без MT.

- **`nixosModules.rt-speech` → `services.rtSpeech.{asr,tts}`:**
  - `Environment = envLines cfg` (Colmena);
  - `EnvironmentFile = "-${deployRsPath}/env"` (deploy-rs; цей файл перекриває ключі);
  - `ExecStart` з `${deployRsPath}/bin/rt-<svc>`;
  - `ConditionPathExists`;
  - system user;
  - `LoadCredential` для токена;
  - firewall: порт закритий за замовчуванням, `tailscale0` — trusted у флоті;
  - нове: `RT_CONFIG_STATE=<stateDir>/runtime-config.json`.
- **`lib.rtSpeech`:**
  - `envLines`;
  - `mkEnvFile`;
  - `healthCheckCommand` (`curl -sf http://127.0.0.1:<port>/health`);
  - `services`.
- **Пакети й overlay:**
  - `packages.rt-asr-{cpu,gpu,rocm}`, `rt-tts-{cpu,gpu}`, `rt-edge`;
  - `overlays.default` → `pkgs.rtSpeech`.
- **nixpkgs:** lock піднімається до ревізії кореневого input `nixpkgs` інфри. Вузол визначається через `.locks.nodes.root.inputs.nixpkgs`.
- **Що робить інфра (не тут):**
  - flake input;
  - overlay;
  - узагальнення `mkDeployProfiles`/`mkEnvFile` з llama-специфічних на параметризовані;
  - `applications/services/rt-*.nix`;
  - рядки clusterops;
  - per-host `services.rtSpeech.*`.
  
  Покроково це описано в документі користувача (§9).
- **Поки інфра не готова:** працює fallback через Colmena без deploy-rs (`services.rtSpeech.<svc>.package = pkgs.rtSpeech.<svc>.<variant>`).

## 8. VRAM і розміщення (довідково)

- **Бюджет на прикладі GPU 8 GiB:** llama Qwen3.5-4B Q4_K_M (≈3.9 GiB при ctx 16384) + ASR turbo (≈1.3 GiB) + TTS на GPU (≤0.9 GiB) ≈ 6.1 GiB, що в межах 90 %.
- **Окремого перекладацького llama немає:** використовується наявний воркер на lease, тож VRAM llama вже врахований на його хості.
- **Admission:** ручна перевірка піку `nvidia-smi` під навантаженням. Автоматичний harness SP5 відкладено.

## 9. Документ користувача

`docs/USAGE.md` (українською) містить:

1. Огляд режимів і компонентів.
2. Локальний запуск у devShell:
   - сервіси з fake- і з реальними backend-ами;
   - edge;
   - локальний `llama-server` (llama.cpp, OpenAI-сумісний) як заміна llama-service для тестів.
3. Моделі: `rt-models list|fetch|verify`, автоматичне завантаження під час старту, офлайн-режим.
4. Конфіг сервісів:
   - env (таблиця);
   - `/config` з прикладами `curl`;
   - які поля TUNABLE, а які RELOAD.
5. Конфіг edge: `config.yml` (ключі сегментації), UI, env, HM-модуль.
6. Деплой:
   - (a) Colmena-only fallback;
   - (b) deploy-rs: кроки в інфрі (flake input, overlay, узагальнення env-рендера, `applications/services`, clusterops, per-host конфіг, CUDA substituter);
   - git-only реліз.
7. LLM через lease:
   - DRF-токен користувача (`api-token-auth`) у файлі;
   - `django_urls`;
   - бажаний воркер;
   - як перевірити lease (`curl`);
   - dev-режим з `llama-server`.
8. Перевірка: smoke `curl`, e2e чек-лист, типові помилки.
9. Що відкладено.

## 10. Що з specs від 2026-10-06 лишається, змінюється і відкладається

| Документ | Рішення |
|---|---|
| overview | §4 контракт: прибрати MT; доповнено цим spec. C1/C2/C4/C6/C0 лишаються. C5/C7 частково не актуальні (MT) |
| SP1 speech API | KEEP: Tasks 1–7, 10–11, 15–18 зі змінами з §4. DROP: 13–14. DEFER: 8, 9, 12 (WS) |
| SP2 nix packaging | KEEP: Tasks 0–13, 15–16 зі змінами з §6–§7. DEFER: 14 (WS). Task 15 (видалення легасі) виконується **одразу** після каркаса flake |
| SP3 infra | DEFER: виконується в swarm-nix. Тут — лише розділ документа користувача |
| SP4 Django | DEFER повністю |
| SP5 models | KEEP: Tasks 2–6, 10, 11, 12 (лише фабрика onnx), 12a (ASR/TTS). DROP: 7–9 (крім `pin_model`), 20, 23. DEFER: 1 (bench-shells), 13–19, 21, 22 |

## 11. Відкриті питання (не блокують)

- **espeak-ng (GPL-3.0)** може потрапити в closure misaki як fallback для OOV-слів. Для внутрішнього використання це не проблема (GPL регулює поширення). Перевірити, якщо пакет колись поширюватиметься.
- **Якість whisper-small на CPU для pl/uk/ru.** Перевірити на реальних записах. Якщо слабко, ASR ставити на GPU-хост.
- **Озвучка з engine на ПК** потребує каналу Django → edge (зміна Django). Зараз є лише `enqueue_playback`.
- **Обмеження lease** (spec lease §4): неоскоплений токен воркера; кілька Django — дзеркальні lease; реплікація пізніше.
