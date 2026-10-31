# Lease LLM-воркера для realtime-перекладу (зміни в Django та llama-service)

Дата: 2026-10-22
Статус: затверджено як напрямок (рішення користувача 2026-10-22). Spec **виконується в репо `swarm-nix`** (`django_api` + `system-conf/common/modules/llm`) окремими сесіями. Цей репо (real-time-translator) реалізує лише клієнтську частину (edge `LeaseKeeper`, spec `2026-10-22-realtime-mvp-design.md` §6.2).

Позначення шляхів:
- `DJ` = `swarm-nix/django_api`;
- `W` = `swarm-nix/system-conf/common/modules/llm/app`.

Посилання вказують на HEAD `3267f3d2`.

## 1. Потік

```
edge (застосунок)                 Django (DJ)                         llama-воркер (W)
  │ POST /api/llm/leases/  ──────▶ вибір воркера, WorkerLease           │
  │   Token <user DRF token>       (heartbeat, expires_at)              │
  │ ◀── {service.base_url, worker_token = sha256:<fp>, ttl}             │
  │                                next(): lease live → {"paused":true, │
  │                                "reason":"leased","retry_after":10} ─▶ не бере WorkItem,
  │                                                                     │ не вивантажує модель
  │ POST {base_url}/v1/chat/completions  Bearer sha256:<fp>  ──────────▶│
  │ POST /api/llm/leases/<id>/heartbeat/  кожні ttl/3                   │
  │ DELETE /api/llm/leases/<id>/  (stop) → воркер знову опитує ≤10 с    │
```

- **Падіння застосунку.** Якщо застосунок упав, heartbeat-и припиняються. Після `expires_at` lease вважається неживим, і на наступному `next()` воркер знову отримує задачі.
- **Задача вже виконується.** Якщо на момент lease воркер виконує WorkItem, перший запит застосунку чекає на `model_lock`, поки задача завершиться. Задача не скасовується.

## 2. Django

### 2.1 Модель `WorkerLease` (`DJ/llm_services/models.py` або `models_lease.py`)

| Поле | Тип |
|---|---|
| `id` | UUID (pk) |
| `service` | FK `LLMService`, `related_name="leases"`, `on_delete=CASCADE` |
| `holder` | FK `User` |
| `client_name` | `CharField(100)` — напр. `"rt-edge@cyborg"` |
| `purpose` | `CharField(50)`, default `"realtime-translation"` |
| `ttl_s` | `PositiveSmallIntegerField`, 15…300, default 60 |
| `created_at`, `heartbeat_at`, `expires_at` | `DateTimeField` |
| `released_at` | `DateTimeField(null=True)` |

- Обмеження: `UniqueConstraint(fields=["service"], condition=Q(released_at__isnull=True), name="one_open_lease_per_service")`.
- Метод `is_live(now) -> bool`: `released_at is None and expires_at > now`.

### 2.2 Сервіс `DJ/llm_services/services/leases.py`

```python
class NoWorkerAvailable(Exception)
class WorkerLeased(Exception)          # .lease (активний чужий)
class LeaseGone(Exception)             # expired або released
def lease_is_live(service: LLMService, now=None) -> bool
def acquire_lease(user, *, client_name: str, service_name: str | None = None, ttl_s: int = 60) -> WorkerLease
def heartbeat_lease(lease: WorkerLease, ttl_s: int | None = None) -> WorkerLease
def release_lease(lease: WorkerLease) -> None   # idempotent
def lease_payload(lease: WorkerLease) -> dict   # §2.3 LeaseOut
```

**`acquire_lease`** (одна транзакція, `select_for_update` на кандидатах):

1. Прострочені відкриті lease закриваються: `released_at = expires_at`.
2. Кандидати: `is_active=True`, `status="online"`, `reported_tier != 1` (не reranker-only).
   - Якщо задано `service_name`, лише він. Якщо в нього вже є живий lease → `WorkerLeased`.
3. Порядок: спершу воркери з непорожнім `current_model`, потім за `last_seen desc`. Перший кандидат без живого lease виграє.
4. Перевірка наживо: `sync_service(candidate)` (DB-статус може бути застарілим до 2 год). Якщо `status != "online"` → наступний кандидат.
5. `worker_callback_credential(service)`; якщо `None` → наступний кандидат. Якщо кандидатів немає → `NoWorkerAvailable`.
6. Створити `WorkerLease(expires_at = now + ttl_s)`.

**`heartbeat_lease`:**
- `LeaseGone`, якщо lease неживий;
- інакше `heartbeat_at = now`, `expires_at = now + ttl`.

`lease_payload` повертає актуальний `worker_token`: після ротації токена воркера застосунок отримає новий через heartbeat.

### 2.3 API (`DJ/llm_services/views/lease_views.py`, маршрути в `DJ/llm_services/urls.py` під `api/llm/`)

**Авторизація:**
- DRF `TokenAuthentication` (DRF Token людини), `IsAuthenticated`;
- worker-токени відхиляються: якщо `request.auth` є `LLMService` → 403.

| Метод і шлях | Тіло | Відповідь |
|---|---|---|
| `POST /api/llm/leases/` | `{"client_name": str, "service": str \| null, "ttl_s": int?}` | 201 `LeaseOut`; 409 `{"detail","holder","expires_at"}` (`WorkerLeased`); 503 `{"detail":"no chat worker available"}` |
| `POST /api/llm/leases/<uuid>/heartbeat/` | `{"ttl_s": int?}` | 200 `LeaseOut`; 404 (не існує або чужий); 410 `{"detail":"lease expired"}` |
| `DELETE /api/llm/leases/<uuid>/` | — | 204 (ідемпотентно; чужий → 404) |
| `GET /api/llm/leases/candidates/` | — | 200 `[{"name","base_url","current_model","reported_tier","status","leased": bool}]` |

