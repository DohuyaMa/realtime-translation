# Документ користувача: real-time translator

Технічні назви (ключі, команди, пакети) наведено англійською. Джерело істини для ключів - код (`src/services/*/settings.py`, `src/core/runtime.py`, `config/default.yml`).

## 1. Огляд режимів і компонентів

```
мікрофон -> edge (UI + VAD-сегментація) -> rt-asr -> [llama-воркер, app-сесія] -> rt-tts -> rt_virtual_output
```

| Компонент | Де працює | Пакет / запуск | Порт |
|---|---|---|---|
| edge (UI, сегментація, клієнти) | ПК користувача | `python -m src.main` | - |
| `rt-asr` (faster-whisper) | будь-який хост | `.#rt-asr-cpu`, `.#rt-asr-gpu`, `python -m src.services.asr` | 8091 |
| `rt-tts` (Kokoro ONNX) | будь-який хост | `.#rt-tts-cpu`, `.#rt-tts-gpu`, `python -m src.services.tts` | 8093 |
| LLM (llama-воркер) | локальний або вузол кластера (список від Django) | app-сесія напряму у воркера | 8080 |

Режими edge (`services.mode`):

- `transcribe` - лише довгі чанки (300-600 с) через `rt-asr`, текст без перекладу;
- `translate` - короткі фрази: ASR -> LLM -> TTS; потребує джерела LLM і TTS (`local.llm`/`local.tts` або `django_urls`);
- `auto` (default) - `translate`, якщо є джерела LLM і TTS, інакше `transcribe`.

Сервіси доступні лише через HTTP (OpenAI-сумісний стиль): `POST /audio/transcriptions`, `POST /audio/speech`, `GET /audio/voices`, `GET /models` (pinned моделі сервісу, `cached`), `GET /health`, `GET /status`, `GET|POST /config`. Опційний токен: `RT_API_TOKEN_FILE` (заголовок `Authorization: Bearer <token>` або `Token <token>`; edge шле `Token`).

Пакети flake: `rt-edge` (default package, `nix run .`), `rt-asr-{cpu,gpu}`, `rt-tts-{cpu,gpu}` (також ROCm-варіант ASR, якщо не `broken`), `nixosModules.rt-speech`, `nixosModules.virtual-sinks`, `overlays.default` (`pkgs.rtSpeech.{asr,tts}.<variant>`), `lib.rtSpeech`.

## 2. Локальний запуск у devShell

```bash
nix develop
```

Сервіси (у окремих терміналах):

```bash
# fake-backend-и (без моделей, для перевірки пайплайну)
RT_ASR_BACKEND=fake python -m src.services.asr
RT_TTS_BACKEND=fake python -m src.services.tts

# реальні backend-и
RT_ASR_BACKEND=faster-whisper python -m src.services.asr
python -m src.services.tts          # kokoro за замовчуванням
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

Моделі закріплені в реєстрі (`whisper-small`, `whisper-large-v3-turbo`, `kokoro-82m-v1.0-onnx`) і зберігаються в `$RT_MODEL_DIR` (default `~/.cache/rt-speech/models`).

```bash
rt-models list                         # alias, сервіс, ліцензія, ok|missing
rt-models fetch whisper-small kokoro-82m-v1.0-onnx
rt-models verify [--full] [alias...]   # quick (default) або повна перевірка
rt-models path <alias>
rt-models --model-dir /var/lib/rt-asr/models list
```

- Автозавантаження: під час старту сервіс сам завантажує відсутню модель (`RT_MODEL_DOWNLOAD=true`, default). Перевірка цілісності: `RT_MODEL_VERIFY=quick|full`.
- Офлайн-режим: `rt-models fetch ...` наперед, потім `RT_MODEL_DOWNLOAD=false`. Токен HuggingFace (за потреби) - файл у `RT_HF_TOKEN_FILE`.
- У `devShell` команда `rt-models` також доступна як `python -m src.services.common.models_cli`.

## 4. Конфіг сервісів

### 4.1 Env

Класи: **BOOTSTRAP** - лише через env/рестарт; **TUNABLE** - змінюється через `/config` без перезавантаження моделі; **RELOAD** - змінюється через `/config`, але модель перезавантажується (при помилці - автоматичний rollback). Default-и нижче - для `RT_DEVICE=cpu` (на `cuda` default `RT_MODEL` для ASR - `whisper-large-v3-turbo`, beam 3, compute `int8_float16`).
Таблиця згенерована (виконувати всередині `nix develop`): `python -m scripts.gen_env_table`.

`RT_IDLE_UNLOAD_MINUTES` (NixOS: `services.rtSpeech.{asr,tts}.idleUnloadMinutes`): модель вантажиться першим запитом і вивантажується після N хвилин без запитів (кожне завантаження/вивантаження - у лозі); `0` - завантажити на старті й не вивантажувати. `/health` модель не вантажить, показує `model.loaded`/`model.state`; `/status` вважає вивантажену модель `ready` (перший запит після простою чекає завантаження).

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
| `RT_ASR_MAX_UPLOAD_MB` | `100` | TUNABLE |
| `RT_ASR_REALTIME_MAX_SECONDS` | `30.0` | TUNABLE |
| `RT_ASR_VAD` | `true` | TUNABLE |
| `RT_COMPUTE_TYPE` | `int8` | RELOAD |
| `RT_CONFIG_STATE` | `(не задано)` | BOOTSTRAP |
| `RT_DEVICE` | `cpu` | RELOAD |
| `RT_IDLE_UNLOAD_MINUTES` | `15` | BOOTSTRAP |
| `RT_LISTEN_ADDRESSES` | `127.0.0.1` | BOOTSTRAP |
| `RT_LOG_LEVEL` | `info` | TUNABLE |
| `RT_MAX_PENDING` | `16` | BOOTSTRAP |
| `RT_MODEL` | `whisper-small` | RELOAD |
| `RT_MODEL_DIR` | `(не задано)` | BOOTSTRAP |
| `RT_PORT` | `8091` | BOOTSTRAP |

**rt-tts**

| Ключ | Default | Клас |
|---|---|---|
| `RT_API_TOKEN_FILE` | `(не задано)` | BOOTSTRAP |
| `RT_COMPUTE_TYPE` | `float32` | RELOAD |
| `RT_CONFIG_STATE` | `(не задано)` | BOOTSTRAP |
| `RT_DEVICE` | `cpu` | RELOAD |
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
### 4.2 `/config`

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

Помилки: 422 - невалідне значення або BOOTSTRAP-поле (`... is not runtime-configurable`); 500 - не вдалося перезавантажити модель (конфіг відкотився). Overrides зберігаються у файлі `RT_CONFIG_STATE` і переживають рестарт.

Статус: `curl -s http://127.0.0.1:8091/health` (liveness), `curl -s http://127.0.0.1:8091/status` (готовність моделі, VRAM, `pending`, `config_version`).

