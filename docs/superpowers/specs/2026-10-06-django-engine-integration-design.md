# Підпроєкт 4 — Django/engine integration для rt-asr / rt-mt / rt-tts

Дата: 2026-10-06
Статус: draft (архітектурний шлях; рішення делеговано агенту, напрямок **A** затверджено — див. overview). Узгоджено 2026-10-06 (overview §7 C5/C7): `httpx` у bundle — SP2; env воркера на хостах — `extraEnv` модуля SP2.
Базовий документ: [`2026-10-06-distributed-speech-services-overview.md`](2026-10-06-distributed-speech-services-overview.md) (§4.2 env, §4.3 HTTP API, §4.6 Django).
Репозиторії: цей (`real-time-transletor` — service-side worker) + `~/repos/swarm-nix/django_api` (Django/engine).
План: [`../plans/2026-10-06-django-engine-integration.md`](../plans/2026-10-06-django-engine-integration.md).

---

## 0. Розуміння задачі (write-back)

**Що сказано (бриф + overview):**
- `rt-asr`, `rt-mt`, `rt-tts` мають споживатись Django `engine` у batch-режимі через ту саму WorkItem-чергу, що й llama-воркери (pull-only: register → `/next/` → `/complete/`).
- Воркер у сервісах вимкнений за замовчуванням (`RT_WORKER_ENABLED=false`); bootstrap-токен — **тільки з файлу**; per-service access token.
- `next()` зараз фільтрує лише за `tier` → треба фільтр за `artifact_type` без зламу LLM-воркерів.
- Нові `artifact_type`: `asr_transcribe`, `mt_translate`, `tts_synthesize`; `WorkerType audio`; engine-workflow мусить вміти ланцюжок ASR→MT→TTS.
- Аудіо — **посиланням**, не base64 у `prompt`; визначити шлях вивантаження TTS, схеми результатів, ліміти, таймаути.
- Вирішити долю реєстру сервісів (`LLMService` без `kind`, хардкод `:8080`) і health-sync (баг `model_path`).
- Тести з обох боків. Поза scope: realtime edge, Nix-пакування, деплой.

**Припущення (мої, не сказані):**
- Підпроєкт 1 ([speech-services-api-design](2026-10-06-speech-services-api-design.md) §4.3, §4.7, §9) дає `ServiceRuntime.submit_threadsafe`, backend-протоколи і `lifespan_hooks` (див. §5.6); воркер викликає backend через inference-чергу runtime, не через власний HTTP API.
- У Django немає жодного media/file-storage (перевірено: нема `MEDIA_ROOT`, `FileField`, `default_storage`) — механізм зберігання аудіо будується з нуля в цьому підпроєкті.
- Два незалежні Django-інстанси (cyborg, SKTOP-NOTE27-NIXOS) з окремими БД: воркер поллить обидва, blob живе на тому інстансі, що створив WorkItem.

**Критерій успіху:**
1. Живий e2e-доказ (правило North Star "доведено теоретично не рахується"): `manage.py speech_submit sample.wav --blueprint speech_translate_audio --target-lang en` → три WorkItem виконані реальними `rt-asr`/`rt-mt`/`rt-tts`, у Django лежить WAV-blob з озвученим перекладом.
2. Жоден існуючий llama-воркер (без змін у `system-conf`) не отримує speech-WorkItem і продовжує отримувати свої.
3. Повні тести з обох боків зелені.

---

## 1. Ключові факти з коду (2026-10-06), на які спирається дизайн

| Факт | Де |
|---|---|
| `next()`: `?tier=N` → `apply_tier_filter()` + `order_by_tier_preference()`; без tier → `exclude(worker_type__in=get_lm_studio_worker_types())`; `SELECT FOR UPDATE SKIP LOCKED`, скан ≤20 кандидатів; відповідь `{"paused": true, "retry_after"}` для `is_active=False` | `engine/views_work_item.py:44-326` |
| `WorkItemSerializer` віддає воркеру `uuid, prompt, max_tokens, temperature, artifact_type, repeat_penalty, top_p` | `engine/serializers_work_item.py` |
| `complete()`: `raw_result` → `ValidatorRegistry.run(expected_format)` → `parsed_result` (поле `parsed_result` від воркера **ігнорується**); далі `validate_capability_input(task.artifact_type, …)` і `PluginRegistry.get_output_validator(task.artifact_type)` | `engine/views_work_item.py:328-435` |
| Валідаційна помилка → `build_retry_prompt()` **перезаписує `prompt`** людським текстом; після вичерпання → `maybe_decompose()` (LLM-реконсолідація) | `_handle_validation_error` |
| `WorkItem.tier` = `classify_tier(worker_type)`: `WorkerType.tier` з БД, інакше fallback **2** → `audio` (tier=null) потрапив би в tier-2 і його б забрав LLM-воркер | `engine/services/tier_classifier.py`, `workitem_factory.resolve_tier` |
| `WorkerType` DB-driven: джерело правди — `engine/constants.py::WORKER_TYPE_DEFINITIONS`, сідінг `bootstrap_blueprints --worker-types`, YAML регенерується `dump_workertypes` | ADR-029, `bootstrap_blueprints.py:91` |
| `audio` зарезервований, `is_active=false`, tier null | `engine/blueprints/worker_types.yaml` |
| Прецедент не-LLM задачі: `rerank_score` — JSON у `prompt`, worker `tasks/registry.py` диспатчить за `artifact_type`; Django — internal step handler + deferred parent + fan-out | `knowledge/handlers/rerank.py`, `system-conf/.../tasks/rerank_task.py` |
| Generic «чекати на зовнішній WorkItem»: deferred parent з `metadata.waiting_on_workitem_uuid` → `resolve_deferred_parent_waiting_on()` у success-шляху `complete()`; **на термінальну помилку дитини parent висить назавжди**; пише статус напряму, а не через `finish_deferred_parent()` | `engine/services/workitem_factory.py:187-212`, `clusterops/handlers/machine_bootstrap.py:351` |
| `WorkflowExecutor.on_complete(task)` просуває DAG для **будь-якого** WorkItem з `metadata.blueprint_name`+`session_id` (listener на `workitem_completed`) → зовнішня «дитина» не має нести ці ключі | `engine/listeners.py:78`, `workflow_executor.py:137-153` |
| `build_input_data(step, parent_task)`: `condition` → `parent.metadata` → `parent.parsed_result`; `field_mapping` `{field: '<target>.<key>'}` резолвиться з done-сиблінгів за `artifact_type` | `engine/services/capability_input.py` |
| Стейл-ресет in_progress: `engine_poll._reset_stale(timeout=300)` — **глобальні 300 с**, поле `WorkItem.timeout_seconds` ніде не читається | `engine/management/commands/engine_poll.py:396` |
| Реєстрація: `POST /api/llm/register/` (`service_name`, `bootstrap_token`) → per-service Django User + стабільний DRF Token; `LLMService.get_or_create(defaults={"base_url": f"http://{REMOTE_ADDR}:8080"})` — base_url ніколи не оновлюється | `llm_services/api_token_views.py` |
| `LLM_BOOTSTRAP_TOKEN` — env Django, дефолт `'change-me-in-production'` | `knowledge_base_api/settings.py:404` |
| Health: `sync_service()` читає `status_data.get("model_path")` — **баг**: llama `/status` повертає `model.path` (вкладено), тож `current_model` завжди `""`, `LLMModel.is_loaded` ніколи не true; далі завжди кличе `/models` | `llm_services/services/health_service.py:33,60`; `system-conf/.../api/routes_status.py` |
| `push_runtime_config_to_nodes()` пушить llama-конфіг **усім** `LLMService` на ноді | `llm_services/selectors.py:88` |
| llama-worker: `TokenManager` per endpoint, 401 → invalidate + 1 retry, `paused_until`, jitter 0-0.5 с, health-check endpoint кожні 15 хв, `RESULT_MODEL_NOT_READY` = не звітувати | `system-conf/common/modules/llm/app/integrations/django/*.py`, `worker/*.py` |
| Секрети: sops-nix доставка `clusterops.Secret` → файл на хості | ADR-055 |