`LeaseOut`:

```json
{"id": "<uuid>", "service": {"name": "...", "base_url": "http://host:8080", "current_model": "Qwen3.5-4B.Q4_K_M.gguf"},
 "worker_token": "sha256:<hex>", "ttl_s": 60, "heartbeat_interval_s": 20, "expires_at": "<iso8601>"}
```

### 2.4 `next()` (`DJ/engine/views_work_item.py`, одразу після блоку `is_active`, рядки 305–313)

```python
if calling_service is not None and lease_is_live(calling_service):
    return response.Response({"paused": True, "reason": "leased",
        "message": f"{calling_service.name} is leased for realtime translation",
        "retry_after": settings.LLM_LEASE_RETRY_AFTER_S}, status=status.HTTP_200_OK)
```

- `LLM_LEASE_RETRY_AFTER_S = 10` (settings, env-override).
- **Не** використовувати `is_active=False`. Він ламає `/complete/` для поточної задачі, health sync, ctx-бюджет і routing.

### 2.5 Тести (`DJ/llm_services/tests/test_leases.py`, `DJ/engine/tests/test_next_lease.py`)

- acquire обирає online-воркер з моделлю й пропускає tier 1;
- другий acquire того самого service → 409;
- acquire без кандидатів → 503;
- heartbeat продовжує `expires_at`; після `expires_at` → 410;
- release ідемпотентний;
- worker-токен на lease-API → 403;
- `next()` для воркера з живим lease → `paused` / `reason=leased` / `retry_after=10`;
- після release або прострочення `next()` знову видає задачі;
- прострочений відкритий lease не блокує новий acquire.

## 3. llama-воркер

1. **Причина паузи.** `W/integrations/django/client.py` `get_next_task`: при `paused` зберігати `self.pause_reason = body.get("reason")` (скидати, коли пауза мине).
2. **Без idle-unload під lease.** `W/worker/worker.py` `_check_idle_unload`: нічого не робити, якщо хоч один клієнт `is_paused()` з `pause_reason == "leased"`. Інакше пауза в мові користувача понад `idleUnloadTimeout` (300 с) вивантажить модель посеред сесії.
3. **Автозавантаження в chat.** `W/api/routes_chat.py`: якщо `state.instance is None`, викликати `model.loader.ensure_model_loaded()` (домашня модель) перед `generate`; 503 — лише якщо завантаження не вдалося.
4. **Тести** (`system-conf/common/modules/llm/test_*.py`):
   - `test_endpoint_backoff.py`: `pause_reason` зберігається і скидається;
   - `test_pinned_model.py`: `_check_idle_unload` пропускає вивантаження під lease;
   - новий `test_chat_autoload.py`.

**Рекомендовані виправлення (окремі коміти):**
- `/health` при незавантаженій моделі повертає JSON-масив (`return {...}, 200`). Треба повертати об'єкт.
- `idleUnloadTimeout = 0` має означати «вимкнено», а зараз означає «вивантажити одразу».

## 4. Безпека й обмеження (свідомо прийняті для v1)

- **`worker_token` — це callback-fingerprint воркера.** Він відкриває всі маршрути воркера, а не лише chat, і після завершення lease не відкликається, поки воркер не перереєструється. Прийнятно: tailnet, особисте використання, токен видається лише автентифікованому користувачу Django.
  - **Пізніше:** окремий lease-токен, який воркер приймає лише для `/v1/chat/completions` і `/status`.
- **Кілька Django.** Воркер опитує всі `DJANGO_ENDPOINTS`, а lease на одному Django не зупиняє видачу задач з іншого. Клієнт (edge) бере lease на тому ж воркері (`service = <name>`) в усіх налаштованих Django. Перший Django обирає воркер, інші лише «дзеркалять» lease (best effort).
  - **Пізніше:** реплікація lease через replicator.
- **Ротація токена.** Токен воркера змінюється при рестарті воркера, після TTL (168 год) або після 401. Edge при 401 від воркера робить heartbeat і отримує свіжий `worker_token`.

## 5. Що робить edge (довідково; реалізація — в real-time-translator)

**`LeaseKeeper`:**
1. **Старт** (режим `translate`): acquire на першому доступному Django з `django_urls`, потім дзеркальні acquire на решті (`service = <name>`, помилки лише логуються).
2. **Heartbeat** кожні `heartbeat_interval_s`. При 410/404 — новий acquire (можливо, інший воркер).
3. **Зупинка:** release на всіх Django.
4. **Помилки воркера:** при 401 — heartbeat і повтор. При unreachable/timeout/503 — release і acquire іншого воркера, один повтор запиту.
5. **Django недоступні (owner 2026-10-31: переклад працює, навіть якщо інстанс вимкнений).** Недоступні Django пропускаються, дзеркальний lease на них береться, щойно вони відповідають. Якщо не відповідає жоден — явний режим «без lease» (WARNING + статус у UI) на воркері останнього lease або `direct_url`; heartbeat-потік повторює acquire і виходить з режиму. Відмова Django (409/503) цей режим не вмикає.

DRF-токен користувача зберігається у файлі (sops на ПК). Отримується один раз:
```bash
curl -s -X POST <django>/api/api-token-auth/ -d username=<u> -d password=<p> | jq -r .token
```
