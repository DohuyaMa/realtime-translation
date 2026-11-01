# Документ користувача: real-time translator

Технічні назви (ключі, команди, пакети) наведено англійською. Джерело істини для ключів - код (`src/services/*/settings.py`, `src/core/runtime.py`, `config/default.yml`).

## 1. Огляд режимів і компонентів

```
мікрофон -> edge (UI, рівень, стрім) -> rt-asr (WS) -> одиниці -> rt-phonetic (HTTP, на одиницю) -> [llama-воркер, app-сесія] -> rt-tts -> rt_virtual_output
```

| Компонент | Де працює | Пакет / запуск | Порт |
|---|---|---|---|
| edge (UI, стріми, одиниці перекладу, клієнти) | ПК користувача | `python -m src.main` | - |
| `rt-asr` (faster-whisper) | будь-який хост | `.#rt-asr-cpu`, `.#rt-asr-gpu`, `python -m src.services.asr` | 8091 |
| `rt-phonetic` (wav2vec2 CTC, лише CPU) | будь-який хост | `.#rt-phonetic-cpu`, `python -m src.services.phonetic` | 8092 |
| `rt-tts` (Kokoro ONNX) | будь-який хост | `.#rt-tts-cpu`, `.#rt-tts-gpu`, `python -m src.services.tts` | 8093 |
| LLM (llama-воркер) | локальний або вузол кластера (список від Django) | app-сесія напряму у воркера | 8080 |

Режими edge (`services.mode`):

- `transcribe` - стрім `rt-asr`, кожна одиниця (речення / пауза) - рядок тексту, без перекладу;
- `translate` - одночасний переклад одиницями: стрім ASR -> фонетика (rt-phonetic) -> LLM -> TTS; потребує джерела LLM і TTS (`local.llm`/`local.tts` або `django_urls`);
- `auto` (default) - `translate`, якщо є джерела LLM і TTS, інакше `transcribe`.

Сервіси доступні лише через HTTP (OpenAI-сумісний стиль): `POST /audio/transcriptions`, `POST /audio/speech`, `GET /audio/voices`, `GET /models` (pinned моделі сервісу: `cached`, `size`, `warning` для великих моделей на CPU-вузлі), `GET /health`, `GET /status`, `GET|POST /config` (`GET` віддає також `schema`: для кожного поля тип `bool|int|float|choice|multichoice|model|text`, `choices`, `min`/`max`, опис, `reload` - з неї діалог будує випадаючі списки, лічильники й прапорці). Опційний токен: `RT_API_TOKEN_FILE` (заголовок `Authorization: Bearer <token>` або `Token <token>`; edge шле `Token`).

`rt-phonetic` (порт 8092; у режимі `translate` - перевірка кожної одиниці whisper і основа перекладу, див. «Фонетика» нижче) - символьна CTC-модель (wav2vec2, жадібне декодування, без словника і мовної моделі): пише літери, які чує, тож не вигадує фраз, а імена й терміни пише як чути («сайборг», «віспер»). Той самий runtime (`/health`, `/status`, `/config`, `POST /model/{load,unload}`, токен, `listenAddresses`, вивантаження за простоєм) і той самий запит, що й `rt-asr`: `POST /v1/audio/transcriptions` (multipart `file` = wav 16 кГц моно + `language`) -> `{"text": "<малі літери, без наголосів>", "language", "words": [{"word", "start", "end"}], "duration"}` (час слів - від початку надісланого аудіо; тиша - `"text": ""`). `RT_MODEL` - ОДНА модель (default `uk-wav2vec2-stress` = `mouseyy/uk_wav2vec2_with_stress_mark`, лише uk): сервіс тримає в пам'яті одну модель однієї мови; мову модель не визначає: `language` порожня / `auto` - мова моделі, інша мова - 409 `model_language_mismatch` (модель запитом ніколи не перемикається; перемикання - явне `POST /config {"values": {"model": ...}}`, у UI - вкладка «Фонетика»). Модель вантажиться - 503 `model_*`. `GET /v1/models` показує `language` кожної моделі. Лише CPU (float32, `RT_NUM_THREADS` потоків torch, default 4: ~0.3 с на 2 с мови, ~2.2 ГБ RAM).

Пакети flake: `rt-edge` (default package, `nix run .`), `rt-asr-{cpu,gpu}`, `rt-phonetic-cpu`, `rt-tts-{cpu,gpu}` (також ROCm-варіант ASR, якщо не `broken`), `nixosModules.rt-speech`, `nixosModules.virtual-sinks`, `overlays.default` (`pkgs.rtSpeech.{asr,phonetic,tts}.<variant>`), `lib.rtSpeech`.

## 2. Локальний запуск у devShell

```bash
nix develop
```

Сервіси (у окремих терміналах):

```bash
# fake-backend-и (без моделей, для перевірки пайплайну)
RT_ASR_BACKEND=fake python -m src.services.asr
RT_TTS_BACKEND=fake python -m src.services.tts
RT_PHONETIC_BACKEND=fake python -m src.services.phonetic

# реальні backend-и
RT_ASR_BACKEND=faster-whisper python -m src.services.asr
python -m src.services.tts          # kokoro за замовчуванням
python -m src.services.phonetic     # wav2vec2, модель uk-wav2vec2-stress
```

Edge:

```bash
python -m src.main
```

Локальний `llama-server` замість llama-service (dev):

```bash
nix run nixpkgs#llama-cpp -- llama-server -m <model.gguf> --port 8080 --api-key-file <file>
```

Edge говорить з воркером через app-сесію (розділ 7), тому голий `llama-server` без `/app-session` як LLM не підходить; для локального LLM потрібен llama-service воркер.

## 3. Моделі

Моделі закріплені в реєстрі (revision + sha256 кожного файлу): увесь каталог faster-whisper - `whisper-{tiny,base,small,medium}[.en]`, `whisper-large-v1/v2/v3`, `whisper-large-v3-turbo`, `whisper-distil-{large-v2,large-v3,large-v3.5,medium.en,small.en}` - і `kokoro-82m-v1.0-onnx`; для `rt-phonetic` - `uk-wav2vec2-stress` (uk, `mouseyy/uk_wav2vec2_with_stress_mark`, 1.26 ГБ на диску, ~2.2 ГБ RAM; 4 файли з HF, без токенізатора - словник `vocab.json` сервіс декодує сам). `.en` - лише англійська і зберігаються в `$RT_MODEL_DIR` (default `~/.cache/rt-speech/models`).

```bash
rt-models list                         # alias, сервіс, ліцензія, ok|missing
rt-models fetch whisper-small kokoro-82m-v1.0-onnx
rt-models verify [--full] [alias...]   # quick (default) або повна перевірка
rt-models path <alias>
rt-models --model-dir /var/lib/rt-asr/models list
```

- Автозавантаження: під час старту сервіс сам завантажує відсутню модель (`RT_MODEL_DOWNLOAD=true`, default). Перевірка цілісності: `RT_MODEL_VERIFY=quick|full`. Скачування не обмежене таймаутом: `/status` показує `model.state = "downloading"` і `model.download = {done_mb, total_mb}`; таймаут (300 с) діє лише на завантаження готових файлів у RAM/VRAM.
- Офлайн-режим: `rt-models fetch ...` наперед, потім `RT_MODEL_DOWNLOAD=false`. Токен HuggingFace (за потреби) - файл у `RT_HF_TOKEN_FILE`.
- У `devShell` команда `rt-models` також доступна як `python -m src.services.common.models_cli`.

