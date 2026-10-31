# Документ користувача: real-time translator

Технічні назви (ключі, команди, пакети) наведено англійською. Джерело істини для ключів - код (`src/services/*/settings.py`, `src/core/runtime.py`, `config/default.yml`).

## 1. Огляд режимів і компонентів

```
мікрофон -> edge (UI + VAD-сегментація) -> rt-asr -> [LLM через lease] -> rt-tts -> rt_virtual_output
```

| Компонент | Де працює | Пакет / запуск | Порт |
|---|---|---|---|
| edge (UI, сегментація, клієнти) | ПК користувача | `python -m src.main` | - |
| `rt-asr` (faster-whisper) | будь-який хост | `.#rt-asr-cpu`, `.#rt-asr-gpu`, `python -m src.services.asr` | 8091 |
| `rt-tts` (Kokoro ONNX) | будь-який хост | `.#rt-tts-cpu`, `.#rt-tts-gpu`, `python -m src.services.tts` | 8093 |
| LLM (llama-воркер) | кластер, lease від Django | або локальний `llama-server` (dev) | 8080 (dev) |

Режими edge (`services.mode`):

- `transcribe` - лише довгі чанки (300-600 с) через `rt-asr`, текст без перекладу;
- `translate` - короткі фрази: ASR -> LLM -> TTS; потребує LLM (`django_urls` або `direct_url`) і `tts_urls`;
- `auto` (default) - `translate`, якщо є джерело LLM і `tts_urls`, інакше `transcribe`.

Сервіси доступні лише через HTTP (OpenAI-сумісний стиль): `POST /audio/transcriptions`, `POST /audio/speech`, `GET /audio/voices`, `GET /health`, `GET /status`, `GET|POST /config`. Опційний bearer-токен: `RT_API_TOKEN_FILE` (заголовок `Authorization: Bearer <token>`).

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

і в `config.yml`: `services.llm.direct_url: "http://127.0.0.1:8080"`, `services.llm.direct_token_file: <file>` (див. розділ 7).

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
| `RT_HOST` | `127.0.0.1` | BOOTSTRAP |
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
| `RT_HOST` | `127.0.0.1` | BOOTSTRAP |
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
| `services.asr_urls` | `RT_EDGE_ASR_URLS` | список rt-asr (через кому в env); вибір за найменшим `pending` |
| `services.tts_urls` | `RT_EDGE_TTS_URLS` | список rt-tts |
| `services.api_token_file` | `RT_EDGE_API_TOKEN_FILE` | bearer-токен для rt-* (файл) |
| `services.source_language` | - | `auto`/`pl`/`uk`/`ru`/`en` |
| `services.timeouts.{connect_s,asr_s,tts_s}` | - | 3 / 30 / 30 |
| `services.llm.django_urls` | `RT_EDGE_DJANGO_URLS` | Django з lease API (primary) |
| `services.llm.django_token_file` | `RT_EDGE_DJANGO_TOKEN_FILE` | DRF-токен користувача |
| `services.llm.service` | `RT_EDGE_LLM_SERVICE` | бажаний воркер (`null` - Django обирає) |
| `services.llm.lease_ttl_s` | - | 15..300, default 60 |
| `services.llm.direct_url` | `RT_EDGE_LLM_DIRECT_URL` | OpenAI-сумісний сервер: dev-режим, якщо `django_urls` порожній; інакше - ціль режиму «без lease» |
| `services.llm.direct_token_file` | `RT_EDGE_LLM_DIRECT_TOKEN_FILE` | токен `direct_url` (для llama-воркера - його `worker_token`) |
| `services.llm.model` | `RT_EDGE_LLM_MODEL` | default `local` |
| `services.llm.{max_tokens_cap,temperature,context_pairs,timeout_s}` | - | 512 / 0.1 / 3 / 15 |
| `segmentation.translate.*` | - | `threshold, min_speech_ms, end_silence_ms, soft_max_ms, soft_silence_ms, hard_max_ms, lookback_ms, preroll_ms` |
| `segmentation.transcribe.*` | - | `chunk_min_s` (300), `chunk_max_s` (600, <= 900), `soft_silence_ms, lookback_ms, keep_silence_ms` |