---

## 2. Розглянуті підходи

### 2.1 Як не дати LLM-воркеру забрати speech-задачу (і навпаки)

| Варіант | Суть | Плюси | Мінуси |
|---|---|---|---|
| **A. `?artifact_types=` + `WorkerType.payload_contract` (обрано)** | Новий DB-атрибут `WorkerType.payload_contract ∈ {llm_prompt, structured_json}`. Поллер з `?artifact_types=a,b` отримує **лише** ці типи; поллер без параметра (усі існуючі llama) **ніколи** не бачить WorkItem з `structured_json` worker_type | Нуль змін у `system-conf`; явна декларація "хто що вміє" на боці воркера (overview §4.6); той самий атрибут керує retry-семантикою (§3.3); DB-driven (ADR-029) | Нове поле + міграція |
| B. Через tier (tier=1-подібний ексклюзив, напр. tier 6 "audio") | Розширити `apply_tier_filter` | Мінімум коду | Tier — це обчислювальна складність (ADR-026), не модальність; ще один "спецтир" — костиль, що вже раз накопичився з tier 1 |
| C. Похідне від `LLMService.kind` на сервері | `next()` сам вирішує типи за kind викликача | Воркер нічого не передає | Мапа kind→artifact_types в Django дублює знання воркера; не працює для токенів без LLMService; неявно |

### 2.2 Як передавати аудіо

| Варіант | Обрано? | Чому |
|---|---|---|
| base64 у `prompt` | ні | Заборонено overview; `prompt` рендериться в UI, retry-історію, логи; роздуває БД (`engine_workitem` уже був 410 MB, TD-134) |
| Shared FS / NFS | ні | Немає спільного FS між хостами; ламає модель "сервіс на будь-якому хості" |
| **Blob у Django (новий `speech.AudioBlob`, FileSystemStorage поза static) + авторизований download/upload за відносним шляхом** | **так** | Один власник даних (Django), доступ лише по worker-токену в межах конкретного WorkItem; працює з двома Django-інстансами |
| S3/MinIO | ні (YAGNI) | Немає в інфрі; можна підмінити storage backend пізніше без зміни контракту |

### 2.3 Реєстр сервісів

| Варіант | Обрано? | Чому |
|---|---|---|
| **Узагальнити `LLMService`: поле `kind` + `port` з реєстрації** | **так** | Уся auth-логіка (`LLMServiceTokenAuthentication`, `resolve_calling_service`, `paused`, `is_active`) уже прив'язана до `LLMService`; нова модель = два паралельні реєстри (порушення "один механізм") |
| Нова модель `SpeechService` | ні | Дублювання токенів/auth/health/UI |
| Перейменувати в `ComputeService` зараз | ні (open question §11) | Широкий blast radius (clusterops, workspace, admin, scripts); не потрібно для мети; записати в tech-debt |

### 2.4 Де живе Django-логіка speech