## 4. Конфіг сервісів

### 4.1 Env

Класи: **BOOTSTRAP** - лише через env/рестарт; **TUNABLE** - змінюється через `/config` без перезавантаження моделі; **RELOAD** - змінюється через `/config`, модель перезавантажується: стара вивантажується, потім вантажиться нова (асинхронно; помилка - стан `error`, див. 4.2). Default-и нижче - для `RT_DEVICE=cpu` (на `cuda` default `RT_MODEL` для ASR - `whisper-large-v3-turbo`, beam 3, compute `int8_float16`).
Таблиця згенерована (виконувати всередині `nix develop`): `python -m scripts.gen_env_table`.

`RT_IDLE_UNLOAD_MINUTES` (NixOS: `services.rtSpeech.{asr,phonetic,tts}.idleUnloadMinutes`): модель вантажиться першим запитом і вивантажується після N хвилин без запитів (кожне завантаження/вивантаження - у лозі); `0` - завантажити на старті й не вивантажувати. `/health` модель не вантажить, показує `model.state`; `/status` вважає вивантажену за простоєм модель `ready` (перший запит після простою чекає завантаження з диска). Явне `POST /model/unload` запитами не скасовується: модель лишається вивантаженою до `POST /model/load` або зміни моделі.

**rt-asr**

| Ключ | Default | Клас |
|---|---|---|
| `RT_API_TOKEN_FILE` | `(не задано)` | BOOTSTRAP |
| `RT_ASR_BACKEND` | `faster-whisper` | RELOAD |
| `RT_ASR_BEAM_SIZE` | `1` | TUNABLE |
| `RT_ASR_CHUNK_LOOKBACK_SECONDS` | `30.0` | TUNABLE |
| `RT_ASR_CHUNK_SECONDS` | `600.0` | TUNABLE |
| `RT_ASR_LANGUAGE` | `(не задано)` | TUNABLE |
| `RT_ASR_LANGUAGES` | `pl,uk,ru,en` | TUNABLE |
| `RT_ASR_MAX_SESSIONS` | `4` | BOOTSTRAP |
| `RT_ASR_MAX_UPLOAD_MB` | `100` | TUNABLE |
| `RT_ASR_REALTIME_MAX_SECONDS` | `30.0` | TUNABLE |
| `RT_ASR_STREAM_TICK_MS` | `500` | BOOTSTRAP |
| `RT_ASR_VAD` | `true` | TUNABLE |
| `RT_COMPUTE_TYPE` | `int8` | RELOAD |
| `RT_CONFIG_STATE` | `(не задано)` | BOOTSTRAP |
| `RT_DEVICE` | `cpu` | RELOAD |
| `RT_DEVICES` | `cpu` | BOOTSTRAP |
| `RT_IDLE_UNLOAD_MINUTES` | `15` | BOOTSTRAP |
| `RT_LISTEN_ADDRESSES` | `127.0.0.1` | BOOTSTRAP |
| `RT_LOG_LEVEL` | `info` | TUNABLE |
| `RT_MAX_PENDING` | `16` | BOOTSTRAP |
| `RT_MODEL` | `whisper-small` | RELOAD |
| `RT_MODEL_DIR` | `(не задано)` | BOOTSTRAP |
| `RT_PORT` | `8091` | BOOTSTRAP |

**rt-phonetic**

| Ключ | Default | Клас |
|---|---|---|
| `RT_API_TOKEN_FILE` | `(не задано)` | BOOTSTRAP |
| `RT_COMPUTE_TYPE` | `float32` | BOOTSTRAP |
| `RT_CONFIG_STATE` | `(не задано)` | BOOTSTRAP |
| `RT_DEVICE` | `cpu` | BOOTSTRAP |
| `RT_DEVICES` | `cpu` | BOOTSTRAP |
| `RT_IDLE_UNLOAD_MINUTES` | `15` | BOOTSTRAP |
| `RT_LISTEN_ADDRESSES` | `127.0.0.1` | BOOTSTRAP |
| `RT_LOG_LEVEL` | `info` | TUNABLE |
| `RT_MAX_PENDING` | `16` | BOOTSTRAP |
| `RT_MODEL` | `uk-wav2vec2-stress` | RELOAD |
| `RT_MODEL_DIR` | `(не задано)` | BOOTSTRAP |
| `RT_PHONETIC_BACKEND` | `wav2vec2` | RELOAD |
| `RT_PHONETIC_MAX_UPLOAD_MB` | `20` | TUNABLE |
| `RT_PORT` | `8092` | BOOTSTRAP |

**rt-tts**

| Ключ | Default | Клас |
|---|---|---|
| `RT_API_TOKEN_FILE` | `(не задано)` | BOOTSTRAP |
| `RT_COMPUTE_TYPE` | `float32` | RELOAD |
| `RT_CONFIG_STATE` | `(не задано)` | BOOTSTRAP |
| `RT_DEVICE` | `cpu` | RELOAD |
| `RT_DEVICES` | `cpu` | BOOTSTRAP |
| `RT_IDLE_UNLOAD_MINUTES` | `15` | BOOTSTRAP |
| `RT_LISTEN_ADDRESSES` | `127.0.0.1` | BOOTSTRAP |
| `RT_LOG_LEVEL` | `info` | TUNABLE |
| `RT_MAX_PENDING` | `16` | BOOTSTRAP |
| `RT_MODEL` | `kokoro-82m-v1.0-onnx` | RELOAD |
| `RT_MODEL_DIR` | `(не задано)` | BOOTSTRAP |
| `RT_PORT` | `8093` | BOOTSTRAP |
| `RT_TTS_BACKEND` | `kokoro` | RELOAD |
| `RT_TTS_ENGINE` | `onnx` | RELOAD |
| `RT_TTS_MAX_CHARS` | `4096` | TUNABLE |
| `RT_TTS_SAMPLE_RATE` | `24000` | TUNABLE |
| `RT_TTS_SPEED` | `1.0` | TUNABLE |
| `RT_TTS_VOICE` | `af_heart` | TUNABLE |

Перед запитами: `TOKEN=$(cat /path/to/token)`, додавайте `-H "Authorization: Bearer $TOKEN"`, якщо сервіс запущено з `RT_API_TOKEN_FILE`.

```bash
H='Content-Type: application/json'

# поточні значення: values, source (override|bootstrap), bootstrap, version
curl -s http://127.0.0.1:8091/config

# TUNABLE: змінити chunk_seconds (version - будь-яке ціле, яке зберігається у стані)
curl -s -X POST http://127.0.0.1:8091/config -H "$H" \
  -d '{"version": 1, "values": {"chunk_seconds": 300}}'

# RELOAD: змінити model (відповідь міститиме "reloaded": true)
curl -s -X POST http://127.0.0.1:8091/config -H "$H" \
  -d '{"version": 2, "values": {"model": "whisper-large-v3-turbo"}}'

# reset: повернути поля до bootstrap (env)
curl -s -X POST http://127.0.0.1:8091/config -H "$H" \
  -d '{"version": 3, "values": {}, "reset": ["chunk_seconds", "model"]}'
```