Сегментація: фраза закривається на паузі (`end_silence_ms`); якщо мовлення тягнеться довше `soft_max_ms`, ріжемо на найближчій паузі `soft_silence_ms`, лише в крайньому разі - на `hard_max_ms`. Це прибирає «слова пополам». Зміни `segmentation.*` і endpoints у UI (діалог налаштувань) застосовуються без рестарту застосунку.

**Приклад для двох ПК.** ПК A (з GPU) - лише сервіси; ПК B - edge.

ПК B, режим `translate`:

```yaml
services:
  mode: translate
  asr_urls: ["http://pc-a:8091"]
  tts_urls: ["http://pc-a:8093"]
  api_token_file: ~/.config/real-time-translator/rt-token
  llm:
    django_urls: ["https://django.example"]
    django_token_file: ~/.config/real-time-translator/drf-token
```

ПК B, режим `transcribe` (лише ASR, без LLM/TTS):

```yaml
services:
  mode: transcribe
  asr_urls: ["http://pc-a:8091"]
segmentation:
  transcribe: { chunk_min_s: 300, chunk_max_s: 600 }
```

Для віддаленого доступу сервіси слухають не `127.0.0.1`: `RT_HOST=0.0.0.0` (+ firewall, + `RT_API_TOKEN_FILE`).

HM-модуль edge: `homeManagerModules.rt-translator` (також `default`), опції з префіксом `rt-translator.*`: `endpoints.asr`, `endpoints.tts`, `llm.{djangoEndpoints,djangoTokenFile,service,directUrl,directTokenFile,model}`, `mode` (`null` = auto), `apiTokenFile`, `autostart`, `virtualSinks.enable`. Вони перетворюються на `RT_EDGE_*` env. Assertions: `mode = "translate"` потребує джерела LLM (`llm.djangoEndpoints` або `llm.directUrl`) і непорожнього `endpoints.tts`; `llm.djangoEndpoints` потребує `llm.djangoTokenFile`; токен-файли не можуть бути в `/nix/store`.

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
    host = "0.0.0.0";
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

Спільні опції: `enable, package, deployRsPath, host, port, device (cpu|cuda|rocm), model, configState, modelDir, apiTokenFile, openFirewall, firewallInterfaces, logLevel, rocmGfxVersion, memoryDenyWriteExecute, extraEnv`. ASR: `computeType, language, languages, beamSize, vad, chunkSeconds`. TTS: `voice, speed, sampleRate`. Потрібен overlay: `nixpkgs.overlays = [ inputs.realtime-translation.overlays.default ];` і `nixosModules.rt-speech` в `imports`.

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

## 7. LLM через lease

Primary-режим: edge отримує від Django ексклюзивний lease на llama-воркер на час сесії; воркер не бере WorkItem, доки lease живий.

> Серверний lease API з'явиться в Django після виконання spec `2026-10-22-llm-worker-lease-design.md` у swarm-nix. До того працює лише dev-режим (нижче).

DRF-токен у файл:

```bash
curl -s -X POST https://django.example/api/api-token-auth/ \
  -d 'username=<user>&password=<pass>' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])' \
  > ~/.config/real-time-translator/drf-token
chmod 600 ~/.config/real-time-translator/drf-token
```