Новий app **`speech`** (bounded context: speech-capabilities + аудіо-blob'и) з власним `EnginePlugin` — за зразком `knowledge` (rerank) та `elinks`. `engine` лишається generic (не імпортує `speech`), `llm_services` — лише реєстр.

---

## 3. Дизайн — Django (swarm-nix/django_api)

### 3.1 WorkerType і claim-scope у `next()`

**Модель** (`engine/models/workitem.py::WorkerType`) — нове поле:

```python
class PayloadContract(models.TextChoices):
    LLM_PROMPT = 'llm_prompt', 'LLM prompt (generic text generation, tier-routed)'
    STRUCTURED_JSON = 'structured_json', 'Structured JSON payload (dedicated worker, artifact_types opt-in)'

payload_contract = models.CharField(max_length=20, choices=PayloadContract.choices,
                                    default=PayloadContract.LLM_PROMPT)
```

Семантика `structured_json` (одне поле → три наслідки, усі явні):
1. **Claim:** такий WorkItem віддається лише поллеру, що явно перелічив його `artifact_type` у `?artifact_types=`.
2. **Retry:** при валідаційній помилці `prompt` **не перезаписується** (`build_retry_prompt` не застосовується — payload не є LLM-інструкцією), `maybe_decompose()` не викликається.
3. **Документування:** `WorkerType.description` пояснює контракт; `dump_workertypes`/`bootstrap_blueprints` переносять поле.

**WorkerType рядки** (`WORKER_TYPE_DEFINITIONS` + міграція `0078`):

| name | provider | tier | payload_contract | is_active | artifact_types |
|---|---|---|---|---|---|
| `audio` (активується) | `workitem` | `null` | `structured_json` | `true` | `asr_transcribe`, `tts_synthesize` |
| `mt` (новий) | `workitem` | `null` | `structured_json` | `true` | `mt_translate` |

`mt` окремо від `audio`, бо MT — текст→текст (правило "ім'я мусить встигати за роллю"). `tier=null` свідомо: tier для structured-задач не бере участі в маршрутизації (`WorkItem.tier` отримає fallback 2, але фільтр нижче виключає їх для tier-поллерів).

**`next()` — нова логіка вибору кандидатів** (`engine/views_work_item.py`), винесена в чисті функції для тестування на `pk__in`-скоупі (конвенція `test_next_tier_matching.py`):

```python
ARTIFACT_TYPES_MAX = 16
_ARTIFACT_TYPE_RE = re.compile(r'^[a-z0-9_]{1,50}$')

def parse_artifact_types(raw: str | None) -> list[str] | None:
    """None якщо параметра немає; ValueError на порожній/некоректний список."""

def apply_claim_scope(queryset, artifact_types: list[str] | None, structured_worker_types):
    """artifact_types задано → filter(artifact_type__in=artifact_types);
    не задано → exclude(worker_type__in=structured_worker_types)."""
```

Потік у `next()`:
1. `paused`-перевірка — без змін.
2. `tier` парситься як зараз; `artifact_types` — `parse_artifact_types()`, некоректний → `400 {"error": ..., "code": "invalid_artifact_types"}`.
3. Базовий queryset (status todo/invalid, exclude internal) — без змін.
4. `qs = apply_claim_scope(base, artifact_types, get_structured_worker_types())`.
5. Якщо `tier` задано → `order_by_tier_preference(apply_tier_filter(qs, tier), tier)` (AND з artifact_types, якщо обидва).
   Якщо `tier` не задано і `artifact_types` не задано → `qs.exclude(worker_type__in=get_lm_studio_worker_types()).order_by('id')` (як зараз).
   Якщо лише `artifact_types` → `qs.order_by('id')`.
6. Решта (orphan-скан, claim, secure_payload) — без змін.

`get_structured_worker_types()` — у `engine/services/providers.py` поруч із `get_lm_studio_worker_types()` (той самий патерн ADR-029, запит до БД).

**Зворотна сумісність llama:** llama не передає `artifact_types` → гілка `exclude(structured)` → жодної зміни в тому, що вони отримують, окрім того, що speech-WorkItem їм ніколи не видаються. Зміни в `system-conf` **не потрібні**.

### 3.2 Payload по WorkItem (wire-контракт Django ↔ worker)

Загальне:
- `WorkItem.prompt` = JSON-об'єкт (UTF-8, `ensure_ascii=False`), `expected_format='json'`, ключ `"v": 1` (версія схеми; воркер відхиляє невідому версію явною помилкою).
- Посилання на blob — **відносний шлях** на тому Django-ендпоінті, з якого воркер забрав задачу. Воркер приєднує його до свого `base_url` і **відмовляє**, якщо шлях не починається з `/api/speech/` або містить схему/хост (захист від витоку токена на чужий хост).
- `raw_result` від воркера = JSON-рядок (його парсить `ValidatorRegistry` у `parsed_result`). Поле `parsed_result` у тілі `complete` воркер не надсилає (Django його ігнорує).
- Помилка воркера: `{"raw_result": "", "status": "error", "error_message": "<клас>: <текст>"}`.
- `next()` не ре-рендерить такий `prompt`: у дитини немає `atom`, `metadata.workflow_id`, `target_file`, `class_name` → `_refresh_prompt_with_context()` лишає його як є (фіксується тестом), `_dependencies_satisfied()` → True.

**`asr_transcribe`** — payload:

```json
{
  "v": 1,
  "audio": {
    "path": "/api/speech/blobs/6f1c…/content/?work_item=9a2e…",
    "content_type": "audio/wav",
    "size_bytes": 1920044,
    "sha256": "ab12…",
    "duration_s": 60.0
  },
  "language": "uk",
  "initial_prompt": null,
  "temperature": 0.0
}
```

результат:

```json
{
  "v": 1,
  "text": "повний текст",
  "language": "uk",
  "duration_s": 60.0,
  "segments": [{"start": 0.0, "end": 2.4, "text": "…"}],
  "model": "large-v3-turbo"
}
```

**`mt_translate`** — payload (текст inline — це не аудіо; `texts` — природні одиниці: сегменти ASR або речення, кожен ≤ `SPEECH_MT_MAX_ITEM_CHARS`, бо NLLB обрізає вхід понад `RT_MT_MAX_LENGTH`=256 токенів):

```json
{"v": 1, "texts": ["речення 1", "речення 2"], "source_lang": "uk", "target_lang": "en", "num_beams": null}
```

результат:

```json
{"v": 1, "translations": ["sentence 1", "sentence 2"], "source_lang": "uk", "target_lang": "en", "model": "nllb-200-distilled-600M-ct2-int8"}
```

**`tts_synthesize`** — payload:

```json
{
  "v": 1,
  "text": "Hello world",
  "lang": "en",
  "voice": null,
  "speed": 1.0,
  "format": "wav",
  "sample_rate": 24000,
  "output": {"upload_path": "/api/speech/blobs/?work_item=9a2e…", "max_bytes": 209715200}
}
```

результат:

```json
{
  "v": 1,
  "audio": {"blob_id": "c0d3…", "content_type": "audio/wav", "size_bytes": 480044, "sha256": "…", "duration_s": 10.0, "sample_rate": 24000},
  "voice": "af_heart",
  "model": "kokoro-82m"
}
```

Мови — ISO 639-1 (overview §4.3). `voice=null` → сервіс обирає дефолт (env `RT_TTS_VOICE`; мовно-залежний дефолт — open question §11).

### 3.3 Зміни в `complete()` для structured-контракту

- `_handle_validation_error(task, …)`: якщо `task.worker_type ∈ get_structured_worker_types()`:
  - зберегти `retry_history` як зараз (prompt + raw_result + error), **не** переписувати `task.prompt`, статус `invalid` (повторно забирається тим самим `/next/` з тим самим payload);
  - на вичерпанні retry — **без** `maybe_decompose()`, одразу `error` + `on_error` + (нове) резолв deferred parent'а як помилки (§3.4).
- `_handle_worker_error` — без змін логіки retry (він і так не чіпає prompt), плюс резолв parent'а на термінальній помилці.

### 3.4 Узагальнення "deferred parent чекає на зовнішній WorkItem"

`engine/services/workitem_factory.py::resolve_deferred_parent_waiting_on(work_item)` змінюється (additive, сумісно з `machine_bootstrap`):

- шукає `in_progress` parent з `metadata.waiting_on_workitem_uuid == str(work_item.uuid)` (як зараз);
- `work_item.status == 'done'` → `finish_deferred_parent(parent, success=True, parsed_result={**child_parsed, 'result_uuid': …, 'raw_result': …})` — **додано** розгортання `parsed_result` дитини (ключі `result_uuid`/`raw_result` лишаються, `machine_bootstrap._get_sibling_result` не ламається);
- `work_item.status == 'error'` → `finish_deferred_parent(parent, success=False, parsed_result={'result_uuid', 'raw_result'}, error=work_item.validation_error)` — **новий** шлях, закриває "parent висить вічно";
- запис статусу — **лише** через `finish_deferred_parent()` (правило 4 `workitem-deferred.md`; зараз функція пише напряму — виправляється по дорозі).

Виклики: success-шлях `complete()` (існує) + термінальні гілки `_handle_worker_error` і `_handle_validation_error` (нові).

### 3.5 Claim-таймаут per WorkItem

`engine_poll._reset_stale()` і `reset_stale_workitems` (без `--timeout`) перестають використовувати глобальні 300 с і порівнюють з **власним** `WorkItem.timeout_seconds` кожного рядка (поле вже існує, default 300 → поведінка для всіх наявних WorkItem не змінюється):

```python
from django.db.models import DateTimeField, DurationField, ExpressionWrapper, F, Value

def overdue_claims(queryset, now):
    """in_progress WorkItems, чий claimed_at + timeout_seconds уже минув (per-row)."""
    allowed = ExpressionWrapper(F('timeout_seconds') * Value(timedelta(seconds=1)),
                                output_field=DurationField())
    deadline = ExpressionWrapper(F('claimed_at') + allowed, output_field=DateTimeField())
    return (queryset.filter(status='in_progress', claimed_at__isnull=False)
            .annotate(_claim_deadline=deadline).filter(_claim_deadline__lt=now))
```

Функція живе в `engine/services/queue_health.py` (одна реалізація для обох команд — зменшує дублювання TD-84).

`reset_stale_workitems --timeout N` (ручний override) лишається глобальним. Speech-хендлери виставляють `timeout_seconds` за обсягом роботи (§3.7).

### 3.6 App `speech`

```
django_api/speech/
  __init__.py
  apps.py                  # SpeechConfig.ready(): PluginRegistry.register(SpeechPlugin()) у try/except
  models.py                # AudioBlob
  storage.py               # speech_storage() → FileSystemStorage(location=settings.SPEECH_STORAGE_ROOT)
  constants.py             # ARTIFACT_*, WORKER_TYPE_*, ALLOWED_INPUT_CONTENT_TYPES, ліміти-дефолти
  services/blobs.py        # store_blob(), open_blob(), blob_download_path(), blob_upload_path(), gc_expired_blobs()
  services/payloads.py     # build_asr_payload/build_mt_payload/build_tts_payload + SpeechInputError
  services/output_validators.py   # validate_asr_result/validate_mt_result/validate_tts_result (за artifact_type)
  services/capability_validators.py  # validate_speech_step (postcondition: _deferred)
  services/capability_input_validators.py  # validate_input_speech_* (лише типи присутніх полів)
  handlers/speech_steps.py # handle_speech_step (один для трьох capability) + handle_speech_gc_blobs
  handlers/engine_acl.py   # work_item_grants_blob(), work_item_accepts_output() — єдине місце, де speech читає engine.WorkItem (зона handlers/ за app-structure rule 2)
  views.py                 # blob_content (GET), blob_upload (POST)
  urls.py                  # app_name='speech'
  plugin.py                # SpeechPlugin — лише мапи {ключ: функція}
  admin.py
  management/commands/speech_submit.py
  migrations/0001_initial.py
  context/README.md
  tests/…
```

**`AudioBlob`:**

| Поле | Тип | Примітка |
|---|---|---|
| `uuid` | UUIDField unique | публічний id (`blob_id`) |
| `file` | FileField(storage=speech_storage, upload_to=`speech/%Y/%m/%d/`) | ім'я файлу = `<uuid>.<ext>` |
| `role` | `input` \| `output` | |
| `content_type` | Char(100) | allow-list для input: `audio/wav`, `audio/x-wav`, `audio/flac`, `audio/ogg`, `audio/mpeg` (те, що декодує `soundfile` у `src/services/common/audio.decode_audio_file`) |
| `size_bytes` | BigInteger | |
| `sha256` | Char(64) | рахується при збереженні |
| `duration_seconds` | Float null | для WAV — stdlib `wave`; інакше null |
| `work_item_uuid` | UUID null | для `output` — WorkItem-продюсер (cross-app → UUID, не FK) |
| `uploaded_by_service_id` | Integer null | pk `LLMService` (cross-app → pk) |
| `created_at` / `expires_at` | DateTime | `expires_at = created_at + SPEECH_BLOB_TTL_DAYS` |

Файли **не** монтуються в `urls.py` як static — віддаються лише через `blob_content` view.

**Settings** (`knowledge_base_api/settings.py`, з env):

| Setting | Env | Default |
|---|---|---|
| `SPEECH_STORAGE_ROOT` | `SPEECH_STORAGE_ROOT` | `BASE_DIR / 'var' / 'speech'` |
| `SPEECH_MAX_INPUT_BYTES` | `SPEECH_MAX_INPUT_BYTES` | `52428800` (50 MB) |
| `SPEECH_MAX_OUTPUT_BYTES` | `SPEECH_MAX_OUTPUT_BYTES` | `209715200` (200 MB) |
| `SPEECH_BLOB_TTL_DAYS` | `SPEECH_BLOB_TTL_DAYS` | `7` |
| `SPEECH_MT_MAX_CHARS` | `SPEECH_MT_MAX_CHARS` | `20000` (сума по WorkItem) |
| `SPEECH_MT_MAX_ITEM_CHARS` | `SPEECH_MT_MAX_ITEM_CHARS` | `1000` (один елемент `texts`; < `RT_MT_MAX_CHARS`=2000 сервісу) |
| `SPEECH_TTS_MAX_CHARS` | `SPEECH_TTS_MAX_CHARS` | `4000` (< `RT_TTS_MAX_CHARS`=4096 сервісу) |
| `SPEECH_ASR_DEFAULT_TIMEOUT` | `SPEECH_ASR_DEFAULT_TIMEOUT` | `1800` |

**Views (`/api/speech/`):**

| Метод/шлях | Auth | Правила |
|---|---|---|
| `GET blobs/<uuid>/content/?work_item=<wi_uuid>` | `LLMServiceTokenAuthentication` (воркер) **або** session staff | Воркер: `resolve_calling_service()` не None, WorkItem `wi_uuid` існує, `status='in_progress'`, `str(blob.uuid) ∈ wi.metadata['speech_blob_ids']`. Staff — без `work_item`. Відповідь — `FileResponse` з `Content-Type`, `Content-Length`, `X-Content-SHA256`. 403/404 явні. |
| `POST blobs/?work_item=<wi_uuid>` (multipart `file`) | лише воркер | WorkItem `in_progress`, `artifact_type == 'tts_synthesize'`; розмір ≤ `SPEECH_MAX_OUTPUT_BYTES` (інакше 413); `content_type == audio/wav`; створює `AudioBlob(role=output, work_item_uuid, uploaded_by_service_id)`; `201 {"blob_id","size_bytes","sha256","content_type"}` |

Помилки — формат `{"error", "code", "details"}` (python-style rule 5).

**Capabilities (step targets) ≠ artifact_types — свідомо:**

| Capability (`WorkflowStep.target`) | WorkItem `artifact_type` | worker_type |
|---|---|---|
| `speech_transcribe` | `asr_transcribe` | `audio` |
| `speech_translate` | `mt_translate` | `mt` |
| `speech_synthesize` | `tts_synthesize` | `audio` |
| `speech_gc_blobs` (internal, без воркера) | — | `internal` |

Причина розділення: `complete()` ганяє `validate_capability_input(task.artifact_type, metadata+parsed_result)` і `get_output_validator(task.artifact_type)`. Якщо capability == artifact_type, вхідні валідатори кроку (чекають `audio_blob_id`) запускались би на **результаті** зовнішнього WorkItem. Плюс deferred parent (artifact_type = capability) і зовнішня дитина (artifact_type = wire-тип) не конфліктують у `_build_condition_context` / `field_mapping`.

**`handle_speech_step(step, session_id, workflow_id, parent_task)`** — один handler, реєстр `SPEECH_JOBS = {capability: JobSpec(artifact_type, worker_type, build_payload)}` (PPP: варіант = запис у реєстрі):

1. Зібрати вхід як `SpeechInputs(run_context, condition, parent_result)` — три окремі dict: `AnalysisRun.context` (вхід запуску), `step.condition`, `parent_task.parsed_result` (результат попереднього кроку; для першого кроку `parent_task` = None → `{}`). Для більшості полів діє загальне злиття `merged = {**run_context, **condition, **parent_result}` (пізніший перекриває); поля з особливим порядком (TTS `text`) builder резолвить явно за таблицею нижче. `step.condition.field_mapping` (якщо є) резолвиться через `engine.services.capability_input.resolve_manual_field_mapping()` і перекриває `merged`.
2. `spec.build_payload(inputs, work_item_uuid)` → `(payload, child_metadata, timeout_seconds)` або `SpeechInputError` (явний текст; handler її **не ковтає** — виняток → `call_handler` повертає `ok=False` → `validate_speech_step` бачить відсутність `_deferred` → WorkItem `error` + `on_error`).
3. В одному `transaction.atomic()`: створити дитину `create_workitem(worker_type, artifact_type, prompt=json.dumps(payload), expected_format='json', max_retries=2, timeout_seconds, priority=condition.priority|5, metadata={'is_speech_job': True, 'speech_blob_ids': [...], 'speech_capability': step.target})` — **без** `session_id`/`workflow_id`/`blueprint_name` (інакше `WorkflowExecutor.on_complete` просуне DAG двічі); потім `create_deferred_parent_workitem(worker_type='internal', artifact_type=step.target, prompt='(<capability> — waiting for <artifact_type> worker)', expected_format='json', session_id, workflow_id, blueprint_name=step.workflow.name, step_type, order, metadata={'is_speech_step': True, 'waiting_on_workitem_uuid': str(child.uuid)})`. Атомарність гарантує, що воркер не забере дитину до появи parent'а.
4. Повернути `{'_deferred': True, 'parent_work_item_id': parent.pk}`.

Для TTS `work_item_uuid` дитини потрібен у payload (`output.upload_path`) до її створення → `create_workitem` створює з `prompt=''`, потім `prompt` оновлюється в тій самій транзакції (`save(update_fields=['prompt'])`). Для ASR те саме (`audio.path` містить `?work_item=`).

**Payload builders — правила вибору входу:**

| Capability | Вхід | Звідки (перший знайдений) | Таймаут |
|---|---|---|---|
| `speech_transcribe` | `audio_blob_id` (обов'язково), `language?`, `initial_prompt?` | merged | `clamp(ceil(duration_s*1.5)+120, 300, 3600)`, якщо тривалість відома; інакше `SPEECH_ASR_DEFAULT_TIMEOUT` |
| `speech_translate` | `texts` = `merged.texts` → тексти `parent.segments` (непорожні) → `split_sentences(merged.text)`; кожен елемент > `SPEECH_MT_MAX_ITEM_CHARS` дорізається по пробілах; `source_lang` = `merged.source_lang` → `merged.language` (детектована ASR); `target_lang` обов'язково | merged | `300` |
| `speech_synthesize` | `text` = `condition.text` → `" ".join(parent.translations)` → `parent.text` → `run_context.text`; `lang` = `merged.target_lang` → `merged.lang` → `merged.language`; `voice?`, `speed?` | див. колонку | `clamp(len(text)//10+120, 300, 1800)` |

Порушення лімітів (`SPEECH_MT_MAX_CHARS`, `SPEECH_TTS_MAX_CHARS`, input blob > `SPEECH_MAX_INPUT_BYTES`, blob `expired`/відсутній, `content_type` не з allow-list) → `SpeechInputError` (явна помилка; без тихого обрізання).

**Output validators** (`SpeechPlugin.get_output_validators()`, ключ = artifact_type; лише форма результату + для TTS — належність blob'а):
- `asr_transcribe`: `v==1`, `text: str`, `language: str | None`, `segments: list`.
- `mt_translate`: `v==1`, `translations: list[str]`, довжина == кількість `texts` у payload (`json.loads(task.prompt)`).
- `tts_synthesize`: `v==1`, `audio.blob_id` існує, `role=output`, `work_item_uuid == task.uuid`, `size_bytes` збігається.

**Workflow blueprints** (`engine/blueprints/workflows/speech.yaml`):

```yaml
workflows:
- name: speech_transcribe_audio
  description: 'Batch ASR: AnalysisRun.context.audio_blob_id → rt-asr → {text, language, segments}'
  version: '1.0'
  workflow_type: generation
  workflow_kind: execution
  is_active: true
  steps:
  - target: speech_transcribe
    step_type: transform
    order: 1
    depends_on: []
    condition: {format: json, priority: 5, worker_type: internal, execution_mode: internal}
- name: speech_translate_audio
  description: 'ASR → MT → TTS: аудіо однією мовою → озвучений переклад (AudioBlob role=output)'
  version: '1.0'
  workflow_type: generation
  workflow_kind: execution
  is_active: true
  steps:
  - {target: speech_transcribe, step_type: transform, order: 1, depends_on: [], condition: {format: json, priority: 5, worker_type: internal, execution_mode: internal}}
  - {target: speech_translate, step_type: transform, order: 2, depends_on: [speech_transcribe], condition: {format: json, priority: 5, worker_type: internal, execution_mode: internal}}
  - {target: speech_synthesize, step_type: transform, order: 3, depends_on: [speech_translate], condition: {format: json, priority: 5, worker_type: internal, execution_mode: internal}}
- name: __speech_blob_gc__
  description: 'Видалити AudioBlob з expires_at < now (файл + рядок)'
  version: '1.0'
  workflow_type: analysis
  workflow_kind: meta
  is_active: true
  steps:
  - {target: speech_gc_blobs, step_type: transform, order: 1, depends_on: [], condition: {format: json, priority: 3, worker_type: internal, execution_mode: internal}}
```

Імена execution-workflow — без `__…__`, бо `Workflow.save()` примусово робить такі `workflow_kind=meta` (`engine/models/workflow.py:120`), а ці — звичайні execution-пайплайни (як `communication_translate`); `__speech_blob_gc__` — справді meta (обслуговування).

`meta_schedules.yaml` += `speech_blob_gc_daily` (`blueprint: __speech_blob_gc__`, `scope_type: self_scanning`, `interval_hours: 24`) — прибирання сховища видиме як `MetaSchedule`, не прихований cron.

Будь-який інший workflow (у т.ч. згенерований системою) може використати `speech_*` capabilities як звичайні кроки; `field_mapping` (`{text: 'speech_transcribe.text'}`) працює, бо результат дитини розгортається в parsed_result parent'а (§3.4).

**Запуск (imperative, user-initiated — дозволено `spawn_analysis_run`):** `manage.py speech_submit <file> [--blueprint speech_transcribe_audio] [--language uk] [--target-lang en] [--voice …]` → `store_blob(role=input)` → `spawn_analysis_run(blueprint, context={'audio_blob_id', 'language', 'target_lang', 'voice'})` → друкує `AnalysisRun.pk` і blob uuid. Ручний UI/API-тригер — ui-tasks.md.

### 3.7 Таймаути й ліміти — зведення

| Що | Значення | Де задається |
|---|---|---|
| Claim-таймаут WorkItem | per-row `timeout_seconds` (§3.6 таблиця) | speech handler → `engine_poll._reset_stale` (§3.5) |
| `max_retries` speech WorkItem | 2 | handler передає явно (`create_workitem(max_retries=2)`), тож `RetryPolicy` до них не застосовується — свідомо: детерміновані помилки (поганий blob) не варто повторювати багато разів |
| Input blob | ≤ 50 MB, allow-list content types | `store_blob` + builder |
| Output blob | ≤ 200 MB | upload view (413) |
| MT текст | ≤ 20 000 символів на WorkItem, ≤ 1 000 на елемент | builder (+ worker перевіряє ≤ `RT_MT_MAX_CHARS`) |
| TTS текст | ≤ 4 000 символів на WorkItem | builder (+ worker перевіряє ≤ `RT_TTS_MAX_CHARS`) |
| HTTP від воркера | connect 5 с; API read 30 с; blob read/write 120 с | worker config (§5.2) |
| Blob TTL | 7 днів | `__speech_blob_gc__` |

Довші аудіо/тексти — **явна помилка** у v1; чанкінг через `fan_out` — open question §11.

### 3.8 Реєстр сервісів (`llm_services`)

**Модель:** `LLMService.kind = CharField(max_length=10, choices=[('llm','LLM (llama)'),('asr','ASR'),('mt','MT'),('tts','TTS')], default='llm', db_index=True)` + міграція `0007_llmservice_kind` (існуючі рядки → `llm`).

**Реєстрація** (`api_token_views.register_service`) — нові **опційні** поля тіла:

| Поле | Тип | Поведінка |
|---|---|---|
| `kind` | str ∈ choices | невалідний → 400 `invalid_kind`; при створенні записується; у наявного запису з іншим kind → **409** `kind_conflict` (без тихої зміни) |
| `port` | int 1..65535 | якщо передано → `base_url = f"http://{remote_ip}:{port}"` записується **і при створенні, і при кожній повторній реєстрації** (сервіс міг переїхати); не передано → поточна поведінка (llama: `:8080` лише при створенні) |

`remote_ip` — як зараз (`HTTP_X_REAL_IP` або `REMOTE_ADDR`), винесено в helper. Відповідь — без змін (+ `kind`). Токен — per-service DRF Token (як зараз) → per-service access token ✓.

**Health sync** (`health_service.sync_service`) — диспетчер за kind (реєстр-словник, python-style rule 1):

```python
_STATUS_SYNCERS = {'llm': _sync_llama_status, 'asr': _sync_speech_status,
                   'mt': _sync_speech_status, 'tts': _sync_speech_status}
```

- `_sync_llama_status`: **фікс бага** — `current_model = (status_data.get('model') or {}).get('path') or ''`; `loaded_name` — basename від того ж; `/models` — як зараз; tier-drift — як зараз.
- `_sync_speech_status`: лише `GET /status` (overview §4.3 + SP1 D3 + блок `worker`, §5.5); `/models` **не** викликається; `current_model = model.id`; `status` (перший збіг): `model.state == 'failed'` → `error` (`error_message = model.error`); `worker.enabled == false` → `disabled`; `ready == false` → `loading`; інакше `online`; `reported_tier` не чіпається.
- Невідомий kind → `ValueError` (явно, не тихий дефолт).

**Інші місця:** `push_runtime_config_to_nodes()` → `.filter(kind='llm')` (llama-конфіг не має їхати в rt-сервіси); admin `list_display/list_filter += kind`; `/workers/` UI — бейдж kind (ui-tasks.md, не в цьому підпроєкті).

### 3.9 Безпека

- **Bootstrap-токен** на боці сервісу: тільки `RT_DJANGO_BOOTSTRAP_TOKEN_FILE` (файл, доставлений sops-nix за ADR-055 — інфра-частина в підпроєкті 3). Env-змінної з самим токеном **немає**; файл перечитується при кожній (ре)реєстрації (ротація без рестарту); відсутній/порожній файл при `RT_WORKER_ENABLED=true` → воркер не стартує, `/status.worker.error` пояснює причину.
- **Access token** — per-service DRF Token (окремий Django User на `RT_SERVICE_NAME`); тримається лише в пам'яті процесу.
- **Least privilege на blob'и:** download лише в межах `in_progress` WorkItem, що посилається на blob; upload лише для `in_progress` `tts_synthesize`; воркер відмовляє абсолютним URL і шляхам поза `/api/speech/`.
- Мережа — Tailscale (overview §4.5); `http://` усередині tailnet, як у llama.
- Django-бік `LLM_BOOTSTRAP_TOKEN` (env з небезпечним дефолтом) — не змінюється тут; open question §11.

---

## 4. Потік даних (ASR→MT→TTS)

```
speech_submit file.wav ──► AudioBlob(input) ──► spawn_analysis_run(speech_translate_audio, ctx)
  step speech_transcribe (internal handler)
     └─ atomic: child WI(audio/asr_transcribe, prompt=JSON{audio.path}) + deferred parent(waiting_on=child)
rt-asr worker: GET /next/?artifact_types=asr_transcribe ─► GET /api/speech/blobs/<b>/content/?work_item=<wi>
     └─ backend.transcribe() ─► POST /complete/ {raw_result: JSON{text,…}}
Django complete(): validate json + output validator ─► done ─► resolve_deferred_parent_waiting_on
     └─ finish_deferred_parent(parent, parsed_result={text, language, …}) ─► WorkflowExecutor.on_complete
  step speech_translate: inputs = run ctx + condition + parent.parsed_result → child WI(mt/mt_translate)
rt-mt worker: /next/?artifact_types=mt_translate ─► backend.translate() ─► complete
  step speech_synthesize → child WI(audio/tts_synthesize, output.upload_path)
rt-tts worker: /next/?artifact_types=tts_synthesize ─► backend.synthesize() ─► POST /api/speech/blobs/?work_item=… ─► complete {audio.blob_id}
Django: output validator (blob належить WI) ─► parent done ─► workflow done
```

Помилка на будь-якому кроці: дитина `error` після `max_retries` → parent `error` (`finish_deferred_parent(success=False)`) → `WorkflowExecutor.on_error` → `workitem_failed` / MetaTrigger (наявний self-healing шлях).

---

## 5. Дизайн — сервісний бік (цей репо)

### 5.1 Модулі

Підпроєкт 1 ([`2026-10-06-speech-services-api-design.md`](2026-10-06-speech-services-api-design.md) §4.1, §9) резервує
імпорт-шлях `src/services/common/django_worker` і точку підключення `lifespan_hooks`. Тут це **пакет** (той самий
імпорт-шлях), бо одна відповідальність на файл:

```
src/services/common/django_worker/
  __init__.py      # публічне API: WorkerConfig, JobContext, JobError, build_django_worker_hooks
  errors.py        # WorkerError, TransportError, AuthError, JobError
  config.py        # WorkerConfig.from_env(service, environ) + WorkerConfigError
  auth.py          # TokenManager (per endpoint, threading.Lock, register з bootstrap-файлу)
  client.py        # DjangoEndpointClient: next/complete/fetch_blob/upload_blob, 401→refresh→1 retry, paused, backoff
  jobs.py          # BlobRef/UploadSpec (pydantic), JobContext, JobError, run_job()
  loop.py          # WorkerLoop.run_once()/run(stop_event), WorkerStatus
  hooks.py         # build_django_worker_hooks(service, handlers_factory, environ) -> list[lifespan hook]
src/services/asr/django_job.py   # make_asr_handlers(runtime, settings) -> {"asr_transcribe": callable}
src/services/mt/django_job.py    # make_mt_handlers(runtime, settings) -> {"mt_translate": callable}
src/services/tts/django_job.py   # make_tts_handlers(runtime, settings) -> {"tts_synthesize": callable}
src/services/{asr,mt,tts}/__main__.py   # змінюється: передає build_django_worker_hooks(...) у create_app(lifespan_hooks=…)
src/services/common/runtime.py, health.py # змінюється (мінімально): runtime.status_extras → злиття в /status
tests/services/django_worker/{conftest.py (FakeDjango, FakeRuntime), test_config.py, test_auth.py, test_client.py, test_jobs.py,
  test_loop.py, test_hooks.py, test_asr_job.py, test_mt_job.py, test_tts_job.py, test_e2e_fake_django.py}
```

Кожен файл — одна відповідальність; `django_worker` нічого не знає про ASR/MT/TTS (handler'и передаються мапою
`{artifact_type: callable(payload: dict, ctx: JobContext) -> dict}`).

### 5.2 Конфіг (env)

| Env | Default | Значення |
|---|---|---|
| `RT_WORKER_ENABLED` | `false` | `true` вмикає воркер |
| `RT_DJANGO_ENDPOINTS` | `""` | comma-sep base URL (`http://cyborg:8000,http://sktop-note27:8000`) |
| `RT_DJANGO_BOOTSTRAP_TOKEN_FILE` | — | шлях до файлу з bootstrap-токеном (обов'язковий, якщо enabled) |
| `RT_SERVICE_NAME` | `<hostname>-rt-<svc>` | ім'я в `LLMService.name` / Django User |
| `RT_WORKER_POLL_INTERVAL` | `10` | сек між порожніми опитуваннями (+ jitter 0–0.5 с) |
| `RT_WORKER_MAX_DOWNLOAD_BYTES` | `104857600` | жорстка межа на download незалежно від payload |
| `RT_PORT` | з overview §4.1 | відправляється як `port` при реєстрації |

Доставка на хостах (v1): ці змінні задаються через `services.rtSpeech.<svc>.extraEnv` NixOS-модуля SP2 (окремих опцій модуля немає; `envLines` рендерить `extraEnv` і в deploy-rs `env`). Bootstrap-токен — sops-файл, читабельний system-user'ом сервісу (`file_owner = "rt-<svc>"`, `0400`; по файлу на сервіс), бо `LoadCredential` модуля SP2 покриває лише `apiTokenFile`.

Валідація при `enabled=true`: порожні endpoints / відсутній файл токена → `WorkerConfigError` з поясненням; сервіс **працює далі** (HTTP API не залежить від воркера), `/status.worker = {"enabled": true, "running": false, "error": "…"}`.

### 5.3 Цикл воркера

```
run(stop_event):
  while not stop_event.is_set():
     outcome = run_once()
     if outcome is IDLE: stop_event.wait(poll_interval + jitter)

run_once():
  if not can_accept(): return IDLE          # backend.ready і (ASR) немає активних realtime-сесій — хук
  for client in rotated(clients):            # ротація стартового індексу — справедливість між Django
     if client.blocked(): continue           # paused_until / backoff_until
     try: task = client.next(artifact_types)
     except TransportError: client.backoff(); continue    # 5→10→…→300 с, скидання на успіх
     if task is None: continue
     result = run_job(task, handlers, JobContext(client, task['uuid']))
     client.complete(task['uuid'], result, attempts=3)     # backoff 1,2,4 с між спробами
     return WORKED
  return IDLE
```

`run_job`: невідомий `artifact_type` → error-result; `json.loads(prompt)` / pydantic-валідація payload / `v != 1` → error-result; `JobError` (очікувана, напр. sha256 mismatch) і будь-який інший виняток → error-result з `"<ExcClass>: <msg>"`; успіх → `{"raw_result": json.dumps(out, ensure_ascii=False), "status": "done", "error_message": "", "generation_seconds": t}`.

Потік: окремий daemon `threading.Thread` (як llama) зі `stop_event`; старт/стоп — через `lifespan_hooks` підпроєкту 1.
Інференс **не** виконується в потоці воркера напряму: handler'и викликають `runtime.submit_threadsafe(fn, …, priority=Priority.REQUEST).result()`
— та сама inference-черга, що й HTTP (серіалізація GPU, детермінований VRAM-пік).

`can_accept()` = `runtime.state == ModelState.READY and runtime.worker.pending == 0` — воркер бере batch-задачу лише
коли inference-черга порожня. Це не преемпція (довгий batch-job, що вже виконується, блокує realtime-фінали — SP1 Q5),
тому для важкого batch рекомендовано окремий інстанс `rt-asr` з `RT_WORKER_ENABLED=true`, а на realtime-інстансі воркер вимкнений.

### 5.4 Клієнт і токени

- `TokenManager.register()` → `POST {base}/api/llm/register/` `{"service_name", "bootstrap_token": <з файлу>, "kind": "asr|mt|tts", "port": RT_PORT}` → кеш `access_token` до `expires_in - 60`.
- Кожен запит: `Authorization: Bearer <access_token>`; 401 → `invalidate()` → одна повторна спроба з новим токеном; знову 401 → `AuthError` (endpoint у backoff).
- `next()`: `GET {base}/api/engine/work-items/next/?artifact_types=<csv>`; 204 → None; 200 `{"paused": true}` → `paused_until = now + retry_after`; 200 task → dict; інше → `TransportError`.
- `complete()`: `POST {base}/api/engine/work-items/{uuid}/complete/`; 2xx або 422 (Django зафіксував валідаційну помилку) → доставлено; інші 4xx (напр. 400 "Task is not in progress" — інший воркер уже завершив після стейл-ресету) → лог warning, без повторів; 5xx/мережа → до 3 спроб з паузами 1, 2 с.
- `fetch_blob(ref)`: перевірка шляху (§3.2), stream download з лічильником ≤ `min(ref.size_bytes, RT_WORKER_MAX_DOWNLOAD_BYTES)`, sha256 == `ref.sha256` інакше `JobError`.
- `upload_blob(spec, data, content_type)`: `len(data) ≤ spec.max_bytes` інакше `JobError`; multipart POST; 201 → dict.

### 5.5 `/status` — блок воркера

До `/status` (overview §4.3) додається (additive):

```json
"worker": {
  "enabled": true,
  "running": true,
  "error": null,
  "artifact_types": ["asr_transcribe"],
  "endpoints": [{"url": "http://cyborg:8000", "registered": true, "paused_for_s": null, "backoff_for_s": null, "last_error": null}],
  "jobs": {"done": 12, "failed": 1, "last_job_at": "2026-10-06T12:00:00Z"}
}
```

Django `_sync_speech_status` читає лише `worker.enabled`, `ready`, `model.id`.

### 5.6 Інтерфейси підпроєкту 1, на які спираються адаптери

| Що | Сигнатура (SP1 spec §4.3, §4.7, §9) | Хто використовує |
|---|---|---|
| Runtime | `ServiceRuntime.state: ModelState`, `.backend`, `.worker.pending`, `.submit_threadsafe(fn, *args, priority) -> Future` | `can_accept`, усі handler'и |
| Lifespan | `create_app(settings=None, backend_factory=None, …, lifespan_hooks=())`; hook: `Callable[[ServiceRuntime], Callable[[], None]]` | `hooks.py`, `<svc>/__main__.py` |
| Аудіо | `src/services/common/audio.decode_audio_file(data: bytes) -> np.ndarray` (float32, 16 kHz, mono); `resample(...)` | ASR, TTS |
| ASR | `AsrBackend.transcribe(audio: np.ndarray, opts: DecodeOptions) -> Transcription(text, language, duration, segments[Segment(start,end,text)])`; `DecodeOptions(language, prompt, temperature, beam_size, vad_filter, without_timestamps)` | `asr/django_job.py` |
| MT | `MtBackend.translate(texts, src, tgt, num_beams) -> list[str]` (NLLB-коди); ISO 639-1 → NLLB — `src/services/mt/languages.py` | `mt/django_job.py` |
| TTS | `TtsBackend.synthesize(text, voice, speed) -> Iterator[np.ndarray]`, `.sample_rate`, `.voices() -> list[Voice(id, language, gender)]`; `UnsupportedVoiceLanguage`; WAV — `src/services/tts/encoding.wav_bytes(...)` | `tts/django_job.py` |
| Settings | `AsrSettings.asr_beam_size/asr_vad`, `MtSettings.mt_num_beams/mt_max_chars/mt_max_batch`, `TtsSettings.tts_voice/tts_speed/tts_sample_rate/tts_max_chars` (імена полів — звірити при реалізації, env-імена зафіксовані SP1 §4.5) | handler'и |

Вибір голосу TTS (вирішує open question «мовний дефолт»): `payload.voice` → якщо `null`, перший голос з
`backend.voices()`, чия `language` починається з `payload.lang` (`en` → `en-us`), якщо `settings.tts_voice` не тієї мови;
інакше `settings.tts_voice`. Немає жодного голосу мови → `JobError("no voice for lang …")`.

Мінімальна зміна SP1-коду в цьому підпроєкті: `ServiceRuntime.status_extras: dict[str, Callable[[], dict]]` і злиття
його в `/status` у `common/health.py` (так `/status.worker` з'являється без знання health-модуля про воркер).

---|---|---|
| ASR | `backend.transcribe(audio: bytes, *, language: str \| None, initial_prompt: str \| None, temperature: float, word_timestamps: bool) -> dict(text, language, language_probability?, duration, segments[{start,end,text}])` | `src/services/asr/django_job.py` |
| MT | `backend.translate(texts: list[str], *, source_lang: str, target_lang: str, num_beams: int \| None) -> list[str]` | `src/services/mt/django_job.py` |
| TTS | `backend.synthesize(text: str, *, lang: str, voice: str \| None, speed: float, sample_rate: int) -> tuple[bytes(wav), str(voice_used)]` | `src/services/tts/django_job.py` |
| усі | `backend.ready: bool`, `backend.model_id: str`; потокобезпечні (внутрішній lock) | `can_accept`, результат `model` |
| app | `create_app(...)` з lifespan-хуком, куди додається `start_django_worker(...)`/`stop` | `src/services/<svc>/app.py` |

Якщо фактичні імена в підпроєкті 1 інші — змінюються лише три `django_job.py` і точки підключення в `app.py`; контракт Django↔worker не змінюється.

---

## 6. Обробка помилок (зведення)

| Ситуація | Поведінка |
|---|---|
| Сервіс не готовий (модель вантажиться) | `can_accept()=False` → не поллить (задачу не забирає) |
| Django недоступний | per-endpoint backoff до 300 с; інші endpoints працюють |
| `paused` | не поллить до `paused_until` |
| Bootstrap-файл відсутній/невірний токен (403) | endpoint у backoff; `/status.worker.endpoints[].last_error`; ERROR-лог |
| Некоректний payload / невідома версія | `status=error` → Django retry (до 2) → дитина `error` → parent `error` → `on_error` |
| Blob 403/404/sha256 mismatch | те саме (помилка явна в `validation_error`) |
| Backend exception | те саме |
| Валідація результату на Django | `invalid`, prompt **не** переписується, повтор; вичерпано → `error` без decompose |
| `complete` не дійшов (мережа) | 3 спроби; далі WorkItem лишається `in_progress` → per-row стейл-ресет → повторне виконання |
| Подвійне виконання після стейл-ресету | другий `complete` → 400 "not in progress" (лог), результат першого лишається |
| Дитина `error` | parent `error` (§3.4) — нічого не висить |

---

## 7. Тестування

**Django (pytest, nix dev shell, ізольована `knowledge_base_test`; collision-proof значення, `pk__in`-скоуп для `next()`-фільтрів):**
- `engine/tests/test_next_artifact_types.py` — `parse_artifact_types` (None/валідні/порожні/regex/ліміт 16); `apply_claim_scope` (opt-in бачить лише свої типи; без параметра structured виключені, llm_prompt лишаються); HTTP-рівень: 400 на некоректний параметр.
- `engine/tests/test_structured_validation_retry.py` — prompt не переписується, `maybe_decompose` не викликається (mock), вичерпання → error.
- `engine/tests/test_deferred_waiting_on.py` — success розгортає child `parsed_result` і зберігає `result_uuid/raw_result`; error-шлях → parent `error`, `on_error` викликано; повторний виклик no-op.
- `engine/tests/test_reset_stale_per_item_timeout.py` — рядок з `timeout_seconds=3600` не скидається через 400 с, рядок з 300 — скидається.
- `engine/tests/test_workertype_payload_contract.py` — сідінг/`get_structured_worker_types()`.
- `llm_services/tests/test_api_token_views.py` (+ кейси kind/port/409/400).
- `llm_services/tests/test_health_service.py` — llama `model.path` (фікс), speech-kind без `/models`, `loading`/`disabled`/`online`, невідомий kind → помилка; існуючі тести з `model_path` оновлюються під реальну форму llama `/status`.
- `llm_services/tests/test_selectors.py` — `push_runtime_config_to_nodes` пропускає не-llm.
- `speech/tests/` — `test_models.py` (sha256, duration WAV, expires_at), `test_blob_views.py` (download scope 200/403/404, upload 201/403/413), `test_payloads.py` (кожен builder, ліміти, вибір входу), `test_speech_step_handler.py` (атомарне створення child+parent, відсутність session-ключів у дитини, `_deferred`), `test_output_validators.py`, `test_blueprints.py` (`bootstrap_blueprints` створює 3 workflow + schedule), `test_speech_pipeline_e2e.py` — повний ланцюг ASR→MT→TTS через Django test `Client` у ролі воркера (реальні `/next/`, `/complete/`, blob download/upload).

**Сервіси (pytest у `nix develop`):**
- `httpx.MockTransport` + `FakeDjango` (in-memory черга WorkItem, register/next/complete/blobs, 401-сценарії, paused) — без нової залежності `respx`.
- unit: config (дефолти, помилки), auth (register, кеш, refresh на 401, перечитування файлу), client (next/complete/fetch/upload, абсолютний URL відхилено, sha256 mismatch, ліміт байтів), loop (`can_accept`, ротація endpoints, backoff, paused, unknown artifact_type, exception→error-result, повтори `complete`).
- per-service: fake backend → правильний payload→виклик→результат; TTS upload.
- `test_e2e_fake_django.py`: три воркери (ASR/MT/TTS з fake backend) проганяють ланцюжок через `FakeDjango`, що імітує Django-логіку deferred-parent'а на рівні контракту.

**Живий e2e (мануальний, після деплою підпроєктів 1-3):** чекліст у плані (Task D-1).

---

## 8. Документація (Documentation Update Rule swarm-nix)

- Новий ADR `context/docs/adr/ADR-057-speech-services-workitem-queue.md` (номер — наступний вільний на момент реалізації): payload_contract, artifact_types opt-in, blob-by-reference, LLMService.kind.
- `context/docs/reference/tier.md` — рядок про `?artifact_types=` (structured worker types поза tier-маршрутизацією).
- Новий `context/docs/reference/speech.md` — settings/env (§3.6, §5.2), де виставляється → для чого.
- `speech/context/README.md`, `llm_services/context/README.md` (kind, port, health-фікс).
- `context/tasks.md` — задача + open questions як backlog; `context/ui-tasks.md` — kind-бейдж, UI запуску speech-workflow; `context/technical-debt.md` — TD: перейменування `LLMService`→`ComputeService`; міграція rerank/cli_executor на `structured_json`.
- `AGENTS.md` (django_api) — рядок `speech` у таблиці apps, `/api/speech/` у URL namespaces, `speech_submit` у Management Commands.

---

## 9. Поза scope

Realtime edge-шлях (WS), Nix-пакування/NixOS-модулі, деплой і доставка секретів (sops/colmena keys), зміни в `system-conf` llama-воркері, UI в `workspace`, чанкінг довгих аудіо, S3.

---

## 10. Deviations from overview

| # | Overview | Тут | Чому |
|---|---|---|---|
| D1 | §4.3 `/status` без блоку воркера | + `worker{enabled, running, error, artifact_types, endpoints[], jobs}` (additive) | Django health-sync має розрізняти `disabled` (як у llama `worker.enabled`) |
| D2 | §4.2 Django-env: 4 змінні | + `RT_WORKER_POLL_INTERVAL`, `RT_WORKER_MAX_DOWNLOAD_BYTES`; `RT_PORT` відправляється при реєстрації | Тюнінг і безпечна межа download |
| D3 | §4.6 "URL на Django media" | **відносний шлях** на endpoint'і, звідки взято задачу, з `?work_item=` | Два Django-інстанси; Django не знає власного зовнішнього URL (`DJANGO_SELF_URL`-проблема); захист токена |
| D4 | §2 "`WorkerType audio`" | `audio` (ASR+TTS) + новий `mt` (MT) | MT — не аудіо-модальність |
| D5 | §4.6 artifact_types як capabilities | capabilities `speech_transcribe/translate/synthesize` ≠ artifact_types | Розведення input-валідаторів кроку і output-валідаторів результату (§3.6) |
| D6 | §4.2 `RT_SERVICE_NAME` без дефолту | дефолт `<hostname>-rt-<svc>` | `LLMService.name` unique; кілька сервісів на хості |
| D7 | — (не згадано) | реєстрація: опційні `kind`, `port` | Узагальнення реєстру замість нової моделі |
| D8 | SP1 §9: `src/services/common/django_worker.py` (файл) | пакет `src/services/common/django_worker/` (той самий імпорт-шлях) | Одна відповідальність на файл; імпорт-шлях SP1 збережено |
| D9 | SP1 `/status` формується лише `health.py` | `ServiceRuntime.status_extras` + злиття в `/status` (мінімальна additive-зміна SP1-коду) | Блок `worker` без зв'язування health-модуля з воркером |

---

## 11. Open questions

1. **Перейменування `LLMService` → `ComputeService`/`WorkerService`.** Зараз узагальнено in-place (`kind`). Рішення власника: чи робити rename (широкий blast radius) — записати як TD.
2. **Міграція rerank / cli_executor на `payload_contract=structured_json` + `?artifact_types=`.** Прибрала б "tier 1 ексклюзивний" спецвипадок (правило "один механізм"), але потребує зміни llama-воркера в `system-conf`. Рекомендую окрему задачу після живого e2e цього підпроєкту.
3. **Серверна перевірка `artifact_types` за `LLMService.kind`** (asr-сервіс не може попросити `mt_translate`). У v1 не робиться (немає реальної загрози в tailnet, була б дублююча мапа) — підтвердити.
4. **Чанкінг довгих аудіо** (> 50 MB / > ~1 год) через `fan_out` + злиття сегментів — потрібен лише якщо з'явиться реальний споживач.
5. **Пріоритет realtime vs batch на одному GPU:** v1 — `can_accept()` = модель READY і inference-черга порожня (§5.3); преемпції немає (SP1 Q5). Підтвердити рекомендацію «окремий batch-інстанс `rt-asr`» або вимогу чанкувати batch-аудіо з поступкою пріоритету.
6. **Django-бік bootstrap-токена з файлу** (`LLM_BOOTSTRAP_TOKEN_FILE` замість env з дефолтом `change-me-in-production`) — належить інфра-підпроєкту 3 / ADR-055; тут не змінюється.
7. **Мовно-залежний вибір голосу TTS** вирішено в адаптері (§5.6: перший голос мови з `backend.voices()`); відкрито лише: чи потрібен Django-рівневий мапінг `lang → voice` у condition workflow.
8. **Точні імена полів Settings і helper'ів SP1** (`decode_audio_file`, `wav_bytes`, ISO→NLLB у `languages.py`, поля `*Settings`) — у SP1-спеці зафіксовані лише env та модулі; звірити при реалізації Part B (крок preflight у плані).
9. **Хто в продукті ініціює speech-workflow** (вкладення `communication`, аудіо-нотатки Obsidian, canvas-нода) — поза scope; v1 — `speech_submit` + capabilities для будь-якого workflow.
10. **Номер ADR** (057) — перевірити вільність на момент реалізації.
11. **Розташування `SPEECH_STORAGE_ROOT` у проді** (`/var/lib/obsidian-django/speech`?) і бекап — інфра.
12. ~~`httpx` у runtime-closure сервісів~~ — **закрито** (overview §7 C5): `httpx` входить у спільні залежності bundle `rt-{asr,mt,tts}-*` у SP2 `nix/packages/deps.nix` (SP2 spec §5.4).

---

## 12. Spec self-review (виконано)

- Placeholders: немає TBD/TODO; усі числові ліміти задані.
- Узгодженість: capabilities vs artifact_types — однаково в §3.6, §4, §10 (D5); `mt` worker_type — §3.1, §3.6, §10 (D4); per-row таймаут — §3.5, §3.7, §6.
- Scope: дві частини (Django, сервіс) з чітким wire-контрактом §3.2 — один план з розділенням задач за репо.
- Неоднозначності зняті явно: порядок джерел входу (§3.6), поведінка `port` при повторній реєстрації (§3.8), 400 vs повтор у `complete` (§5.4), поведінка воркера без токен-файлу (§5.2).