## 5. Конфіг edge

Файл `~/.config/real-time-translator/config.yml` (повний перелік - `config/default.yml`). Пріоритет: `RT_EDGE_*` env > `config.yml` > default. Значення з env у UI лише для читання.

| Ключ `config.yml` | Env | Опис |
|---|---|---|
| `services.mode` | `RT_EDGE_MODE` | `auto` / `transcribe` / `translate` |
| `services.django_urls` | `RT_EDGE_DJANGO_URLS` | Django для discovery (`GET /api/llm/app-services/`), свій хост першим |
| `services.django_token_file` | `RT_EDGE_DJANGO_TOKEN_FILE` | DRF-токен користувача (файл) |
| `services.allowed_services` | `RT_EDGE_ALLOWED_SERVICES` | дозволені сервіси `<kind>:<node>` (через кому в env); `null` - усі знайдені; вибір у UI |
| `services.local.{llm,asr,tts}` | `RT_EDGE_LOCAL_{LLM,ASR,TTS}_URL` | сервіси цього хоста, завжди першими (default: asr `http://127.0.0.1:8091`, llm/tts `null`); `null` при знайденому в discovery сервісі цього хоста = `http://127.0.0.1:<його порт>` |
| `services.local.node` | `RT_EDGE_LOCAL_NODE` | ім'я цього хоста в discovery (`null` = hostname, без урахування регістру); сервіс цього вузла (або з URL на адресу цього хоста) ніколи не викликається через tailscale-URL |
| `services.use.{llm,asr,tts}` | - | який екземпляр використовувати: `null`/`auto` - локальний першим + failover; ім'я вузла - лише він (без failover, недоступний = явна помилка). Вибір у панелі «Сервіси» головного вікна або в діалозі |
| `services.local_token_file` | `RT_EDGE_LOCAL_TOKEN_FILE` | токен локального воркера без Django, default `/run/llama/local-token` |
| `services.source_language` | - | `auto`/`pl`/`uk`/`ru`/`en` |
| `services.timeouts.{connect_s,asr_s,tts_s}` | - | 3 / 30 / 30 |
| `services.llm.session_ttl_s` | - | ttl app-сесії, 6..300, default 30 (heartbeat кожні ttl/3) |
| `services.llm.model` | `RT_EDGE_LLM_MODEL` | default `local` |
| `services.llm.{max_tokens_cap,temperature,context_pairs,timeout_s}` | - | 512 / 0.1 / 3 / 15 |
| `services.llm.system_prompt` | - | system prompt перекладу; змінні `{{source_lang}}`, `{{target_lang}}`; `""` = вбудований |
| `services.llm.user_template` | - | шаблон запиту до LLM: `{{text}}` (обов'язкова - куди вставляються слова), `{{source_lang}}`, `{{target_lang}}`; `""` = `{{text}}`. Старий синтаксис `{lang}`/`{text}` - WARNING, використовується типовий шаблон (видно в діалозі) |
| `services.llm.words_per_request` | - | скільки слів розпізнаного тексту в одному запиті до LLM (default 40, >= 1); довший текст ділиться по кінцях речень (якщо вміщаються, інакше по словах), кожна частина йде через шаблон, результати з'єднуються по порядку. Вкладка «Переклад» показує попередній перегляд запитів для зразкового речення |
| `services.asr.prompt` | - | Whisper prompt (словник, імена, стиль), додається перед контекстом останніх фраз |
| `services.tts.{voice,speed}` | - | голос (з `GET /v1/audio/voices`) і швидкість; `null` = типові сервісу |
| `segmentation.translate.*` | - | `threshold, min_speech_ms, end_silence_ms, soft_max_ms, soft_silence_ms, hard_max_ms, lookback_ms, preroll_ms` |
| `segmentation.transcribe.*` | - | `chunk_min_s` (300), `chunk_max_s` (600, <= 900), `soft_silence_ms, lookback_ms, keep_silence_ms` |

Сегментація: фраза закривається на паузі (`end_silence_ms`); якщо мовлення тягнеться довше `soft_max_ms`, ріжемо на найближчій паузі `soft_silence_ms`, лише в крайньому разі - на `hard_max_ms`. Це прибирає «слова пополам». Зміни `segmentation.*` і endpoints у UI (діалог налаштувань) застосовуються без рестарту застосунку.

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

HM-модуль edge: `homeManagerModules.rt-translator` (також `default`), опції `rt-translator.*`: `django.urls`, `django.tokenFile`, `local.{llm,asr,tts}` (default `http://127.0.0.1:{8080,8091,8093}`, `null` - сервісу на хості немає), `localTokenFile` (default `/run/llama/local-token`), `allowedServices` (`null` - усі; тоді вибір в UI), `llm.model`, `mode` (`null` = auto), `autostart`, `virtualSinks.enable`. Вони перетворюються на `RT_EDGE_*` env. Assertions: `mode = "translate"` потребує `local.llm` і `local.tts` або `django.urls`; `django.urls` потребує `django.tokenFile`; токен-файли не можуть бути в `/nix/store`.

Звук: потрібні PipeWire null-sink-и `rt_virtual_input` і `rt_virtual_output` (створює `nix develop`, `nixosModules.virtual-sinks` або HM `virtualSinks.enable`).

## 6. Деплой

### (a) Colmena-only

Через NixOS-модуль `nixosModules.rt-speech` (`services.rtSpeech.{asr,tts}`):

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

Спільні опції: `enable, package, deployRsPath, listenAddresses, port, device (cpu|cuda|rocm), model, configState, modelDir, apiTokenFile, openFirewall, firewallInterfaces, logLevel, rocmGfxVersion, memoryDenyWriteExecute, extraEnv`. ASR: `computeType, language, languages, beamSize, vad, chunkSeconds`. TTS: `voice, speed, sampleRate`. Потрібен overlay: `nixpkgs.overlays = [ inputs.realtime-translation.overlays.default ];` і `nixosModules.rt-speech` в `imports`.

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

**Налаштування сервісів (діалог «Налаштування»).** Вкладки «Розпізнавання», «Переклад», «Озвучка»: мої налаштування (Whisper prompt; system prompt і шаблон LLM, модель, temperature; голос і швидкість TTS) - у `config.yml`, йдуть із кожним запитом. Блок «Налаштування сервісу на вузлі» читає `GET /config` + `GET /v1/models` обраного вузла (модель із позначкою «кеш» / «завантажити ~розмір», beam size, VAD, мови, типовий голос...) і застосовує через `POST /config`; зміна моделі перезавантажує сервіс і, якщо моделі немає в кеші, завантажує її (`RT_MODEL_DOWNLOAD`). Ці значення спільні для всіх клієнтів вузла і зберігаються сервісом (`RT_CONFIG_STATE`).

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
5. зміна `chunk_min_s` в UI без рестарту.

Типові помилки:

- `401` від rt-*/воркера - токен зі списку Django не збігається (оновіть список: перезапуск/налаштування) або застарів `local_token_file`;
- `/status` `ready: false` - модель ще завантажується або відсутня (`rt-models list`, `RT_MODEL_DOWNLOAD`);
- `no llm worker available` - жоден дозволений воркер не відкрив сесію (див. лог WARNING по вузлах);
- `PipeWire preflight` - немає `rt_virtual_output`/`rt_virtual_input` (`nix develop` або virtual-sinks);
- `422` на `/config` - BOOTSTRAP-поле або значення поза межами.

## 9. Що відкладено

- WS-стрімінг ASR;
- серверна частина (Django `app-services`, воркер `/app-session` і пауза задач під сесією) - у swarm-nix;
- Django/engine: воркери tier 2, озвучка з engine на ПК, пул TTS/ASR для engine;
- harness і калібрування, ROCm spike, torch TTS.