Конфіг: `services.llm.django_urls: ["https://django.example"]`, `django_token_file: <файл>`, `service: <ім'я воркера>` (необов'язково).

Ручна перевірка lease:

```bash
D=https://django.example; AUTH="Authorization: Token $(cat ~/.config/real-time-translator/drf-token)"
# кандидати
curl -s -H "$AUTH" $D/api/llm/leases/candidates/
# acquire: 201 LeaseOut (id, service, worker_token, ttl_s, heartbeat_interval_s, expires_at); 409 - воркер зайнятий; 503 - немає воркера
curl -s -X POST -H "$AUTH" -H 'Content-Type: application/json' $D/api/llm/leases/ \
  -d '{"client_name": "manual-check", "service": null, "ttl_s": 60}'
# heartbeat (200; 410 - lease прострочено)
curl -s -X POST -H "$AUTH" -H 'Content-Type: application/json' $D/api/llm/leases/<uuid>/heartbeat/ -d '{}'
# release (204, ідемпотентно)
curl -s -X DELETE -H "$AUTH" $D/api/llm/leases/<uuid>/
```

Поки lease живий, `next()` для воркера повертає `{"paused": true, "reason": "leased", "retry_after": 10}`.

Що бачить UI: статус LLM (`lease` / `direct` / `none`), ім'я воркера та модель, помилки acquire (409 - зайнятий, 503 - немає воркера); edge сам робить heartbeat і release на `stop`.

**Режим «без lease» (Django недоступні).** Переклад не зупиняється, якщо якийсь Django або інший хост вимкнений:

- частина Django недоступна - lease береться на доступних, недоступні пропускаються (WARNING у лозі, `skipped` у статусі); коли Django знову відповідає, edge сам бере на ньому дзеркальний lease;
- жоден Django не відповідає - edge переходить у режим **без lease**: WARNING `NO-LEASE mode` у лозі, у UI рядок LLM «без lease» (червоний) з джерелом. Ціль: воркер останнього lease цієї сесії (`last_lease:<воркер>`), інакше `direct_url` + `direct_token_file`. Без жодної цілі старт падає з явною помилкою;
- heartbeat-потік кожні ~10 с пробує взяти lease; щойно вдалося - WARNING `leaving no-lease mode`, UI знову показує `lease`;
- якщо Django відповів відмовою (409 - воркер зайнятий, 503 - немає воркера), режим «без lease» **не** вмикається: це відмова, а не збій.

Без lease воркер не на паузі й паралельно бере WorkItem - переклад може бути повільнішим.

**Dev-режим без Django:** `services.llm.django_urls: []`, `direct_url: "http://127.0.0.1:8080"`, `direct_token_file: <файл з --api-key-file>`; `llama-server` - див. розділ 2.

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
4. `llama-server` (dev) або lease;
5. `python -m src.main`.

Чек-лист e2e:

1. `/health` `rt-asr` і `rt-tts` ok, lease отримано (UI показує воркер);
2. фраза українською -> англійська озвучка в `rt_virtual_output`;
3. поки триває сесія, воркер не бере WorkItem (`next()` -> `paused`/`leased`); після stop - бере;
4. 12-хвилинна транскрипція без втрат на `stop`;
5. зміна `chunk_min_s` в UI без рестарту.

Типові помилки:

- `401` від rt-* - не збігається токен (`RT_API_TOKEN_FILE` / `api_token_file`);
- `/status` `ready: false` - модель ще завантажується або відсутня (`rt-models list`, `RT_MODEL_DOWNLOAD`);
- `409` на acquire - воркер уже в lease іншого клієнта; `503` - немає онлайн chat-воркера;
- `PipeWire preflight` - немає `rt_virtual_output`/`rt_virtual_input` (`nix develop` або virtual-sinks);
- `422` на `/config` - BOOTSTRAP-поле або значення поза межами.

## 9. Що відкладено

- WS-стрімінг ASR;
- серверна частина lease (Django `WorkerLease`, API, `next()`; llama: `pause_reason`, без idle-unload під lease, автозавантаження в chat) - окремі сесії в swarm-nix;
- Django/engine: воркери tier 2, озвучка з engine на ПК, пул TTS/ASR для engine;
- harness і калібрування, ROCm spike, torch TTS.