Зміна моделі/пристрою: `POST /config` одразу відповідає `"reloaded": true`, сервіс вивантажує стару модель (у пам'яті ніколи немає двох) і асинхронно вантажить нову - стан у `/status`. Невдале завантаження (напр. CUDA OOM) - `model.state = "error"` і текст у `model.error`; старої моделі вже немає, конфіг не відкочується, тихого відкату на cpu немає: оберіть іншу модель/пристрій або `POST /model/load` ще раз.

Помилки: 422 - невалідне значення або BOOTSTRAP-поле (`... is not runtime-configurable`); 409 `model_busy` - інше завантаження/вивантаження ще триває (конфіг відкотився). Overrides зберігаються у файлі `RT_CONFIG_STATE` і переживають рестарт.

Керування моделлю (та сама авторизація, що й `/config`):

```bash
curl -s -X POST http://127.0.0.1:8091/model/unload -H "$H"   # звільнити RAM/VRAM
curl -s -X POST http://127.0.0.1:8091/model/load -H "$H"     # завантажити налаштовану модель (асинхронно)
```

Статус: `curl -s http://127.0.0.1:8091/health` (liveness), `curl -s http://127.0.0.1:8091/status`:

- `model`: `{id, state, error, download, device}`; `state` - `unloaded` | `downloading` | `loading` | `ready` | `error`; `download` - `{done_mb, total_mb}` лише під час скачування;
- `memory`: `{rss_mb, swap_mb, vram_mb}` процесу (`vram_mb` - `null`, якщо `nvidia-smi` не відповідає);
- `ready`, `pending`, `config_version`; rt-asr ще `dropped_segments: {hallucination, low_confidence}`;
- rt-asr: `sessions` - `{active, max, opened, tick_ms, open: [...]}`, для кожної відкритої WS-сесії `ticks`, `skipped_ticks`, `busy_ticks`, `step_ms_p50/max`, `finals`; ще `commit_lag_p50/p90`, `buffer_s`, `dropped_segments`, `prompt_echo`, `forced_commits`, `language`.

Поки модель не `ready`, `/v1/audio/*` одразу відповідає 503 з кодом `model_unloaded` / `model_downloading` / `model_loading` / `model_error` (чекає лише запит, що сам розбудив вивантажену за простоєм модель). Невдале або покинуте за таймаутом завантаження й вивантажена модель звільняють пам'ять одразу (`unload` моделі CTranslate2, `gc`, `malloc_trim`); покинуте завантаження блокує нові (409), доки не завершиться.

Галюцинації Whisper (rt-asr): сегмент відкидається, якщо `no_speech_prob > 0.6`, `avg_logprob < -1.0` або `compression_ratio > 2.4` (пороги faster-whisper, тут кожен окремо), або якщо весь сегмент - відома фраза-галюцинація (титри/фінал відео uk/pl/ru/en: «Дякую за перегляд», «Napisy stworzone przez społeczność Amara.org», ...; список - `src/services/asr/textfilter.py`). Кожне відкидання - INFO у лозі й лічильник у `/status`.

**Стрімінг `WS /v1/audio/stream` (rt-asr;** дизайн - `docs/superpowers/specs/2026-10-31-streaming-asr-simultaneous-translation.md` §1-3). Клієнт: `{"type":"start","language":"pl"|null,"sample_rate":16000,"prompt"?:str,"t0"?:сек}`, далі binary PCM16LE mono 16 кГц (будь-якої довжини, типово по 100 мс), `{"type":"stop"}`. Сервер: `ready{session_id, model}`, `final{seq, text, words:[{word,start,end,conf}], start, end, eos, language}` (лише дописується), `partial{text, start}` (замінює незафіксований хвіст), `endpoint{at}` (кінець мовлення), `error{code, state, message}`, `done`; кожне повідомлення несе `audio_end`. Час - секунди `t0` + семпли від `start`; на тій самій шкалі edge вирізає аудіо одиниці для rt-phonetic.
- токен - заголовок `Authorization` handshake-у (невірний - close 1008 до accept); сесій більше за `RT_ASR_MAX_SESSIONS` (4) - close 1013; модель не готова (вивантажена за простоєм - handshake її вантажить і чекає) - `error model_not_ready` + 1013; відкрита сесія не дає вивантажити модель за простоєм; `POST /model/unload` або зміна моделі - `error model_unloaded` + 1012; помилки протоколу (`binary` до `start`, невідомий `type`, `sample_rate` не 16000, невідома мова) - `error` + 1003; `stop` - решта фіналів, `done`, close 1000. Після `ready` сервер робить фіктивний декод 1 с тиші (прогрів);
- rt-asr (LocalAgreement-2): Silero VAD (з faster-whisper), кожні `RT_ASR_STREAM_TICK_MS` (500) перекодовує буфер мовлення (`beam 1`, `temperature 0`, часи слів, `initial_prompt` = вже зафіксований текст ≤ 200 символів) і фіксує спільний префікс двох останніх гіпотез (крім останнього слова новішої - його пунктуація ще не усталена); тиша ≥ 500 мс - фіксує все до кінця мовлення (`eos`) і шле `endpoint` (800 мс перевірено й відкинуто: +1 с p50 / +2.5 с p90 затримки - live test 2026-10-31 §18). Тік, що випав на ще не закінчений декод, пропускається (`skipped_ticks`). Фільтр галюцинацій і повторів - на кожну гіпотезу, повтор `prompt` (echo) відкидається. Буфер від 8 с обрізається по кінцю зафіксованого речення (коротший не ріжеться - whisper потрібен контекст; > 15 с - по кінцю сегмента; > 28 с без згоди - фіксація гіпотези як є, WARNING, `forced_commits`). `language: null` - мова визначається один раз (≥ 1 с мовлення, серед `RT_ASR_LANGUAGES`) і фіксується на сесію;

**Рівень входу.** Edge один раз, до стріму (і аудіо одиниць для фонетики), нормалізує рівень мікрофона (`src/audio/level.py`): огинаюча рівня мови (лише блоки мови - тиша й рівний шум підсилення не піднімають), підсилення до −20 dBFS, не більше +24 дБ, м'який лімітер; гучний вхід не приглушується. Індикатор рівня в UI - сирий RMS. Якщо мова ≥ 3 с тихіша за −38 dBFS - WARN «Мікрофон тихий — підніміть гучність входу (мова N dBFS, підсилення +M дБ)» у журналі UI і лозі (`get_stats()["mic"]`: `quiet`, `dbfs`, `gain_db`); на тихому вході другий розпізнавач чув менше, ніж whisper (live test §13).

**Одиниці перекладу (режим `translate`).** Edge шле 100-мс кадри в стрім rt-asr. Зафіксовані слова whisper (лише дописуються) ріжуться на одиниці (`src/core/simul.py`): кінець речення (`.`/`!`/`?`, не «…»), endpoint (тиша ≥ 500 мс), ≥ 12 неперекладених слів на межі клаузи (`,;:`), жорстко 20 слів. Крапка whisper - кінець речення, лише якщо одразу за нею не йде слово з малої літери без паузи (≤ 50 мс: whisper ставить крапку на короткій паузі посеред речення - «кожній екрані.» + «ноль ,5 секунды.»); шматок з одного слова коротший за 0.25 с («так.») не стає окремою одиницею - приєднується до попереднього речення (на початку одиниці - до наступного); речення, що закінчується останнім зафіксованим словом, чекає ≤ 0.5 с на наступне слово, поки partial whisper продовжується з малої літери (після endpoint - без очікування; live test 2026-10-31 §14). Короткий шматок на endpoint (≤ 3 слів без кінця речення: «використання принципів», «автентифікації авторизація») не стає окремою одиницею - приєднується до наступної, якщо наступні слова вже є, partial whisper продовжується або минуло < 1 с після endpoint; інакше (мовець замовк) - окрема одиниця (`get_stats()["units_short_merged"]` - скільки приєднано; §18). Незафіксований хвіст (слова після останньої одиниці + partial whisper) показується в UI сірим і не озвучується; одиниці не перетинаються, вже озвучене не виправляється. LLM отримує `SOUND:` (літери фонетики для одиниці - основа), `TEXT:` (одиниця whisper) і `NEXT (do not translate):` (решта зафіксованого + partial - лише для розуміння; витік NEXT у переклад - `next_leak`, відкидається). Переклад іде в TTS частинами: речення, довге - по клаузах (≥ 4 слів); тиша Kokoro по краях частини обрізається до 50 / 150 мс.

**Фонетика (rt-phonetic, 2026-11-01).** Whisper пише грамотно, але може вигадати фразу («Дякую за перегляд!») або впевнено поставити не те слово («висперстоянно»); символьна CTC-модель пише літери, що реально прозвучали, без словника - не вигадує, а імена й терміни пише як чути. Докази - `context/phonetic-bench-2026-11-01.md`. На кожну одиницю:
- **запит**: аудіо одиниці [t0, t1] (той самий відрізок, що й у записі сесії) - один `POST /v1/audio/transcriptions` до rt-phonetic з бюджетом 0.5 с + 0.25 с на секунду аудіо одиниці, не менше 1.5 с і не більше 4 с (10-секундна одиниця - 3 с; фіксовані 1.5 с не встигали на 5/39 довгих одиниць, §18; без failover у межах запиту; збій позначає вузол недоступним - наступна одиниця піде на наступний);
- **гейт у коді** (`core/crosscheck.py`, `gate_reason`): обидва тексти зводяться до літер (малі, без пробілів, пунктуації, цифр, апострофів) і порівнюються `difflib`-відношенням. Порожній транскрипт - тиша, whisper вигадав одиницю (`no_word`); схожість < 0.4 - інша фраза (`disagree`): одиниця не йде в LLM (INFO-рядок, подія в журналі UI, лічильники `gate_dropped` / `gate_disagree`; ledger просто йде далі). Слова whisper не абеткою моделі (латиниця «TypeScript, Docker» для uk) не рахуються - модель пише їх кирилицею як чути; одиниця лише з такими словами проходить (`foreign_terms`, `gate_foreign_terms`), < 4 літер whisper - без порівняння (`short_words`);
- **переклад «зі звуком» (умова F)**: system prompt `services.llm.sound_system_prompt` і шаблон `services.llm.sound_user_template` (типовий `SOUND: {{sound_text}}\nTEXT: {{text}}`): SOUND - основа перекладу, TEXT (whisper) - лише правопис, межі слів і числа; де розходяться в українському слові - вірити SOUND. Англійські терміни беруться з TEXT, не з SOUND (фонетика знає лише українські літери: з «trust SOUND» «Zero Trust» ставав «User registration», «AI-First» - «jailbreak», live test 2026-11-01): слово, яке whisper написав латиницею (API, Kubernetes, CI/CD), лишається як є, термін кирилицею («зеротраст», «деплой») - відповідний англійський термін. Приклади в prompt загальні; власні назви (машини, люди) - у «Словнику термінів» (`{{terms}}`). Як просто підказка SOUND не допомагав (впевнені помилки whisper виживали). Відповідь-коментар («I notice you provided…», «please provide», про TEXT/SOUND, «transcript») відкидається як `meta`; переклад, що є транслітерацією джерела латиницею замість перекладу («Raz dva tri.», «Takozh prosymo vas aktivuvaty logy.»; більше половини слів речення - не англійські слова, які збігаються з транслітерованим словом TEXT або SOUND; англійський словник - SCOWL `hunspellDicts.en_US`, `RT_EDGE_EN_DICT` задає обгортка rt-edge / devShell; терміни словника, абревіатури й CamelCase - англійські), відкидається як `transliterated`. Такі пари ніколи не йдуть у контекст LLM: live 2026-11-01 18:07 кілька транслітерованих пар у контексті - і модель далі лише транслітерувала, не відновлюючись (англійські слова на кшталт «Tot.» перевірка не ловить); відкинута відповідь «зі звуком», з якої в TTS ще нічого не пішло, перекладається один раз лише з whisper (`retry_whisper_only`, WARN у журналі); якщо речення вже пішли в TTS - звичайне відхилення (`rejected_after_tts`). Переклад, набагато коротший за одиницю (`too_short`: від 6 слів джерела - менше половини слів; калібровано на всіх одиницях 2026-11-01: 0 хибних спрацювань, live unit 26 15 -> 6 слів), у будь-якому режимі перекладається ще раз лише з whisper з рядком «translate all of TEXT, every item… nothing omitted» (`retry_incomplete`, WARN); якщо повтор теж закороткий - озвучується довша з двох відповідей (WARN, `spoken_incomplete`: неповний переклад краще, ніж загублена фраза); повтор, відкинутий з іншої причини (meta, транслітерація), - озвучується перша відповідь. Речення такої одиниці йдуть у TTS, щойно переклад досяг цієї половини (раніше `too_short` ще можливий), тож повтор ніколи не повторює озвучене;
- **лише whisper**: тайм-аут (`phonetic_timeout`), збій / не знайдено (`phonetic_unavailable`, стан `unavailable`), `services.use.phonetic = off` (стан `off`, без лічильника) - одиниця перекладається лише з whisper зі звичайним prompt; лічильник `whisper_only`, WARN у журналі UI, рядок «Фонетика (rt-phonetic)» у панелі «Сервіси» (`get_stats()["phonetic"]`: `mode` `on`/`off`/`unavailable`/`no_language`, `error`);
- **мова**: модель лише uk. Польська / російська (задана або визначена whisper) - 409 від сервісу: «фонетика недоступна для польської мови», одиниці цієї мови далі йдуть лише з whisper без запиту; задана мова перевіряється вже на прогріві. Вкладка «Фонетика» показує модель і мову вузла і кнопку «Завантажити модель для «<мова>»» (явна дія);
- **лог одиниці**: кожен прохід LLM - один INFO-рядок `unit #N (<причина розрізу>, t0-t1 s): gate pass (<agree|foreign_terms|short_words|no_phonetic>) | whisper … | phonetic … (similarity S, N ms) | llm sound|whisper-only|retry | next … | -> <переклад>`;
- **запис сесії для аналізу** (прапорець «Записувати сесію для аналізу» в головному вікні, `services.record_session`, env `RT_EDGE_RECORD_SESSION`, default вимкнено, діє одразу): кожна одиниця - `seg-<N>.wav` (аудіо, надіслане у фонетику) + рядки `phrases.jsonl` (`segment`: whisper зі словами, `phonetic`, `phonetic_words`, `phonetic_ms`, `similarity`, `gate`, `mode`; `translation`: `mode` `sound`/`whisper-only`/`retry`, TEXT, SOUND, NEXT, переклад, `rejected`) у `~/.local/state/real-time-translator/sessions/<час старту>/`. Офлайн-повтор: `python -m scripts.replay_session <каталог> --phonetic <url>` (§8). Помилка запису зупиняє запис явно (ERROR у журналі, `get_stats()["session_record"]`);
- **порівняння текстів**: у вікні «Розпізнаний текст» під кожною фразою whisper - рядок `Звук: …` (що почула фонетика; `(без фонетики)` / `(нічого не почула)`), прапорець «Показувати звук (фонетика)» (`ui.show_phonetic_text`).
- **старі ключі Vosk** у `config.yml` (`services.use.vosk`, `services.local.vosk`, `services.llm.check_*`, `ui.show_vosk_text`, `{{vosk_text}}`, `vosk:<вузол>` у `allowed_services`) - явна помилка налаштувань з назвою нового ключа (`services.use.phonetic`, `services.local.phonetic`, `services.llm.sound_*`, `ui.show_phonetic_text`, `{{sound_text}}`, `phonetic:<вузол>`): конвеєр не стартує, доки ключ не перейменовано.

## 5. Конфіг edge

Файл `~/.config/real-time-translator/config.yml` (повний перелік - `config/default.yml`). Пріоритет: `RT_EDGE_*` env > `config.yml` > default. Значення з env у UI лише для читання.

| Ключ `config.yml` | Env | Опис |
|---|---|---|
| `services.mode` | `RT_EDGE_MODE` | `auto` / `transcribe` / `translate` |
| `services.django_urls` | `RT_EDGE_DJANGO_URLS` | Django для discovery (`GET /api/llm/app-services/`), свій хост першим |
| `services.django_token_file` | `RT_EDGE_DJANGO_TOKEN_FILE` | DRF-токен користувача (файл) |
| `services.allowed_services` | `RT_EDGE_ALLOWED_SERVICES` | дозволені сервіси `<kind>:<node>` (через кому в env); `null` - усі знайдені; вибір у UI |
| `services.local.{llm,asr,tts,phonetic}` | `RT_EDGE_LOCAL_{LLM,ASR,TTS,PHONETIC}_URL` | сервіси цього хоста, завжди першими (default: asr `http://127.0.0.1:8091`, llm/tts/phonetic `null`); `null` при знайденому в discovery сервісі цього хоста = `http://127.0.0.1:<його порт>` |
| `services.local.node` | `RT_EDGE_LOCAL_NODE` | ім'я цього хоста в discovery (`null` = hostname, без урахування регістру); сервіс цього вузла (або з URL на адресу цього хоста) ніколи не викликається через tailscale-URL |
| `services.record_session` | `RT_EDGE_RECORD_SESSION` | «Записувати сесію для аналізу»: WAV кожної одиниці + `phrases.jsonl` у `~/.local/state/real-time-translator/sessions/<час>/` (default `false`) |
| `services.use.{llm,asr,tts,phonetic}` | - | який екземпляр використовувати: `null`/`auto` - локальний першим + failover; ім'я вузла - лише він (без failover, недоступний = явна помилка); для `phonetic` ще `off` - фонетику вимкнено, переклад лише з whisper. Вибір у панелі «Сервіси» головного вікна або в діалозі |
| `services.local_token_file` | `RT_EDGE_LOCAL_TOKEN_FILE` | токен локального воркера без Django, default `/run/llama/local-token` |
| `services.source_language` | - | `auto`/`pl`/`uk`/`ru`/`en` |
| `services.timeouts.{connect_s,asr_s,tts_s}` | - | 3 / 30 / 30 |
| `services.llm.session_ttl_s` | - | ttl app-сесії, 6..300, default 30 (heartbeat кожні ttl/3) |
| `services.llm.{max_tokens_cap,temperature,context_pairs,timeout_s}` | - | 512 / 0.1 / 3 / 15 |
| `services.llm.system_prompt` | - | system prompt перекладу; змінні `{{source_lang}}`, `{{target_lang}}`, `{{terms}}`; `""` = вбудований (типовий каже, що мова - про IT і терміни приходять кирилицею/розчутими неправильно, «церен» = CRM) |
| `services.llm.terms` | - | «Словник термінів» (вкладка «Переклад»): терміни через кому або з нового рядка; у system prompt (обох) замість `{{terms}}` - речення `Known terms (always written in English): …`, порожній словник - нічого. `null` = вбудований список IT-термінів (API, backend, … CRM, SaaS, Playwright, … деплой, фреймворк); `""` = без словника. Whisper prompt словник не отримує |
| `services.llm.user_template` | - | шаблон запиту до LLM: `{{text}}` (обов'язкова - куди вставляються слова), `{{source_lang}}`, `{{target_lang}}`; `""` = `{{text}}`. Старий синтаксис `{lang}`/`{text}` - WARNING, використовується типовий шаблон (видно в діалозі) |
| `services.llm.{sound_system_prompt,sound_user_template}` | - | prompt і шаблон перекладу «зі звуком» (одиниці, які почула фонетика): `{{sound_text}}` - літери фонетики (основа), `{{text}}` - whisper (у шаблоні обидві обов'язкові), `{{source_lang}}`, `{{target_lang}}`, у system prompt ще `{{terms}}`; `""` = вбудовані (`SOUND: {{sound_text}}\nTEXT: {{text}}`). У вкладці «Переклад» - дві групи («Лише whisper» і «Зі звуком»), список змінних з описом і рядок, яка пара активна зараз (за `services.use.phonetic`), з попереднім переглядом |
| `services.llm.words_per_request` | - | («Слів у {{text}} (максимум)» поряд із шаблоном) МАКСИМУМ слів в одному запиті до LLM (default 40, >= 1); неповний запит ніколи не чекає - одиниця йде в переклад одразу. Довший текст ділиться по кінцях речень (якщо вміщаються, інакше по словах), кожна частина йде через шаблон, результати з'єднуються по порядку. Вкладка «Переклад» показує попередній перегляд запитів для зразкового речення |
| `services.asr.prompt` | - | шаблон Whisper prompt (словник, імена, стиль), необов'язкова змінна `{{source_lang}}`; уже зафіксований текст стріму rt-asr додає до prompt сам (≤ 200 символів), повтор prompt (echo) сервер відкидає. `null`/`""` - без prompt; до 120 символів. Невідомі змінні (зокрема колишня `{{previous_text}}`) - помилка збереження; у вкладці «Розпізнавання» попередній перегляд |
| `services.tts.{voice,speed}` | - | голос (з `GET /v1/audio/voices`) і швидкість; `null` = типові сервісу |

Паузного сегментатора немає (крок S5): межі одиниць задає стрім - див. «Одиниці перекладу» вище. Ключі `segmentation.*` і `services.asr.previous_words` у старому `config.yml` ігноруються з WARNING у лозі.

**Приклад.** На кожному хості свої сервіси (`local.*`), решту кластера edge бере зі списку Django:

```yaml
services:
  mode: translate
  django_urls: ["http://127.0.0.1:8000", "http://cyborg:8000"]
  django_token_file: ~/.config/real-time-translator/drf-token
  local: { llm: "http://127.0.0.1:8080", asr: "http://127.0.0.1:8091", tts: "http://127.0.0.1:8093" }
  allowed_services: ["llm:cyborg", "asr:cyborg", "tts:cyborg"]
```

Режим `transcribe` лише з локальним ASR: `mode: transcribe` без `django_urls`.

Сервіс слухає кожну адресу з `RT_LISTEN_ADDRESSES` (список через кому, default `127.0.0.1`; окремий сокет на адресу, ще не піднята адреса теж біндиться - `IP_FREEBIND`). Для віддаленого доступу додайте кластерну адресу вузла поряд із `127.0.0.1`, напр. `RT_LISTEN_ADDRESSES=127.0.0.1,100.64.0.10` (+ firewall, + `RT_API_TOKEN_FILE`); `0.0.0.0` не потрібен.

HM-модуль edge: `homeManagerModules.rt-translator` (також `default`), опції `rt-translator.*`: `django.urls`, `django.tokenFile`, `local.{llm,asr,tts}` (default `http://127.0.0.1:{8080,8091,8093}`, `null` - сервісу на хості немає), `localTokenFile` (default `/run/llama/local-token`), `allowedServices` (`null` - усі; тоді вибір в UI), `mode` (`null` = auto), `autostart`, `virtualSinks.enable`. Вони перетворюються на `RT_EDGE_*` env. Assertions: `mode = "translate"` потребує `local.llm` і `local.tts` або `django.urls`; `django.urls` потребує `django.tokenFile`; токен-файли не можуть бути в `/nix/store`.

Звук: потрібні віртуальні пристрої PipeWire (одне визначення - `lib.rtSpeech.virtualDevicesConf`; створює HM `virtualSinks.enable` або `nixosModules.virtual-sinks`, після деплою - `systemctl --user restart pipewire pipewire-pulse wireplumber` або перелогін; для розробки - `nix develop` через `pactl`):

- `rt_virtual_input` - null-sink, у який інші застосунки віддають звук; застосунок слухає його монітор;
- `rt_virtual_output` («RT Переклад → мікрофон») - вихід перекладу: виберіть його в «Output Device»;
- `rt_virtual_mic` («RT Переклад (мікрофон)») - справжній мікрофон (`Audio/Source`, loopback від `rt_virtual_output`): його обирають Zoom/Teams/браузер, щоб чути переклад. Монітор `*.monitor` браузери часто ховають.

Пристрої застосунку не змінюють системних за замовчуванням: «Input Device» записується напряму (`parec --device`), «Output Device» - потік перекладу (`pacat --device`). «Також чути в навушниках» - переклад одночасно звучить і в «Output Device», і у вибраному пристрої (по одному `pacat` на пристрій, стартують разом). Вибір зберігається в `config.yml` (`audio.input_device`, `audio.output_device`, `audio.monitor_enabled`, `audio.monitor_device`; лише відмінні від типових значення).

Прогрів на старті: «Start Translation» паралельно прогріває всі сервіси конвеєра - відкриває стрім `WS /v1/audio/stream` rt-asr (`src/clients/stream.py`: handshake вантажить модель; вивантажена - `POST /model/load` і очікування `ready`; після `ready` сервер декодує 1 с тиші), rt-phonetic (завантаження моделі за потреби + 1 с тиші мовою сесії), LLM (одна відповідь на 1 токен) і rt-tts (завантаження за потреби + коротка озвучка). Захоплення звуку починається одразу: аудіо (16 кГц PCM16) чекає в кільцевому буфері стріму (≤ 30 с) і йде на сервер кадрами по 100 мс одразу після `ready`. Кожен прогрів - рядок у лозі з мс (`<kind> warm-up on <url>: N ms (model loaded first)`), невдалий - WARNING, переклад іде далі; рядок стану показує «Translation active - прогрів… (ASR, Фонетика)» з сервісами, що ще гріються, і один INFO-рядок `warm-up: all ready N ms after start (...)` - час від натискання до повної готовності; `get_stats()["warmup"]` / `["warmup_pending"]`.

Стрім-клієнт (`StreamClient`): черга відправки ≤ 5 с аудіо, надісланого після `ready` (більше - обрив; беклог кільця до `ready`, напр. холодне завантаження моделі, надсилається повністю поза лімітом; переповнення позначає вузол недоступним, лише якщо є інший); обрив (handshake відхилено, з'єднання закрито, `error`) - вузол позначається недоступним у пулі, відкривається наступний, і аудіо від кінця останнього зафіксованого слова повторюється з кільцевого буфера; фінали, що закінчуються не пізніше нього, відкидаються (дублікати за `end`). Немає жодного вузла - подія `failed` (раз на відмову), повтори з відступом 1 → 2 → 4 → 8 с, аудіо далі в буфер. `stop` - решта аудіо, `stop`, останні фінали до `done` (≤ 2 с).

Рубильник «Зупинити все й звільнити пам'ять» (головне вікно і трей): зупиняє захоплення і конвеєр, закриває app-сесію (воркер повертається до задач Django), зупиняє періодичний discovery і health-опитування, вивантажує моделі ЛИШЕ локальних `rt-asr`/`rt-tts` цього хоста (`POST /model/unload`; вузли кластера обслуговують інших). LLM llama-воркера не вивантажується - він обслуговує чергу Django. «Запустити» - у зворотному порядку: `POST /model/load`, чекає `ready`, відновлює discovery і, якщо переклад ішов, перезапускає його (нова app-сесія + прогрів усіх сервісів). Іконка в треї - дві «бульбашки» A/Я: сіра - зупинено/готовий, кольорова - перекладає, жовта позначка - завантаження, червона - помилка (деталі в підказці).

## 6. Деплой

### (a) Colmena-only

Через NixOS-модуль `nixosModules.rt-speech` (`services.rtSpeech.{asr,phonetic,tts}`):

```nix
{ pkgs, ... }: {
  services.rtSpeech.asr = {
    enable = true;
    package = pkgs.rtSpeech.asr.gpu;
    device = "cuda";
    listenAddresses = [ "127.0.0.1" "100.64.0.10" ];  # loopback + tailscale-адреса вузла
    openFirewall = true;            # лише на firewallInterfaces (default tailscale0)
    apiTokenFile = "/run/secrets/rt-speech-token";
  };
  services.rtSpeech.tts = {
    enable = true;
    package = pkgs.rtSpeech.tts.gpu;
    device = "cuda";
  };
}
```

Спільні опції: `enable, package, deployRsPath, listenAddresses, port, device (cpu|cuda|rocm), devices, model, configState, modelDir, apiTokenFile, openFirewall, firewallInterfaces, logLevel, rocmGfxVersion, memoryDenyWriteExecute, extraEnv`. ASR: `computeType, language, languages, beamSize, vad, chunkSeconds`. TTS: `voice, speed, sampleRate`. Phonetic: власних опцій немає (лише CPU: `devices` інші за `[ "cpu" ]` - assertion; `model` - один alias CTC-моделі, її мова - єдина, яку сервіс обслуговує). `devices` (default `[ device ]`, env `RT_DEVICES`) - між якими пристроями сервіс перемикається без redeploy: `devices = [ "cpu" "cuda" ]` ставить GPU-бандл (він працює і на cpu) і GPU-sandbox навіть при старті на cpu; `device` - стартовий. Перемикання - поле `device` у діалозі налаштувань вузла (`POST /config`, модель перезавантажується; default-и моделі/beam/compute переобчислюються для нового пристрою, якщо не задані явно). Помилка (напр. CUDA OOM) - явна: `/status` `model.state = "error"` з текстом у `model.error` (стара модель уже вивантажена, див. 4.2); тихого відкату на cpu немає. Потрібен overlay: `nixpkgs.overlays = [ inputs.realtime-translation.overlays.default ];` і `nixosModules.rt-speech` в `imports`.

### (b) deploy-rs (покроково, в інфрі swarm-nix)

1. Flake input: `realtime-translation.url = "github:DohuyaMa/realtime-translation";` (лише `github:`, без `path:`).
2. Overlay: `inputs.realtime-translation.overlays.default` у pkgs інфри (дає `pkgs.rtSpeech.*` і `lib.rtSpeech`).
3. Узагальнити `mkDeployProfiles.contractFor` і `mkEnvFile` (параметри `cfgPath`, `mkEnvLines`), щоб env-файл рендерився з опцій `services.rtSpeech`.
4. `applications/services/rt-asr-<variant>.nix`:
   ```nix
   mkApplication {
     name = "rt-asr-<variant>";
     serviceName = "rt-asr";
     appDir = pkgs.rtSpeech.asr.<variant>;
     stateDir = "/var/lib/rt-asr";
     healthCheck.command = lib.rtSpeech.healthCheckCommand "asr" cfg;
   }
   ```
5. clusterops: додати Service і виконати `generate_infra --regenerate`.
6. CUDA substituter: `https://cache.nixos-cuda.org` (ключ `cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M=`) у системному `nix.settings` GPU-хостів (`nixConfig` флейку ігнорується для untrusted користувачів).
7. Per-host `machines/<host>/modules/flake-services/rt-speech.nix` з `services.rtSpeech.*` (`deployRsPath = "/var/lib/rt-asr/current"`).

**Реліз (git-only):** commit + push у цьому репозиторії -> `nix flake update realtime-translation` в інфрі -> deploy.

## 7. Сервіси кластера: discovery, app-сесія, failover

Дизайн (owner, 2026-10-31): застосунок сам підключається до воркерів і сервісів; поки триває app-сесія, воркер не бере задач із Django. Django, як і раніше, лише виставляє задачі, а для застосунку віддає список сервісів і токени. Django lease API більше не використовується.

**Discovery.** `GET <django>/api/llm/app-services/` з `Authorization: Token <DRF>` по `django_urls` по черзі; відповідь `{"instance", "generated_at", "services": [{"kind": llm|asr|tts, "node", "url", "token", "tier", "models", "healthy"}]}`. Кожен сервіс викликається з `Authorization: Token <його token>`. Остання відповідь кешується в `$XDG_CACHE_HOME/real-time-translator/app-services.json` (0600). Список оновлюється при старті, при зміні налаштувань і у фоні кожні 5 хв (нові токени підставляються в чинні з'єднання). Токен воркера ротується при кожному рестарті/перереєстрації: на 401 від сервісу кластера edge робить свіже discovery (не частіше ніж раз на 30 с) і повторює запит один раз; повторний 401 - явна помилка `auth` і перехід на наступний вузол.

**Вибір і порядок.** `services.use.<kind>` = вузол - лише він, без failover. Інакше (auto) для кожного виду: спочатку локальний (`local.<kind>`, або 127.0.0.1 на порту знайденого сервісу цього хоста), далі дозволені (`allowed_services`, вибір у діалозі налаштувань) у порядку Django. Береться перший здоровий; при помилці - наступний (WARNING `switched A -> B`), позначений недоступним вузол повертається через 30 с, а LLM на кожному heartbeat перевіряє `/health` пріоритетніших воркерів і повертається на локальний. UI показує, який вузол обслуговує кожен сервіс (`локальний`, `офлайн-токен`) і джерело списку (кеш / лише локальні); у панелі «Сервіси» для кожного виду є вибір `auto` / конкретний вузол (зберігається в `services.use`, застосовується одразу).

**Конвеєр.** Стадії - окремі потоки з обмеженими чергами: стадія одиниць (події стрімів -> одиниці -> гейт), LLM, TTS, відтворення; порядок зберігається (одна нитка на стадію, номери одиниць). Переповнена черга блокує попередню стадію (backpressure: WARNING + статус «не встигає»), нічого не відкидається. Воркер наразі відповідає не потоком (одне JSON-тіло), тож перша частина перекладу готова з усією відповіддю LLM. Панель «Сервіси» і лог показують для останньої одиниці: фіксацію, LLM (перша частина / усього), TTS (перша частина) і «кінець мовлення → звук», глибину черг; `get_stats()["latency"]` - p50/p90 `commit_ms`, `llm_first_ms`, `tts_first_ms`, `ready_ms` (кінець мовлення → звук готовий), `e2e_ms` (→ початок відтворення), `playback_backlog_s` - секунд перекладу в черзі відтворення. **Наздоганяння черги** (власник 2026-11-01: озвучка не пришвидшується взагалі - лише коли відстає): поки в черзі відтворення > 4 с неозвученого перекладу, TTS просить Kokoro швидше - на крок 0.05 за кожну частину, до цілі за довжиною черги (×1.05 при ~2 с ... ×1.25 при ≥ 8 с); нижче 2 с - знову `tts.speed` (не задано - 1.0). Кожна зміна - INFO `tts catch-up on/speed/off`; `get_stats()["tts_catchup"]` (`active`, `factor`, `episodes`, `pieces`), панель «Сервіси»: «черга відтворення N с: озвучка ×1.15».

**Налаштування сервісів (діалог «Налаштування»).** Вкладки «Розпізнавання», «Переклад», «Озвучка»: мої налаштування (Whisper prompt; system prompt і шаблон LLM, temperature; голос і швидкість TTS) - у `config.yml`, йдуть із кожним запитом. Блок «Налаштування сервісу на вузлі» читає `GET /config` + `GET /v1/models` обраного вузла (модель із позначкою «кеш» / «завантажити ~розмір», beam size, VAD, мови, типовий голос...) і застосовує через `POST /config`; зміна моделі перезавантажує сервіс і, якщо моделі немає в кеші, завантажує її (`RT_MODEL_DOWNLOAD`). Ці значення спільні для всіх клієнтів вузла і зберігаються сервісом (`RT_CONFIG_STATE`). Над формою - **фактична** модель вузла з `GET /status` (не налаштована): id, стан (`не завантажена` / `завантажується з мережі N/M МБ (%)` / `завантажується в пам'ять` / `готова` / `помилка` - текст помилки червоним), пам'ять процесу (RAM, swap, VRAM); якщо налаштована модель ≠ фактичної - це показано окремим рядком. Кнопки «Завантажити модель» / «Вивантажити модель» (`POST /model/load|unload`), «Оновити» перечитує стан і налаштування; поки модель вантажиться або запит виконується, кнопки вимкнені, а стан опитується щосекунди. Панель «Сервіси» головного вікна показує те саме для кожного екземпляра ASR/TTS і лічильники проблем пайплайну (відкинуті LLM-відповіді, ASR без слів, помилки сервісів); відкинуті переклади й помилки сервісів (зокрема «модель не готова», HTTP 503) пишуться в System Log.

**Модель LLM-воркера (вкладка «Переклад», блок «Модель воркера»).** Воркер тримає в пам'яті ОДНУ модель для всіх клієнтів; поле `model` запиту він ігнорує, тож перекладає та модель, що завантажена у воркері (окремого налаштування моделі в edge немає; старі `services.llm.model` / `RT_EDGE_LLM_MODEL` / HM `llm.model` ігноруються з WARNING або помилкою nix). Блок діє на воркер, обраний у «Воркер (вузол)» (auto - перший за порядком). «Оновити» читає `GET /status` (фактична модель, джерело, помилка завантаження червоним, кількість app-сесій і стан черги Django) і `GET /models` (файли в MODEL_DIR воркера, з розміром). «Завантажити модель»: заповнене поле HF repo (напр. `IndexTeam/Index-Translate-2B-GGUF` + файл `Index-Translate-2B.Q8_0.gguf`; порожній файл - перший `.gguf` репозиторію) - `POST /models/load?repo_id=…&filename=…` (завантаження з Hugging Face у MODEL_DIR, далі в пам'ять), інакше - обраний файл (`?model_name=…`). Запит блокується до кінця завантаження (до 1 год), прогресу воркер не публікує: рядок стану показує «завантаження… N с», а стан моделі опитується щосекунди; помилка або таймаут - текстом. «Вивантажити модель» - `DELETE /models/unload`. **Воркер спільний із чергою Django:** поки app-сесія перекладача жива, воркер не бере задач черги; після неї черга працює на завантаженій тут моделі; задачі з закріпленою моделлю перемикають модель самі, а після простою воркер вивантажує модель і повертається до своєї типової (Nix).

**App-сесія у воркера.** `POST /app-session` `{"client": "rt-edge", "ttl_s": 30}` -> 201 `{"session_id", "ttl_s"}`; heartbeat `POST /app-session/<id>/heartbeat` кожні ttl/3 (404 - нова сесія); `DELETE /app-session/<id>` на stop. Далі `POST /v1/chat/completions` на тому ж воркері.

**Без Django.** Використовується кеш. Для локального воркера, якщо кешований токен не підходить (воркер перезапустився), edge один раз перечитує `local_token_file` (`/run/llama/local-token`, користувач у групі `llama-clients`) і повторює запит; статус «офлайн-токен». Повторний 401 - явна помилка `auth`. Без кешу і без Django - лише локальні сервіси.

Ручна перевірка:

```bash
D=http://127.0.0.1:8000; AUTH="Authorization: Token $(cat ~/.config/real-time-translator/drf-token)"
curl -s -H "$AUTH" $D/api/llm/app-services/ | jq '.services[] | {kind, node, url, healthy}'
W=http://127.0.0.1:8080; WA="Authorization: Token $(cat /run/llama/local-token)"
curl -s -X POST -H "$WA" -H 'Content-Type: application/json' $W/app-session -d '{"client": "manual", "ttl_s": 30}'
```

## 8. Перевірка

Smoke:

```bash
curl -s http://127.0.0.1:8091/health ; curl -s http://127.0.0.1:8093/health
curl -s http://127.0.0.1:8093/audio/voices
curl -s -X POST http://127.0.0.1:8093/audio/speech -H 'Content-Type: application/json' \
  -d '{"input": "hello", "response_format": "wav"}' -o /tmp/hello.wav
curl -s -F file=@/tmp/hello.wav http://127.0.0.1:8091/audio/transcriptions
```

Локальний e2e:

1. `nix develop`;
2. `RT_ASR_BACKEND=faster-whisper python -m src.services.asr`;
3. `python -m src.services.tts`;
4. llama-воркер (локальний або в кластері через `django_urls`);
5. `python -m src.main`.

Чек-лист e2e:

1. `/health` `rt-asr` і `rt-tts` ok, app-сесія відкрита (UI показує вузол воркера);
2. фраза українською -> англійська озвучка в `rt_virtual_output`;
3. поки триває app-сесія, воркер не бере задач із Django; після stop - бере;
4. 12-хвилинна транскрипція без втрат на `stop`;
5. зміна prompt у UI без рестарту.

Офлайн-стенд стріму (повтор WAV через справжній rt-asr у темпі мікрофона; фінали з затримкою фіксації, p50/p90, час до `ready`):

```bash
RT_API_TOKEN=$(sudo -n cat /run/secrets/rt-api-token) \
  python -m scripts.stream_bench запис.wav --language uk   # --speed 2 = удвічі швидше
```

Офлайн-стенд усього конвеєра (WAV -> стрім -> одиниці -> фонетика -> LLM -> TTS -> «відтворення» тривалістю аудіо, без звукового пристрою; p50/p90 затримок, лічильники, лог одиниць). Не на живих сервісах власника - приватні екземпляри на інших портах:

```bash
RT_LLM_TOKEN=$(sudo -n cat /run/llama/local-token) python -m scripts.pipeline_bench запис.wav \
  --language uk --asr http://127.0.0.1:18091 --phonetic http://127.0.0.1:18092 --tts http://127.0.0.1:18093 \
  --log units.log   # --gain-db -24 = тихий мікрофон; --phonetic '' = лише whisper
```

Повтор записаної сесії (`services.record_session`) через гейт фонетики і переклад «зі звуком» - як на edge, одиниця за одиницею (аудіо `seg-N.wav` -> rt-phonetic -> гейт -> LLM з SOUND/TEXT/NEXT і контекстом -> перевірка відповіді -> повтор лише з whisper); поряд - живий переклад із запису, у кінці - зведення (гейт, режими, відхилення, затримка фонетики p50/p90/max). Токени читаються з файлів і не друкуються; `--sound-system-prompt-file` / `--sound-user-template-file` - перевірити інший prompt:

```bash
python -m scripts.replay_session ~/.local/state/real-time-translator/sessions/<час> \
  --phonetic http://127.0.0.1:18092 [--llm http://127.0.0.1:8080] [--no-llm] [--out replay.jsonl]
```

Типові помилки:

- `401` від rt-*/воркера - токен зі списку Django не збігається (оновіть список: перезапуск/налаштування) або застарів `local_token_file`;
- `/status` `ready: false` - див. `model.state`/`model.error`: модель скачується, вантажиться, вивантажена (`POST /model/load`) або не завантажилась (`rt-models list`, `RT_MODEL_DOWNLOAD`);
- `no llm worker available` - жоден дозволений воркер не відкрив сесію (див. лог WARNING по вузлах);
- `PipeWire preflight` - немає `rt_virtual_input`/`rt_virtual_output`/`rt_virtual_mic` (`nix develop` або virtual-sinks + рестарт PipeWire);
- `422` на `/config` - BOOTSTRAP-поле або значення поза межами.

## 9. Що відкладено

- серверна частина (Django `app-services`, воркер `/app-session` і пауза задач під сесією) - у swarm-nix;
- Django/engine: воркери tier 2, озвучка з engine на ПК, пул TTS/ASR для engine;
- harness і калібрування, ROCm spike, torch TTS.
