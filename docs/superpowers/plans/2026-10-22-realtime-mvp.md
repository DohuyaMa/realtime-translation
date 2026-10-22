# Realtime MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Довести до робочого стану переклад наживо (pl/uk/ru → en) на розподіленій архітектурі: `rt-asr` + `rt-tts` + llama-воркер на lease через Django; підготувати `rt-asr`/`rt-tts`/edge до деплою через deploy-rs і задокументувати використання.

**Architecture:**
- Два stateless FastAPI-сервіси (`rt-asr`, `rt-tts`):
  - конфіг трирівневий: env → runtime `/config` → параметри запиту;
  - `rt-asr` чергує realtime- і batch-задачі на рівні 30-секундних вікон faster-whisper.
- Edge (PySide6):
  - ріже мікрофонний потік на паузах (`PauseSegmenter`);
  - викликає `rt-asr` → LLM-воркер → `rt-tts`. LLM-воркер — наявний llama-воркер, якого Django віддає на lease і на цей час ставить на паузу для engine; виклик через `/v1/chat/completions`;
  - `rt-asr` і `rt-tts` — через пули endpoint-ів з failover.
- Nix: бандли, NixOS-модуль і HM-модуль повторюють підхід llama-service: `Environment=` (Colmena) + `EnvironmentFile=-<deployRsPath>/env` (deploy-rs).

**Tech Stack:** Python 3.13, FastAPI/uvicorn, faster-whisper (CTranslate2), Kokoro-82M ONNX (onnxruntime + misaki), pysilero-vad, httpx, PySide6, Nix flakes (nixpkgs-unstable, home-manager), NixOS VM tests.

**Spec:** `docs/superpowers/specs/2026-10-22-realtime-mvp-design.md` (головний). Деталі частин KEEP — у specs 2026-10-06 (SP1 `speech-services-api-design`, SP2 `nix-packaging-design`, SP5 `model-optimization-design`). Lease LLM-воркера (зміни в Django і llama виконуються поза цим репо, тут лише клієнт): `docs/superpowers/specs/2026-10-22-llm-worker-lease-design.md`.

## Як читати задачі з «Base»

Багато задач будуються на задачі зі старого плану, де вже є повний код і тести. Формат:
- **Base:** `<план> Task N (рядки)`. Виконай кроки базової задачі в її порядку (TDD).
- **Deltas:** обов'язкові зміни поверх base: що прибрати, що змінити, що додати. Delta перемагає base.
- **Extra tests:** тести, яких у base немає. Пишуться до реалізації разом з тестами base.

Старі плани:
- SP1 = `docs/superpowers/plans/2026-10-06-speech-services-api.md`
- SP2 = `docs/superpowers/plans/2026-10-06-nix-packaging.md`
- SP5 = `docs/superpowers/plans/2026-10-06-model-optimization.md`

## Global Constraints

- **Змінюємо лише цей репо.** `~/repos/swarm-nix` не редагувати.
- **Сервіси:**
  - `rt-asr` — порт 8091, юніт/користувач `rt-asr`, `/var/lib/rt-asr`;
  - `rt-tts` — порт 8093, юніт/користувач `rt-tts`, `/var/lib/rt-tts`;
  - `rt-mt` і порт 8092 **не існують**: жодного коду, опції, пакета чи тесту з `mt`.
- **Мови:**
  - ASR: `RT_ASR_LANGUAGES` default `pl,uk,ru,en`;
  - ціль перекладу — завжди `en`;
  - TTS — лише голоси з префіксами `a`/`b` (28 шт.), default `af_heart`.
- **Пріоритет конфігу:** параметр запиту > runtime override (`/config`) > env > default у коді.
- **`/config`:**
  - `GET` → `{"values","source","bootstrap","version"}`;
  - `POST {"version":int,"values":{},"reset":[]}` → те саме + `"reloaded":bool`;
  - помилка валідації → 422 `{"detail":str}`, нічого не застосовано;
  - невідомі ключі ігноруються;
  - BOOTSTRAP-поле в `values` → 422.
- **LLM для перекладу:** lease наявного llama-воркера через Django за spec `2026-10-22-llm-worker-lease-design.md` (шляхи, тіла, коди — дослівно). Dev-режим `direct_url` існує лише для локального `llama-server`.
- **Токени лише з файлів.** `/health` без auth. `/config` та `/v1/*` — з bearer, якщо задано `RT_API_TOKEN_FILE`.
- **GPU:**
  - лише окремий scoped інстанс nixpkgs з `cudaSupport = true` (SP2 `variant-pkgs.nix`);
  - **ніколи** `torch → torch-bin`;
  - overlay додає лише атрибут `rtSpeech`.
- **Git-only:** усе, що потрібно для збірки, закомічене; жодних абсолютних шляхів `/home/...`; моделі не в репо.
- **Тести:**
  - усі запускаються як `nix develop -c pytest <path> -q`;
  - якщо SP5 base каже `nix develop .#bench-cpu`, використовуй `nix develop` (bench-shells відкладено);
  - повільні тести — під маркером `slow` (`RT_SLOW_TESTS=1`).
- **Коміти:** без рядків `Co-Authored-By` (правило користувача); стиль Conventional Commits.
- **Форматування:** `black`, `flake8` на змінених файлах.

## Review Focus

1. **Зупинка edge у режимі `transcribe` посеред 10-хвилинного шматка.** Очікування: останній шматок відправлено і розпізнано, текст у UI. Тест у Task 28: `test_stop_flushes_and_drains_transcribe_chunk`.
2. **Realtime-запит під час batch-транскрипції на тому самому `rt-asr`.** Очікування: realtime завершується, не чекаючи кінця batch. Тести: Task 4 `test_higher_priority_runs_between_batch_steps`, Task 17 `test_realtime_request_overtakes_running_batch`.
3. **llama повертає `<think>…</think>` або порожній `content`, токен воркера ротувався (401), воркер зник посеред lease.** Очікування:
   - чистий переклад;
   - при 401 — свіжий токен через heartbeat і повтор;
   - при недоступному воркері — новий lease і повтор;
   - інакше `ServiceError` без падіння пайплайну.

   Тести: Task 24 `test_llm_401_invalidates_auth_and_retries_once`, Task 25 `test_invalidate_unreachable_releases_and_reacquires`.
4. **Endpoint падає посеред сесії.** Очікування: один повтор на наступному endpoint-і і закріплення сесії за ним. Тест у Task 26: `test_failover_repins_and_retries_once`.
5. **Пошкоджений `runtime-config.json` або невалідний override.** Очікування: сервіс стартує з bootstrap, `POST /config` з поганим значенням нічого не змінює. Тести в Task 7.

---

## Phase 0 — Основа репо

### Task 1: Bump nixpkgs до ревізії інфри

**Base:** SP2 Task 0 (рядки 75–121).

**Deltas:**
- **Step 1:** читати вузол кореневого input, а не `.locks.nodes.nixpkgs`:
  `jq -r '.nodes[.nodes.root.inputs.nixpkgs].locked | "\(.rev) \(.lastModified)"' ~/repos/swarm-nix/system-conf/flake.lock`
  Очікувана ревізія — та, що виведе ця команда (станом на 2026-10-22 це `e7439b6…`), а не `7a0f122`.
- **Rationale:** прибрати NLLB/MT. Залишається CT2 для faster-whisper (ROCm ASR) і збіг drvPath з інфрою для попадань у кеш.

**Interfaces:** Produces: `flake.lock`, де `nixpkgs.locked.rev` дорівнює ревізії кореневого nixpkgs інфри.

- [ ] **Step 1:** Виконати кроки base з deltas.
- [ ] **Step 2:** Перевірка: `jq -r '.nodes[.nodes.root.inputs.nixpkgs].locked.rev' flake.lock` дорівнює виводу тієї ж команди для `~/repos/swarm-nix/system-conf/flake.lock`.
- [ ] **Step 3:** Commit `chore(nix): bump nixpkgs to infra revision`.

### Task 2: Git-гігієна, каркас flake, `lib.rtSpeech`, devShell

**Base:** SP2 Task 1 (рядки 123–519).

**Deltas:**
- `nix/lib/default.nix`:
  - `services = { asr = { port = 8091; …}; tts = { port = 8093; …}; }` — **без `mt`** (base рядок 381);
  - прибрати mt-гілку `envLines` (417–423);
  - додати безумовний рядок `RT_CONFIG_STATE=${cfg.configState}`, якщо `cfg.configState != null`. Позиція — після `RT_LOG_LEVEL`.
- `nix/checks/env-lines.nix`:
  - прибрати fixture `mt` (191), `testMt` і `testMtBeamsWhenSet`;
  - `testRocmGfxOnlyOnRocm` і `testTokenPathLiteral` переписати на `asr` (очікується `RT_API_TOKEN_FILE=/run/credentials/rt-asr.service/api-token`);
  - `testHealthCheck` → порт 8091;
  - новий тест `testConfigState`: `cfg.configState = "/var/lib/rt-asr/runtime-config.json"` → рядок `RT_CONFIG_STATE=/var/lib/rt-asr/runtime-config.json` присутній; з `null` — відсутній.
- `nix/devshell.nix`:
  - `transformers` і `sentencepiece` прибирає Task 2b;
  - додати `httpx`.
- Global Constraints base (рядки 18–21, 27, 35, 37) — у цьому плані не діють; замість них Global Constraints вище.

**Interfaces:** Produces:
- `rtLib = { services.<asr|tts>.{port,unit,stateDir}; variantForDevice; envLines :: svc -> cfg -> [str]; mkEnvFile { pkgs, service, cfg }; healthCheckCommand :: svc -> cfg -> str; }`;
- `nix develop` (default devShell);
- `checks.<system>.{flake-purity,env-lines}`.

- [ ] **Step 1:** Виконати кроки base з deltas (тести env-lines пишуться першими).
- [ ] **Step 2:** `nix build .#checks.x86_64-linux.env-lines -L` → PASS; `nix flake check --no-build` → без помилок.
- [ ] **Step 3:** Commit `build(nix): flake skeleton, rtSpeech lib, devshell`.

### Task 2b: Видалення легасі (одразу)

**Base:** SP2 Task 15 (рядки 4143–4279) — перелік того, що видаляється. Проєкт особистий і не в продакшені, тож легасі видаляється одразу. Edge-застосунок не працює до Task 29 — це очікувано.

**Deltas:**
- **Без передумов і без gate.** Перед видаленням: `git tag legacy-ipc-last` (сам тег пушить користувач).
- **Видалити:**
  - код: `src/capture/`, `src/playback/`, `src/whisper/`, `src/translate/`, `src/tts/`, `src/pipeline/`, `src/common/ipc.py`, `src/status_logger.py`, `src/adapters/ipc_adapter.py`, `src/models/`;
  - Nix: `flake-global/`, `parts/`, `nixosModules/`, `flake.nix.old`;
  - тести: усі тести, що імпортують видалене (`test_ipc_communication`, `test_wyoming_integration`, `test_integration`, `test_service_status`, `test_pipeline`, `test_direct_adapter`, `tests/systemd-update/`, `test_ai_models`, `test_tts_engine`, `test_ui_components`). Їхні нові версії з'являться в задачах Phase F.
- **`flake.nix`:**
  - прибрати всі легасі-outputs (packages, apps, legacy HM-модуль і все, що посилається на `flake-global/`, `parts/`, `nixosModules/`);
  - лишити лише те, що створив Task 2: `devShells.default`, `checks.{flake-purity,env-lines}`, `lib.rtSpeech`, `nixosModules.virtual-sinks` з `nix/nixos/`;
  - решту outputs додають Tasks 21–23, 31. Фінальний вигляд — SP2 Task 15 без `mt`.
- **`nix/devshell.nix`:** прибрати `transformers` і `sentencepiece`.
- **`pyproject.toml`:** прибрати scripts легасі-сервісів, лишити `translator-ui`.
- **Не чіпати** `src/translation_system.py`, `src/adapters/direct_adapter.py`, `src/controller/`, `src/main.py`, `src/ui/`, `src/core/`, `src/audio/` — їх переписують Tasks 24–30. Імпорти видалених модулів у них лишаються зламаними до Task 28–29.

- [ ] **Step 1:** `git tag legacy-ipc-last`, потім видалення за deltas.
- [ ] **Step 2:** `nix flake check --no-build` → OK; `nix develop -c true` → OK.
- [ ] **Step 3:** `command grep -rn -E "src\.(whisper|translate|tts|capture|playback|pipeline|models)|common\.ipc|flake-global" --include=*.py --include=*.nix . | command grep -v -E "^./(docs|context|plans)/"` → лише `src/translation_system.py`, `src/adapters/direct_adapter.py`, `src/main.py`, `src/controller/*` (виправляються в Task 28–29).
- [ ] **Step 4:** Commit `refactor!: remove legacy IPC pipeline`.

---

## Phase A — Спільне ядро сервісів (`src/services/common`)

### Task 3: Тестовий каркас сервісів

**Base:** SP1 Task 1 (рядки 58–182).

**Deltas:**
- Не створювати `src/services/mt/__init__.py`.
- Залежності devshell уже додано в Task 2: перевірити, що `fastapi uvicorn python-multipart httpx websockets pytest-timeout` імпортуються, і нічого не дублювати в `flake-global/`.

**Interfaces:** Produces:
- `src/services/{__init__,common/__init__,asr/__init__,tts/__init__}.py`;
- `tests/services/conftest.py` (маркер `slow`);
- `tests/services/svc_testutil.py` (`SR`, `WINDOW`, `tone`, `pcm`, `speech_windows`, `silence_windows`, `wav_file`, `running`).

- [ ] **Step 1:** Виконати base з deltas.
- [ ] **Step 2:** `nix develop -c pytest tests/services -q` → `no tests ran` без помилок імпорту.
- [ ] **Step 3:** Commit `test(services): scaffold`.

### Task 4: InferenceWorker з BATCH-пріоритетом і покроковими генераторами

**Base:** SP1 Task 3 (рядки 443–669).

**Deltas:**
- `class Priority(IntEnum): FINAL = 0; REQUEST = 1; PARTIAL = 2; BATCH = 3`.
- Новий метод:
  ```python
  def submit_iter(self, gen_factory: Callable[..., Iterator[T]], *args,
                  priority: Priority = Priority.BATCH,
                  on_item: Callable[[T], None] | None = None,
                  cancel: threading.Event | None = None,
                  bypass_limit: bool = False, **kwargs) -> Future[list[T]]
  ```
- Семантика `submit_iter`:
  - **Перший крок** викликає `gen_factory(*args, **kwargs)` на потоці inference (eager-частина faster-whisper потребує моделі).
  - **Кожен крок** — один `next(gen)`, потім `on_item(item)`, якщо задано, далі елемент додається в список результату. Після кроку в чергу ставиться продовження `(priority, next(seq), …)`.
  - **Кінець:** `StopIteration` → `future.set_result(items)`. Виняток → `gen.close()` + `future.set_exception(exc)`.
  - **Скасування:** перед кожним кроком перевіряється `cancel.is_set()`. Якщо встановлено → `gen.close()` + `future.set_exception(CancelledJob())`, де `class CancelledJob(RuntimeError)` визначено в `worker.py`.
  - **`pending`:** уся задача займає один слот від `submit_iter` до завершення. Продовження не змінюють `pending` і не перевіряються проти `max_pending`.
  - **`stop()`:** продовження, яке лежить у черзі, закривається (`gen.close()`), а його future отримує `WorkerBusy("stopping")`.
- Внутрішній елемент черги розрізняє звичайну задачу і продовження, наприклад через `kind` у кортежі. Формат на вибір реалізатора, але FIFO в межах одного пріоритету зберігається.

**Extra tests** (`tests/services/test_svc_worker.py`):
```python
def test_submit_iter_collects_items_in_order(): ...  # factory yields 1,2,3 → result()==[1,2,3]
def test_higher_priority_runs_between_batch_steps():
    # factory: yield 1; (step blocks on event e1); yield 2
    # while batch is at step 1, submit(fn, priority=Priority.REQUEST) → its future completes
    # BEFORE batch future; order log == ["b1", "req", "b2"]
def test_submit_iter_cancel_between_steps():  # cancel.set() after first on_item → CancelledJob, generator finally-block ran
def test_submit_iter_exception_mid_generator():  # raises ValueError on 2nd next → future.exception() is ValueError, gen closed
def test_submit_iter_pending_counts_one_slot():  # max_pending=1: during batch, pending==1; plain submit raises WorkerBusy
def test_stop_with_queued_continuation_fails_future():  # stop() while continuation queued → WorkerBusy, gen closed
```

**Interfaces:** Produces:
- `Priority` (з `BATCH`);
- `WorkerBusy`;
- `CancelledJob`;
- `InferenceWorker.submit_iter(...)` (сигнатура вище).

- [ ] **Step 1:** Написати тести base і extra.
- [ ] **Step 2:** `nix develop -c pytest tests/services/test_svc_worker.py -q` → FAIL (`ImportError`/`AttributeError`).
- [ ] **Step 3:** Реалізувати base + deltas у `src/services/common/worker.py`.
- [ ] **Step 4:** Тести → PASS.
- [ ] **Step 5:** Commit `feat(services): inference worker with batch stepping`.

### Task 5: Env-парсинг, `BaseSettings` і класи полів

**Base:** SP1 Task 2 (рядки 185–440).

**Deltas:**
- `DEFAULT_PORTS = {"rt-asr": 8091, "rt-tts": 8093}` (без `rt-mt`).
- `BaseSettings` отримує поле `config_state: str | None` (env `RT_CONFIG_STATE`, default `None`).
- Кожен клас налаштувань оголошує три `ClassVar`. Для `BaseSettings`:
  - `ENV_KEYS: ClassVar[dict[str, str]]` — поле → env-ключ: `backend→RT_<SVC>_BACKEND`, `model→RT_MODEL`, `device→RT_DEVICE`, `compute_type→RT_COMPUTE_TYPE`, `max_pending→RT_MAX_PENDING`, `log_level→RT_LOG_LEVEL`;
  - `TUNABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"log_level"})`;
  - `RELOAD_FIELDS: ClassVar[frozenset[str]] = frozenset({"backend","model","device","compute_type"})`.
  
  Підкласи розширюють ці набори.
- Новий helper:
  ```python
  def render_env_value(value: object) -> str
  ```
  Правила: `bool` → `"true"`/`"false"`; `tuple`/`list` → значення через кому; `None` → `""`; інше → `str(value)`.
- `from_env(env)` має лишатися чистою функцією від `env: Mapping[str, str]`. На ній будується валідація overrides у Task 7.

**Extra tests** (`tests/services/test_svc_env_settings.py`):
```python
def test_default_ports_have_no_mt(): assert set(DEFAULT_PORTS) == {"rt-asr", "rt-tts"}
def test_render_env_value():
    assert render_env_value(True) == "true"; assert render_env_value(("pl","uk")) == "pl,uk"
    assert render_env_value(None) == ""; assert render_env_value(2.5) == "2.5"
def test_config_state_from_env(): ... RT_CONFIG_STATE=/x/rc.json → settings.config_state == "/x/rc.json"
```
Тести base, де використовується `service="rt-mt"`, перевести на `rt-asr`.

**Interfaces:** Produces:
- `SettingsError`, `env_*`;
- `BaseSettings` (з `config_state`, `ENV_KEYS`, `TUNABLE_FIELDS`, `RELOAD_FIELDS`);
- `base_from_env`, `render_env_value`, `DEFAULT_PORTS`, `apply_model_dir`.

- [ ] **Step 1:** Тести (base + extra) → **Step 2:** FAIL → **Step 3:** реалізація в `src/services/common/{version,env,settings}.py` → **Step 4:** PASS → **Step 5:** Commit `feat(services): env settings with field classes`.

### Task 6: Помилки, auth, ServiceRuntime, health, app factory

**Base:** SP1 Task 4 (рядки 672–876) без змін + SP1 Task 5 (рядки 879–1354).

**Deltas (Task 5 base):**
- У тестах (930–937, 962, 989–990, 1021–1023) `service="rt-mt"` / `RT_MT_BACKEND` замінити на `rt-asr` / `RT_ASR_BACKEND`.
- `ServiceRuntime`:
  ```python
  def submit_iter(self, gen_factory, *args, priority=Priority.BATCH, on_item=None, cancel=None, **kw) -> Future
  async def run_iter(self, gen_factory, *args, priority=Priority.BATCH, on_item=None, cancel=None, **kw) -> list
  def reload(self, backend_factory: Callable[[], Any], timeout_s: float = 600.0) -> None
  ```
  Поведінка `reload`:
  - вантажить новий backend у loader-потоці;
  - на час завантаження `/status.model.state == "loading"`, а старий backend ще обслуговує запити;
  - після успіху атомарно підміняє backend;
  - при помилці лишає старий backend, повертає стан `ready` і пробрасує виняток.
- `create_service_app(..., config: "RuntimeConfig | None" = None)` — якщо передано, монтує router з Task 7 за bearer-залежністю і кладе config у `app.state.config`.
- `/status` додає `"pending": int` і `"config_version": int | None`.

**Extra tests** (`tests/services/test_svc_runtime_health.py`):
```python
def test_status_has_pending_and_config_version(): ...  # keys present; config_version None without config
def test_reload_swaps_backend_on_success(): ...        # FakeBackend A → reload(lambda: B) → runtime backend is B
def test_reload_failure_keeps_old_backend(): ...       # factory raises → old backend still serves, state ready, exception propagated
async def test_run_iter_returns_items(): ...           # via runtime with fake gen
```

**Interfaces:** Produces:
- `ApiError`, `install_error_handlers`, `load_token`, `make_bearer_dependency`;
- `ServiceRuntime(service, backend_factory, max_pending)` з `.start/.stop/.require_ready/.run/.submit_threadsafe/.run_iter/.submit_iter/.reload/.backend`;
- `create_service_app(*, settings, runtime, token, routers=(), ws_routers=(), lifespan_hooks=(), vram=None, config=None)`;
- `run_service(settings_loader, app_factory) -> int`.

- [ ] **Step 1:** Тести → **Step 2:** FAIL → **Step 3:** реалізація (`common/{errors,auth,runtime,gpu,health,app,logs,serve}.py`) → **Step 4:** PASS → **Step 5:** Commit `feat(services): runtime, health, app factory`.

### Task 7: Runtime config (`/config`) зі збереженням стану

**Files:**
- Create: `src/services/common/runtime_config.py`
- Test: `tests/services/test_svc_runtime_config.py`

**Interfaces:**
- Consumes:
  - `BaseSettings.{ENV_KEYS,TUNABLE_FIELDS,RELOAD_FIELDS}`, `from_env`, `render_env_value`, `SettingsError` (Task 5);
  - `make_bearer_dependency` (Task 6).
- Produces:
  ```python
  class RuntimeConfig(Generic[S]):
      def __init__(self, settings_cls: type[S], bootstrap_env: Mapping[str, str],
                   state_path: Path | None = None) -> None
      def current(self) -> S
      def effective(self) -> dict          # {"values","source","bootstrap","version"}
      def apply(self, values: Mapping[str, object], version: int,
                reset: Sequence[str] = ()) -> list[str]   # changed RELOAD field names; ValueError on invalid
      def restore(self) -> None            # read state_path; invalid/missing → warning, bootstrap kept
  def build_config_router(cfg: RuntimeConfig, on_reload: Callable[[Any], None]) -> APIRouter
  ```

Правила:
- **Валідація `apply` all-or-nothing:**
  1. Ключі поза `TUNABLE_FIELDS | RELOAD_FIELDS`, але присутні в `ENV_KEYS` (тобто BOOTSTRAP), → `ValueError("<field> is not runtime-configurable")`.
  2. Ключі, яких немає в `ENV_KEYS`, ігноруються.
  3. Будується `env = {**bootstrap_env, **{ENV_KEYS[f]: render_env_value(v) for overrides після reset}}`.
  4. Викликається `settings_cls.from_env(env)`. `SettingsError` → `ValueError`.
- **Значення:** `values` — поля нового `current()`, але лише `TUNABLE ∪ RELOAD`. `source[f]` — `"override"` або `"bootstrap"`.
- **Збереження:** після успішного `apply` стан атомарно записується в `state_path` (`tmp` + `os.replace`): `{"version": int, "overrides": {field: value}}`.
- **Router:**
  - `GET /config` → `effective()`;
  - `POST /config` з тілом `{"version": int, "values": dict, "reset": list[str] = []}` викликає `apply`;
  - якщо змінено RELOAD-поля, виконується `on_reload(cfg.current())` (синхронно, в `run_in_threadpool`);
  - відповідь — `effective() | {"reloaded": bool}`;
  - `ValueError` → 422 `{"detail": str(e)}`;
  - виняток з `on_reload` → overrides відкочуються до попереднього стану, відповідь 500 `{"detail": ...}`.

- [ ] **Step 1: Написати тести, що падають**
```python
def test_effective_shape_bootstrap_only(): # values/source/bootstrap/version==0, all sources "bootstrap"
def test_apply_tunable_changes_current_and_source(): # apply({"log_level":"debug"},1) → current().log_level=="debug", source=="override", version==1, returns []
def test_apply_reload_field_reported(): # apply({"model":"whisper-small"},2) == ["model"]
def test_apply_invalid_is_all_or_nothing(): # apply({"log_level":"debug","max_pending":-1},3) raises ValueError; current unchanged
def test_bootstrap_field_rejected(): # apply({"port":1},1) raises ValueError("port is not runtime-configurable")
def test_unknown_key_ignored(): # apply({"nope":1},1) → no error, version==1
def test_reset_restores_bootstrap(): # apply override, then apply({}, 2, reset=["log_level"]) → source bootstrap
def test_state_persisted_and_restored(tmp_path): # apply → file exists; new RuntimeConfig(...).restore() → same overrides/version
def test_corrupt_state_file_keeps_bootstrap(tmp_path, caplog): # write "{not json" → restore() no raise, warning logged
def test_router_get_post_and_422(): # TestClient: GET 200 shape; POST invalid → 422 detail; POST valid → reloaded False
def test_router_reload_failure_rolls_back(): # on_reload raises → 500; GET shows previous values
def test_router_requires_bearer_when_token(): # token set → no header 401
```
Для тестів використовується мінімальний підклас `BaseSettings` (`service="rt-asr"`) з `TUNABLE_FIELDS={"log_level","max_pending"}`.

- [ ] **Step 2:** `nix develop -c pytest tests/services/test_svc_runtime_config.py -q` → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3:** Реалізувати `runtime_config.py`. Lock (`threading.Lock`) на `apply`/`current`; `current()` повертає кешований frozen-об'єкт.
- [ ] **Step 4:** Тести → PASS.
- [ ] **Step 5:** Commit `feat(services): runtime /config with persistence`.

### Task 8: Аудіо-хелпери і фільтр повторів

**Base:** SP1 Task 6 (рядки 1357–1479) і SP1 Task 7 (рядки 1482–1608).

**Deltas:** у SP1 Task 7 пропустити зміну `src/whisper/whisper_service.py`, бо легасі видалено в Task 2b.

**Interfaces:** Produces:
- `common/audio.py`: `TARGET_RATE`, `AudioDecodeError`, `resample`, `decode_audio_file(data: bytes, target_rate=16000) -> np.ndarray`, `pcm16_to_float`, `float_to_pcm16`;
- `asr/textfilter.py`: `normalize_word`, `filter_repetitions`.

- [ ] **Step 1:** Виконати кроки обох base (тести → FAIL → код → PASS).
- [ ] **Step 2:** Commit `feat(services): audio helpers and repetition filter`.

### Task 9: Спільна сегментація на паузах (`src/segmentation`)

**Files:**
- Create: `src/segmentation/__init__.py`, `src/segmentation/pause.py`, `src/segmentation/chunks.py`
- Test: `tests/segmentation/test_pause.py`, `tests/segmentation/test_chunks.py`

**Interfaces:** Produces (лише numpy, без інших залежностей):
```python
SAMPLE_RATE = 16000; WINDOW_SAMPLES = 512; WINDOW_MS = 32

@dataclass(frozen=True)
class SegmenterConfig:
    threshold: float = 0.5
    min_speech_ms: int = 400
    end_silence_ms: int | None = 700
    soft_max_ms: int = 8_000
    soft_silence_ms: int = 250
    hard_max_ms: int = 20_000
    lookback_ms: int = 1_500
    keep_silence_ms: int | None = None
    preroll_ms: int = 300
    def validate(self) -> None                     # ValueError: soft_max<hard_max, lookback<=hard_max, всі >0
    @classmethod
    def translate(cls) -> "SegmenterConfig"         # значення за замовчуванням вище
    @classmethod
    def transcribe(cls, chunk_min_s: float = 300, chunk_max_s: float = 600) -> "SegmenterConfig"
        # end_silence_ms=None, soft_max_ms=chunk_min_s*1000, soft_silence_ms=500,
        # hard_max_ms=chunk_max_s*1000, lookback_ms=5000, keep_silence_ms=2000

@dataclass(frozen=True)
class Segment:
    audio: np.ndarray        # int16 mono 16 kHz
    start_s: float
    end_s: float
    reason: Literal["end", "soft", "hard", "flush"]

class PauseSegmenter:
    def __init__(self, cfg: SegmenterConfig) -> None
    def feed(self, window: np.ndarray, prob: float) -> list[Segment]   # window: int16, len WINDOW_SAMPLES
    def flush(self) -> Segment | None
    def reconfigure(self, cfg: SegmenterConfig) -> None                # діє з наступного вікна
    def reset(self) -> None

def plan_chunks(speech_spans: Sequence[tuple[int, int]], total_samples: int,
                chunk_samples: int, lookback_samples: int) -> list[tuple[int, int]]
```

**Алгоритм `PauseSegmenter`** (визначає поведінку, тести нижче):
- **Idle:**
  - тримає кільцевий pre-roll з `preroll_ms` вікон;
  - вікно з `prob ≥ threshold` починає сегмент з pre-roll.
- **У сегменті** — лічильники `speech_ms`, `silence_run_ms` (поточна тиша) і `len_ms`.
- **Завершення сегмента** (перевіряється по порядку на кожному вікні):
  1. **`end`:** `end_silence_ms` не `None` і `silence_run_ms ≥ end_silence_ms` → сегмент завершується. Якщо `speech_ms ≥ min_speech_ms`, він емітиться, інакше відкидається. Тишу після останнього мовлення обрізати до `min(silence_run_ms, 300)` мс.
  2. **`soft`:** `len_ms ≥ soft_max_ms` і `silence_run_ms ≥ soft_silence_ms` → емітити сегмент, розрізаючи посередині поточної паузи. Хвіст після розрізу стає початком нового сегмента, якщо в ньому є мовлення; інакше нового сегмента немає.
  3. **`hard`:** `len_ms ≥ hard_max_ms` → серед останніх `lookback_ms` вікон знайти вікно з мінімальною `prob` (при рівності береться найпізніше), розрізати **перед** ним. Хвіст переноситься в новий сегмент.
- **`keep_silence_ms`:** якщо задано, вікна тиші понад `keep_silence_ms` у поточному `silence_run` не додаються в аудіо сегмента. Час (`start_s`/`end_s`) рахується за реальним потоком.
- **Безперервність:** для `soft`/`hard` `next.start_s == prev.end_s`, жоден семпл не дублюється і не губиться (за винятком вирізаної тиші).
- **`flush()`:** повертає `Segment(reason="flush")`, якщо поточний сегмент має `speech_ms ≥ min_speech_ms`, інакше `None`.

**Алгоритм `plan_chunks`:**
- Якщо `total ≤ chunk_samples` → `[(0, total)]`.
- Інакше від `pos`:
  - ціль `t = pos + chunk_samples`;
  - кандидати — паузи (проміжки між сусідніми spans), що перетинають `[t − lookback, t]`;
  - береться найдовша пауза, межа — середина її перетину з вікном;
  - якщо пауз немає → межа `t`;
  - останній шматок закінчується на `total`.
- Шматки суміжні, кожен непорожній.

- [ ] **Step 1: Написати тести, що падають.** Хелпер `feed_probs(seg, probs)` подає вікна `np.full(512, 1000 if p>=.5 else 0, int16)` з відповідною `prob` і збирає сегменти.
```python
# test_pause.py
def test_translate_phrase_ends_on_700ms_silence():
    segs = feed_probs(PauseSegmenter(SegmenterConfig.translate()), [0.9]*31 + [0.0]*22)  # ~1 s speech, 704 ms silence
    assert [s.reason for s in segs] == ["end"]
def test_short_blip_discarded():  # 0.2 s speech + 0.8 s silence → []
def test_soft_cut_after_soft_max_on_short_pause():
    probs = [0.9]*260 + [0.0]*9 + [0.9]*40   # 8.3 s speech, 288 ms pause, more speech
    segs = feed_probs(PauseSegmenter(SegmenterConfig.translate()), probs)
    assert segs[0].reason == "soft" and 8.3 <= segs[0].end_s <= 8.6
def test_hard_cut_at_quietest_window_in_lookback():
    probs = [0.9]*625; probs[600] = 0.1        # 20 s of speech, dip at 19.2 s
    segs = feed_probs(PauseSegmenter(SegmenterConfig.translate()), probs)
    assert segs[0].reason == "hard" and abs(segs[0].end_s - 600*0.032) < 1e-6
def test_hard_cut_carries_tail_without_loss():
    seg = PauseSegmenter(SegmenterConfig.translate()); probs = [0.9]*700 + [0.0]*30
    segs = feed_probs(seg, probs)
    assert segs[1].start_s == segs[0].end_s
    # "end" fires after 22 silent windows; trailing silence is trimmed to 300 ms → 300 // 32 = 9 windows
    assert sum(len(s.audio) for s in segs) == (700 + 300 // 32) * 512   # nothing lost, nothing duplicated
def test_transcribe_ignores_pauses_before_chunk_min():
    cfg = SegmenterConfig.transcribe(chunk_min_s=10, chunk_max_s=20)
    probs = ([0.9]*100 + [0.0]*25) * 4         # 4 × (3.2 s speech + 0.8 s pause)
    segs = feed_probs(PauseSegmenter(cfg), probs)
    assert len(segs) == 1 and segs[0].reason == "soft" and segs[0].end_s >= 10.0
def test_transcribe_trims_long_silence_to_keep_silence():
    cfg = SegmenterConfig.transcribe(chunk_min_s=30, chunk_max_s=60)   # chunk_min > stream length → no soft cut
    seg = PauseSegmenter(cfg); feed_probs(seg, [0.9]*50 + [0.0]*313 + [0.9]*50)   # 10 s silence inside
    out = seg.flush(); assert len(out.audio) <= (100 + 2000//32 + 1) * 512
def test_flush_returns_pending_speech_and_none_for_blip(): ...
def test_reconfigure_applies_from_next_window(): ...  # switch translate→transcribe mid-stream: no "end" on 0.8 s pause
def test_validate_rejects_bad_config():
    with pytest.raises(ValueError): SegmenterConfig(soft_max_ms=5000, hard_max_ms=4000).validate()
# test_chunks.py
def test_short_audio_single_chunk(): assert plan_chunks([], 100, 1000, 100) == [(0, 100)]
def test_cut_in_middle_of_longest_pause_in_lookback():
    assert plan_chunks([(0, 900), (950, 2500)], 2500, 1000, 200)[0] == (0, 925)
def test_no_pause_hard_cut(): assert plan_chunks([(0, 2500)], 2500, 1000, 200)[0] == (0, 1000)
def test_chunks_contiguous_cover_all():  # random spans → chunks[i][1]==chunks[i+1][0], first 0, last total
```
- [ ] **Step 2:** `nix develop -c pytest tests/segmentation -q` → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3:** Реалізувати `pause.py`, `chunks.py`; `__init__.py` реекспортує публічні імена.
- [ ] **Step 4:** Тести → PASS.
- [ ] **Step 5:** Commit `feat(segmentation): pause-aware segmenter and chunk planner`.

---

## Phase B — Моделі (SP5)

### Task 10: Реєстр моделей і HF-середовище

**Base:** SP5 Task 2 (рядки 224–506) і SP5 Task 3 (рядки 510–639). Тести запускати як `nix develop -c pytest …`.

**Deltas:**
- **Task 2:**
  - видалити NLLB `ModelSpec` (385–397);
  - `DEFAULT_ALIASES` без NLLB (247);
  - `spec.service in {"asr","tts"}` (260);
  - `test_specs_for_service` (278–279) — лише asr/tts;
  - коментар 329 — `"asr" | "tts"`;
  - видалити `whisper-medium` (358–370) і `kokoro-82m-v1.0-torch` (439–477).
- **Task 3** — без змін.

**Interfaces:** Produces:
- `FileSpec`, `ModelSpec`, `REGISTRY`, `get_spec(alias) -> ModelSpec`, `specs_for(service)`, `UnknownModel`;
- `configure_hf_home`, `set_offline`, `read_token_file`.
- Aliases: `whisper-small`, `whisper-large-v3-turbo`, `kokoro-82m-v1.0-onnx`.

- [ ] **Step 1:** Base з deltas (TDD). **Step 2:** PASS. **Step 3:** Commit `feat(models): registry and HF env`.

### Task 11: Device defaults і сховище моделей

**Base:** SP5 Task 4 (рядки 643–890) і SP5 Task 5 (рядки 894–1358).

**Deltas:**
- **Task 4:**
  - прибрати рядки `("mt", …)` у `_DEFAULT_COMPUTE`/`_DEFAULT_MODEL`/`_DEFAULT_BEAMS` (780–804);
  - прибрати відповідні тестові рядки (685–686, 706–707, 719–720).
- **Task 5:** у тесті `make_spec` поставити `service="asr"` (933).

**Interfaces:** Produces:
- `normalize_device`, `backend_device`, `default_compute_type(service, device)`, `default_model(service, device)`, `default_beams(service, device)`, `default_threads(env)`, `resolve_ct2_compute_type`, `require_gpu`, `DeviceError`;
- `ModelStore`, `ModelSettings.from_env()`, `prepare_model(value, settings) -> Path`, `ModelNotProvisioned`, `ModelIntegrityError`, `parse_bool`.

- [ ] **Step 1:** Base з deltas. **Step 2:** PASS. **Step 3:** Commit `feat(models): device defaults and model store`.

### Task 12: CLI `rt-models`

**Base:** SP5 Task 6 (рядки 1362–1550), без змін.

**Interfaces:** Produces: `src/services/common/models_cli.py: main(argv, *, registry, store_factory) -> int`, entry point `rt-models` у `pyproject.toml`.

- [ ] **Step 1:** Base. **Step 2:** Commit `feat(models): rt-models CLI`.

### Task 13: WhisperEngine з лінивими сегментами і визначенням мови серед дозволених

**Base:** SP5 Task 10 (рядки 2318–2579).

**Deltas** — додати до `WhisperEngine`:
```python
def iter_transcribe(self, audio, *, language: str, beam_size: int = 1,
                    vad: VadProfile | None = FILE_VAD,
                    initial_prompt: str | None = None) -> Iterator[Segment]
def detect_language(self, audio, allowed: Sequence[str]) -> tuple[str, float]
```
- **`iter_transcribe`** — генератор:
  - перший `next()` викликає `self._model.transcribe(...)` з тими самими final-параметрами, що й `transcribe(partial=False)`;
  - далі віддає `Segment` по одному, **не** збираючи список;
  - `language` обов'язковий.
- **`detect_language`:**
  - бере перші ≤ 30 с аудіо;
  - `self._model.detect_language(audio=…)` → `all_language_probs`;
  - argmax серед `allowed` (у порядку `allowed` при рівності);
  - повертає `(lang, prob)`;
  - якщо `allowed` порожній → `ValueError`.
- Наявний `transcribe` перевикористовує `iter_transcribe` через `list(...)`, коли `language` задано.

**Extra tests** (`tests/services/test_svc_whisper_engine.py`, з `model_factory`-фейком):
```python
def test_iter_transcribe_is_lazy(): # fake model's segments generator records consumption; after first next() only 1 consumed
def test_detect_language_restricted_to_allowed():
    # fake detect_language returns ("ru",.6,[("ru",.6),("uk",.3),("pl",.1)])
    assert engine.detect_language(audio, ["pl","uk"]) == ("uk", 0.3)
def test_detect_language_empty_allowed_raises(): ...
```

**Interfaces:** Produces: `WhisperEngine.{from_settings, transcribe, iter_transcribe, detect_language}`, `VadProfile`, `FILE_VAD`, `STREAMING_VAD`, `Segment(start,end,text)`, `TranscriptionResult`.

- [ ] **Step 1:** Тести → **Step 2:** FAIL → **Step 3:** реалізація → **Step 4:** PASS → **Step 5:** Commit `feat(asr): lazy whisper segments and restricted language detection`.

### Task 14: Kokoro ONNX + G2P, фабрика синтезаторів (лише onnx)

**Base:** SP5 Task 11 (рядки 2583–2906) без змін. З SP5 Task 12 (рядки 2910–3126) — **лише** `tts/engines.py`.

**Deltas (Task 12):**
- `kokoro_torch.py` не створювати;
- `create_synthesizer(engine, …)` приймає тільки `"onnx"`, для будь-чого іншого → `ValueError("engine 'torch' is deferred")`;
- `DEFAULT_TTS_ENGINE = "onnx"`;
- тести torch-гілки не писати; тест на `ValueError` для `"torch"` додати.

**Interfaces:** Produces:
- `KokoroG2P`, `lang_code_for_voice`, `UnknownVoice`, `VOICE_RE`;
- `KokoroOnnxSynthesizer(model_dir, device, compute_type, threads)` з `.sample_rate`, `.voices()`, `.synthesize(text, voice, speed) -> Iterator[np.ndarray]`;
- `create_synthesizer(engine, model_dir, device, compute_type, threads)`, `DEFAULT_TTS_ENGINE`.

- [ ] **Step 1:** Base з deltas. **Step 2:** PASS. **Step 3:** Commit `feat(tts): kokoro onnx engine`.

---

## Phase C — rt-asr

### Task 15: ASR settings і backend-и (fake + faster-whisper на WhisperEngine)

**Base:** SP1 Task 10 (рядки 2077–2357) + ASR-частина SP5 Task 12a (Step 3, рядок 3194).

**Deltas:**
- **`AsrSettings`:**
  - **лишити:** `language`, `beam_size`, `vad_filter`, `max_upload_mb` (default **100**, межі 1…1000);
  - **видалити WS-поля:** `vad_threshold`, `min_speech_ms`, `endpoint_silence_ms`, `max_utterance_s`, `preroll_ms`, `partials`, `partial_interval_ms`, `max_sessions`;
  - **додати:**

    | Поле | Env | Default | Межі |
    |---|---|---|---|
    | `languages: tuple[str, ...]` | `RT_ASR_LANGUAGES` | `pl,uk,ru,en` | кожен — ISO 639-1 з таблиці `normalize_language` |
    | `chunk_seconds: float` | `RT_ASR_CHUNK_SECONDS` | 600 | 30…900 |
    | `chunk_lookback_seconds: float` | `RT_ASR_CHUNK_LOOKBACK_SECONDS` | 30 | 5…120 |
    | `realtime_max_seconds: float` | `RT_ASR_REALTIME_MAX_SECONDS` | 30 | 5…120 |

  - `TUNABLE_FIELDS += {language, languages, beam_size, vad_filter, max_upload_mb, chunk_seconds, chunk_lookback_seconds, realtime_max_seconds}`;
  - `ENV_KEYS` для всіх полів.
- **Протокол `AsrBackend`:**
  ```python
  def transcribe(self, audio: np.ndarray, opts: DecodeOptions) -> Transcription
  def transcribe_iter(self, audio: np.ndarray, opts: DecodeOptions) -> Iterator[Segment]   # opts.language required
  def detect_language(self, audio: np.ndarray, allowed: Sequence[str]) -> str
  ```
- **`FakeAsrBackend`:**
  - нові параметри `detected: str = "uk"` і `segment_seconds: float = 10.0`;
  - `transcribe_iter` віддає по одному `Segment` на кожні `segment_seconds` аудіо, з `decode_delay` перед кожним;
  - `detect_language` повертає `detected`, якщо він у `allowed`, інакше `allowed[0]`.
- **`FasterWhisperBackend`:** за SP5 Task 12a Step 3 (через `WhisperEngine`, `prepare_model`), плюс делегування `transcribe_iter` → `engine.iter_transcribe`, `detect_language` → `engine.detect_language(...)[0]`.
- `VAD_PARAMETERS` видалити (SP5 12a).
- **Залежності:** `pysilero-vad` не потрібен сервісу (WS відкладено).

**Extra tests:**
```python
def test_asr_settings_defaults(): s = AsrSettings.from_env({}); assert s.languages == ("pl","uk","ru","en") and s.chunk_seconds == 600 and s.max_upload_mb == 100
def test_chunk_seconds_bounds(): with pytest.raises(SettingsError): AsrSettings.from_env({"RT_ASR_CHUNK_SECONDS": "10"})
def test_fake_transcribe_iter_segments(): 25 s audio, segment_seconds=10 → 3 segments
def test_fake_detect_language_respects_allowed(): FakeAsrBackend(detected="ru").detect_language(a, ["pl","uk"]) == "pl"
```

**Interfaces:** Produces: `AsrSettings`, `DecodeOptions`, `Segment(start,end,text)`, `Transcription`, `AsrBackend`, `FakeAsrBackend`, `FasterWhisperBackend`, `make_backend(settings)`, `normalize_language`.

- [ ] **Step 1:** Тести → **Step 2:** FAIL → **Step 3:** реалізація → **Step 4:** PASS → **Step 5:** Commit `feat(asr): settings and backends with segment iteration`.

### Task 16: Розбиття довгого аудіо на сервері

**Files:**
- Create: `src/services/asr/chunking.py`
- Test: `tests/services/test_svc_asr_chunking.py`

**Interfaces:**
- Consumes: `plan_chunks` (Task 9).
- Produces:
  ```python
  def speech_spans(audio: np.ndarray, sample_rate: int = 16000) -> list[tuple[int, int]]
      # lazy: from faster_whisper.vad import VadOptions, get_speech_timestamps
      # VadOptions(threshold=0.5, min_silence_duration_ms=500, speech_pad_ms=200)
  def split_for_transcription(audio: np.ndarray, chunk_seconds: float, lookback_seconds: float,
                              *, spans_fn=speech_spans) -> list[tuple[int, int]]
  ```

- [ ] **Step 1: Тести**
```python
def test_short_audio_not_split(): assert split_for_transcription(np.zeros(16000*20, np.float32), 600, 30, spans_fn=lambda a: []) == [(0, 320000)]
def test_split_uses_pause_from_spans():
    a = np.zeros(16000*1300, np.float32)
    spans = [(0, 16000*590), (16000*595, 16000*1300)]
    assert split_for_transcription(a, 600, 30, spans_fn=lambda _: spans)[0][1] == 16000*590 + (16000*5)//2
def test_faster_whisper_not_imported_at_module_import(): ...  # 'faster_whisper' not in sys.modules after import chunking
```
- [ ] **Step 2:** FAIL. **Step 3:** Реалізація. **Step 4:** PASS. **Step 5:** Commit `feat(asr): server-side chunk planning`.

### Task 17: `POST /v1/audio/transcriptions` з пріоритетом, шматками і runtime config; app; entrypoint

**Base:** SP1 Task 11 (рядки 2361–2606).

**Deltas:**
- **Сигнатури:** `build_http_router(config: RuntimeConfig[AsrSettings], runtime: ServiceRuntime)`. `create_app(settings=None, backend_factory=None, lifespan_hooks=(), state_path=None)` створює `RuntimeConfig(AsrSettings, os.environ, state_path or settings.config_state)`, викликає `restore()` і передає `config` у `create_service_app`. `on_reload` = `runtime.reload(lambda: make_backend(cfg.current()))`.
- **Читання налаштувань:** **кожен запит** читає `s = config.current()`. Ліміт `s.max_upload_mb` рахується в момент запиту, а не під час побудови router.
- **Нові form-поля:**
  - `chunk_seconds: float | None` — `None` → `s.chunk_seconds`; поза 30…900 → 400 `invalid_request`;
  - `priority: str = "auto"` — `auto|realtime|batch`, інше → 400 `invalid_request`.
- **Потік обробки:**
  1. `audio = decode_audio_file(...)`; `dur = len(audio)/16000`.
  2. `prio = realtime` якщо (`priority=="realtime"`) або (`priority=="auto"` і `dur <= s.realtime_max_seconds`), інакше `batch`. Пріоритет worker: `REQUEST` для realtime, `BATCH` для batch.
  3. **Мова:**
     - `language = normalize_language(form.language) or s.language`;
     - якщо `None` → `await runtime.run(backend.detect_language, audio[:16000*30], s.languages, priority=<prio worker>)`;
     - явна мова поза `s.languages` дозволена (запит перемагає).
  4. **realtime:** `await runtime.run(backend.transcribe, audio, opts, priority=Priority.REQUEST)`.
  5. **batch:**
     - `bounds = await asyncio.to_thread(split_for_transcription, audio, chunk, s.chunk_lookback_seconds)`;
     - для кожного `(a, b)`: `segs = await runtime.run_iter(backend.transcribe_iter, audio[a:b], opts_i, priority=Priority.BATCH, cancel=cancel)`;
     - зсунути `start/end` на `a/16000`;
     - `opts_i.prompt` = `form.prompt` для першого шматка, далі останні ≤ 200 символів тексту попереднього;
     - текст = `" ".join(seg.text.strip())`;
     - між шматками: `if await request.is_disconnected(): cancel.set(); return` (відповіді немає).
  6. `filter_repetitions` як у base. `verbose_json` додає `"chunks": len(bounds)` (1 для realtime).

**Extra tests** (`tests/services/test_svc_asr_http.py`, `FakeAsrBackend`):
```python
def test_auto_priority_short_is_realtime(): ...   # 5 s wav → fake.transcribe called, transcribe_iter not called
def test_auto_priority_long_is_batch_and_chunked(): # 70 s wav, chunk_seconds=30 → verbose_json chunks==3, segment starts increase monotonically
def test_invalid_priority_and_chunk_seconds_400(): ...
def test_language_detected_among_allowed(): # FakeAsrBackend(detected="ru"), POST /config languages=["pl","uk"] → response language=="pl"
def test_runtime_config_changes_upload_limit(): # POST /config max_upload_mb=1 → 2 MB wav → 413
def test_realtime_request_overtakes_running_batch():
    # fake decode_delay=0.3 per segment, 120 s batch (12 segments) started in a thread via TestClient;
    # after 0.5 s a 3 s realtime POST returns 200 while the batch call is still running
def test_config_endpoint_mounted_and_reload_swaps_backend(): # POST /config {"values":{"model":"x"}} → reloaded True, /status model id "x"
```

**Interfaces:** Produces: `asr/http.py: build_http_router(config, runtime)`, `asr/app.py: create_app(...)`, `asr/__main__.py: main()` (entry point `rt-asr`).

- [ ] **Step 1:** Тести (base, адаптовані під `config`, + extra) → **Step 2:** FAIL → **Step 3:** реалізація → **Step 4:** PASS → **Step 5:** Commit `feat(asr): transcriptions with priority, chunking and runtime config`.

---

## Phase D — rt-tts

### Task 18: TTS: голоси (лише англійські), settings, кодування, backend-и

**Base:** SP1 Task 15 (рядки 3659–4000) + TTS-частина SP5 Task 12a (Step 4, рядок 3196).

**Deltas:**
- **`voices.py`:**
  - `KOKORO_VOICES` — лише 28 голосів `a*`/`b*` (af ×11, am ×9, bf ×4, bm ×4; список узяти з SP5 registry `voices/{af,am,bf,bm}_*.bin`);
  - `LANGUAGE_BY_PREFIX = {"a": "en-us", "b": "en-gb"}`.
- **`TtsSettings`:**
  - поле `engine` (`RT_TTS_ENGINE`, лише `onnx`);
  - `TUNABLE_FIELDS += {voice, speed, sample_rate, max_chars}`;
  - `RELOAD_FIELDS += {engine}`;
  - `ENV_KEYS` для всіх полів;
  - `RT_DEVICE=rocm` → `SettingsError`.
- **`KokoroBackend`:** за SP5 12a — `synth_factory=create_synthesizer`, `prepare=prepare_model`, `voices()` = перетин каталогу з `synth.voices()`.
- **Тест** `test_kokoro_backend_unsupported_language` з `jf_alpha` видалити.

**Extra tests:**
```python
def test_catalog_is_english_only(): assert len(KOKORO_VOICES) == 28 and all(v[0] in "ab" for v in KOKORO_VOICES)
def test_tts_rocm_rejected(): with pytest.raises(SettingsError): TtsSettings.from_env({"RT_DEVICE": "rocm"})
```

**Interfaces:** Produces: `TtsSettings`, `KOKORO_VOICES`, `TtsBackend`, `FakeTtsBackend`, `KokoroBackend`, `make_backend`, `encoding.{wav_bytes, pcm_bytes}`.

- [ ] **Step 1:** Тести → **Step 2:** FAIL → **Step 3:** реалізація → **Step 4:** PASS → **Step 5:** Commit `feat(tts): english voices, settings and kokoro onnx backend`.

### Task 19: TTS HTTP, app, entrypoint з runtime config

**Base:** SP1 Task 16 (рядки 4004–4357).

**Deltas:**
- `build_http_router(config: RuntimeConfig[TtsSettings], runtime)`. Defaults (`voice`, `speed`, `sample_rate`, `max_chars`) читаються з `config.current()` на кожен запит (base 4228–4237).
- `create_app(...)` — як в ASR (Task 17): `RuntimeConfig` + `restore()` + `/config` + `on_reload`.
- **Тести base:**
  - `test_voice_catalog` і `test_voices_endpoint` очікують 28 голосів;
  - у `test_validation_errors` `jf_alpha` → `unknown_voice`.

**Extra tests:**
```python
def test_config_voice_default_applies_without_restart(): # POST /config {"values":{"voice":"am_adam"}} → speech without voice uses am_adam (fake records voice)
```

**Interfaces:** Produces: `tts/http.py: build_http_router(config, runtime)`, `tts/app.py: create_app(...)`, `tts/__main__.py: main()` (entry point `rt-tts`).

- [ ] **Step 1:** Тести → **Step 2:** FAIL → **Step 3:** реалізація → **Step 4:** PASS → **Step 5:** Commit `feat(tts): speech endpoint with runtime config`.

### Task 20: Lazy imports, slow-тести, фінальна перевірка сервісів

**Base:** SP1 Task 17 (рядки 4361–4521) і SP1 Task 18 (рядки 4525–4570).

**Deltas:**
- **Task 17:**
  - прибрати MT-імпорти і `mt_app` (4390–4391, 4397), а також `test_svc_mt_real.py`;
  - ручна перевірка «add import torch» → `tts/kokoro_backend.py`;
  - очікувані лічильники — 2 slow.
- **Новий slow-тест** `tests/services/test_svc_asr_real.py::test_long_audio_chunked_real`:
  - реальна `whisper-small` на CPU;
  - 70 с (повторений короткий WAV);
  - `chunk_seconds=30` → `chunks == 3`, текст непорожній.
- **Task 18:** smoke лише `rt-asr` і `rt-tts` (`RT_ASR_BACKEND=fake`, `RT_TTS_BACKEND=fake`), плюс `curl /config`.

- [ ] **Step 1:** Base з deltas.
- [ ] **Step 2:** `nix develop -c pytest tests/services tests/segmentation -q` → усі PASS (slow skipped).
- [ ] **Step 3:** `RT_SLOW_TESTS=1 nix develop -c pytest -m slow tests/services -q` → PASS (потрібна мережа для моделей).
- [ ] **Step 4:** Commit `test(services): lazy imports and slow model tests`.

---

## Phase E — Nix: модуль, бандли, варіанти

### Task 21: `nixosModules.rt-speech` (asr, tts) + eval-перевірка

**Base:** SP2 Task 2 (рядки 521–862).

**Deltas:**
- Прибрати `specificOptions.mt` (733–738) і «mt» з описів (529, 671).
- Тести `rocm` і `deployed` (555–556, 593–610) перевести на `asr` (`/var/lib/rt-asr/current`).
- **Asr-специфічні опції:**
  - лишити `computeType`, `language`, `beamSize`, `vad`;
  - **додати** `languages` (`listOf str`, default `[ "pl" "uk" "ru" "en" ]`) і `chunkSeconds` (`nullOr float`, default `null` → не емітиться);
  - `envLines` емітує `RT_ASR_LANGUAGES=pl,uk,ru,en`, а `RT_ASR_CHUNK_SECONDS` — лише якщо значення не `null`.
- **Спільна опція** `configState` (`nullOr str`, default `"${stateDir}/runtime-config.json"`) → `RT_CONFIG_STATE`.
- **Порівняння з llama** (перевірити, що збережено): `Environment = envLines`; `EnvironmentFile = "-${deployRsPath}/env"`; `ExecStart` з `${deployRsPath}/bin/rt-<svc>`, якщо `deployRsPath != ""`; `StateDirectory`; system user.

**Extra eval tests:**
- `testConfigStateDefault` — `RT_CONFIG_STATE=/var/lib/rt-tts/runtime-config.json` у `Environment` для tts;
- `testAsrLanguages` — рядок `RT_ASR_LANGUAGES=pl,uk,ru,en`.

**Interfaces:** Produces: `nixosModules.rt-speech` → `services.rtSpeech.{asr,tts}.{enable, package, deployRsPath, host, port, device, model, modelDir, configState, apiTokenFile, openFirewall, firewallInterfaces, logLevel, rocmGfxVersion, memoryDenyWriteExecute, extraEnv}` + asr `{computeType, language, languages, beamSize, vad, chunkSeconds}` + tts `{voice, speed, sampleRate}`.

- [ ] **Step 1:** Тести (eval) → **Step 2:** FAIL → **Step 3:** модуль → **Step 4:** `nix build .#checks.x86_64-linux.nixos-eval-gpu -L` PASS → **Step 5:** Commit `feat(nix): rt-speech NixOS module (asr, tts)`.

### Task 22: VM-тест модуля

**Base:** SP2 Task 3 (рядки 864–1035).

**Deltas:**
- `genAttrs [ "asr" "tts" ]`.
- Сценарій deploy-rs (base `mtDeployed`, 927–930, 952–957, 1004–1011) переписати на **tts**:
  - `deployRsPath=/var/lib/rt-tts/current`;
  - env-файл з `RT_TTS_VOICE=am_adam`;
  - перевірити, що `/status` віддає `am_adam` як default;
  - порт 8093.
- `fake-service.py` отримує `GET/POST /config`: зберігає в пам'яті `{version, values}` і пише в `RT_CONFIG_STATE`. VM-subtest:
  1. `POST /config` → `systemctl restart rt-tts`;
  2. `GET /config` показує те саме значення (персистентність через `StateDirectory`).

- [ ] **Step 1:** Base з deltas. **Step 2:** `nix build .#checks.x86_64-linux.nixos-rt-speech -L` → PASS. **Step 3:** Commit `test(nix): VM test for rt-speech module`.

### Task 23: Бандли (cpu), overlay, `packages.rt-*`, варіанти GPU/ROCm

**Base:** SP2 Task 4 (рядки 1037–1305) і SP2 Task 5 (рядки 1307–1379).

**Deltas:**
- **Передумова:** у git є `src/services/{asr,tts}/__main__.py`.
- **`sources.nix`:** `speechSrc` = `src/__init__.py` + `src/services` + **`src/segmentation`**.
- **`deps.nix`:**
  - без рядка `mt`;
  - `asr = [ faster-whisper ]` (без `pysilero-vad`);
  - `tts = [ onnxruntime kokoro enCoreWebSm ]`;
  - `httpx` прибрати з common (воркер SP4 відкладено).
- Без `mt = variants …` (1271) і без `"mt"` у `flattenRt` (1084).
- **Overlay-тест** (1061, 1070–1074) — на `asr`: `pkgs.rtSpeech.asr.gpu` існує; `envLines "asr"` містить `RT_PORT=8091`.
- Прибрати `rt-mt-cpu` (1097, 1282, 1297).
- **Task 5:** цикли без `rt-mt-*`; ROCm-збірка (1361–1364) — `rt-asr-rocm`.

**Interfaces:** Produces:
- `packages.<x86_64-linux>.{rt-asr-cpu, rt-asr-gpu, rt-asr-rocm, rt-tts-cpu, rt-tts-gpu}`;
- `overlays.default` → `pkgs.rtSpeech.{asr.{cpu,gpu,rocm}, tts.{cpu,gpu}, mkBundle, lib}`;
- кожен бандл має `bin/rt-<svc>`, `bin/rt-models`, `share/rt-speech/info.json`.

- [ ] **Step 1:** Base з deltas (overlay-тест першим).
- [ ] **Step 2:** `nix build .#rt-asr-cpu .#rt-tts-cpu .#checks.x86_64-linux.overlay -L` → OK. Smoke: `RT_ASR_BACKEND=fake RT_PORT=18091 ./result/bin/rt-asr &` → `curl -sf localhost:18091/health` за ≤ 10 с.
- [ ] **Step 3:** Task 5 base: drvPath/кеш-перевірки для `rt-asr-gpu`, `rt-tts-gpu`; збірка `rt-asr-rocm`. Результат записати в коміт-повідомлення.
- [ ] **Step 4:** Commit `build(nix): speech bundles, overlay and variants`.

---

## Phase F — Edge

### Task 24: EdgeSettings, конфіг і клієнти (ASR, TTS, LLM)

**Base:** SP2 Task 6 (рядки 1381–1757) і SP2 Task 7 (рядки 1759–2255).

**Deltas (Task 6) — `src/core/runtime.py`:**
```python
@dataclass(frozen=True)
class LlmSettings:
    django_urls: tuple[str, ...] = ()        # lease mode (primary)
    django_token_file: str | None = None     # user DRF token
    service: str | None = None               # preferred worker name; None = Django picks
    lease_ttl_s: int = 60
    direct_url: str | None = None            # dev mode: local llama-server / any OpenAI-compatible
    direct_token_file: str | None = None
    model: str = "local"
    max_tokens_cap: int = 512
    temperature: float = 0.1
    context_pairs: int = 3
    timeout_s: float = 15.0
    def source(self) -> Literal["lease", "direct", "none"]   # django_urls → lease; elif direct_url → direct; else none

@dataclass(frozen=True)
class SegmentationSettings:
    translate: SegmenterConfig = SegmenterConfig.translate()
    transcribe: SegmenterConfig = SegmenterConfig.transcribe()

@dataclass(frozen=True)
class EdgeSettings:
    mode: Literal["auto", "transcribe", "translate"] = "auto"
    asr_urls: tuple[str, ...] = ("http://127.0.0.1:8091",)
    tts_urls: tuple[str, ...] = ()
    llm: LlmSettings = LlmSettings()
    api_token_file: str | None = None
    source_language: Literal["auto", "pl", "uk", "ru", "en"] = "auto"
    timeouts: Timeouts = Timeouts(connect_s=3.0, asr_s=30.0, tts_s=30.0)
    segmentation: SegmentationSettings = SegmentationSettings()
    managed: frozenset[str] = frozenset()
    def effective_mode(self) -> Literal["transcribe", "translate"]   # auto → translate iff llm.source()!="none" and tts_urls
    def validate(self) -> list[str]
        # translate requires llm.source()!="none" and tts_urls; lease requires django_token_file;
        # asr_urls non-empty; segmentation.*.validate() ok; transcribe.hard_max_ms <= 900_000; 15<=lease_ttl_s<=300
```
- **`resolve_edge_settings(cfg: Mapping, environ: Mapping) -> EdgeSettings`:**
  - **config.yml:**
    - `services.{mode, asr_urls, tts_urls, api_token_file, source_language}`;
    - `services.llm.{django_urls, django_token_file, service, lease_ttl_s, direct_url, direct_token_file, model, max_tokens_cap, temperature, context_pairs, timeout_s}`;
    - `segmentation.translate.<поле SegmenterConfig>`;
    - `segmentation.transcribe.{chunk_min_s, chunk_max_s, soft_silence_ms, lookback_ms, keep_silence_ms}`.
  - **env:** `RT_EDGE_MODE`, `RT_EDGE_ASR_URLS`, `RT_EDGE_TTS_URLS`, `RT_EDGE_DJANGO_URLS` (списки через кому), `RT_EDGE_DJANGO_TOKEN_FILE`, `RT_EDGE_LLM_SERVICE`, `RT_EDGE_LLM_DIRECT_URL`, `RT_EDGE_LLM_DIRECT_TOKEN_FILE`, `RT_EDGE_LLM_MODEL`, `RT_EDGE_API_TOKEN_FILE`.
  - **пріоритет:** env > config.yml > defaults.
  - **нормалізація:** кінцевий `/` в URL відкидається.
  - **зворотної сумісності зі старими ключами немає:** легасі видалено в Task 2b.
- `config/default.yml` переписати під ці ключі з коментарями.

**Deltas (Task 7):**
- **Видалити:** `src/clients/mt.py`, `build_clients`/`SpeechClients` і всі тести MtClient.
- **`AsrClient.transcribe`:**
  ```python
  def transcribe(self, audio_int16, sample_rate=16000, language=None, prompt=None, *,
                 priority: str = "realtime", chunk_seconds: float | None = None,
                 timeout_s: float | None = None) -> AsrResult
  ```
- **Новий `src/clients/llm.py`:**
  ```python
  LANGUAGE_NAMES = {"pl": "Polish", "uk": "Ukrainian", "ru": "Russian"}
  SYSTEM_PROMPT = ("You are a professional interpreter. Translate the user's {lang} speech transcript "
                   "into natural English. Output only the English translation, nothing else. "
                   "Fix obvious speech-recognition errors using context.")
  class LlmTarget(Protocol):
      def current(self) -> tuple[str, str]          # (base_url, bearer token)
      def invalidate(self, kind: str) -> None       # kind: "auth" | "unreachable" | "timeout" | "busy"
  class StaticTarget:                                # dev mode
      def __init__(self, url: str, token_file: str | None) -> None
  class LlmClient:
      def __init__(self, target: LlmTarget, settings: LlmSettings, *, transport=None) -> None
      def translate(self, text: str, source_lang: str, context: Sequence[tuple[str, str]] = ()) -> str
      def status(self) -> dict                       # GET {base_url}/status з тим самим bearer
  ```
  Поведінка `translate`:
  - `source_lang == "en"` → повертає `text` без HTTP;
  - мова поза `LANGUAGE_NAMES` → `ValueError`;
  - тіло запиту — точно за spec §6.2 (`max_tokens = min(cap, 32 + 3*len(text.split()))`, `chat_template_kwargs={"enable_thinking": False}`);
  - відповідь: `<think>…</think>` видаляється regex `(?s)<think>.*?</think>`, потім `strip()`; порожньо → `ServiceError("llm", "http")`;
  - **повтор:** 401/403 → `invalidate("auth")`; unreachable/timeout → `invalidate(kind)`; 503 → `invalidate("busy")`. Після `invalidate` — **один** повтор із новим `current()`, далі `ServiceError`.
- **Фабрики:**
  ```python
  @dataclass(frozen=True)
  class ClientFactories:
      asr: Callable[[str], AsrClient]
      tts: Callable[[str], TtsClient]
  def client_factories(settings: EdgeSettings, transport=None) -> ClientFactories
  ```

**Extra tests:**
```python
# tests/test_edge_settings.py
def test_auto_mode_derivation():  # nothing → transcribe; django_urls+tts → translate; direct_url+tts → translate
def test_env_lists_override_config():  # RT_EDGE_ASR_URLS="http://a:1,http://b:2/" → ("http://a:1","http://b:2")
def test_lease_requires_django_token_file():  # django_urls set, no token file → validate() non-empty
def test_transcribe_chunk_max_over_900s_invalid():  # chunk_max_s: 1200 → validate() non-empty
# tests/test_clients.py
def test_llm_request_body():  # system mentions "Ukrainian"; context as user/assistant pairs; enable_thinking False; Authorization "Bearer sha256:abc" from target
def test_llm_strips_think_block():  # "<think>x</think> Hello" → "Hello"
def test_llm_empty_content_raises_service_error(): ...
def test_llm_401_invalidates_auth_and_retries_once():  # 1st 401, 2nd 200 → result; target.invalidate called with "auth"; 2×401 → ServiceError(kind="auth")
def test_llm_unreachable_invalidates_and_retries_on_new_target(): ...
def test_llm_english_passthrough_no_http(): ...
def test_static_target_reads_token_file(tmp_path): ...
def test_asr_client_sends_priority_and_chunk_seconds(): ...
```

**Interfaces:** Produces: `EdgeSettings`, `LlmSettings`, `SegmentationSettings`, `resolve_edge_settings`, `get_runtime_config`, `reset_runtime_config`; `ServiceError`, `ServiceClient`, `AsrClient`, `AsrResult`, `TtsClient`, `LlmTarget`, `StaticTarget`, `LlmClient`, `ClientFactories`, `client_factories`.

- [ ] **Step 1:** Тести → **Step 2:** FAIL → **Step 3:** реалізація → **Step 4:** `nix develop -c pytest tests/test_edge_settings.py tests/test_clients.py -q` PASS → **Step 5:** Commit `feat(edge): settings, llm client with target abstraction`.

### Task 25: LeaseKeeper — lease LLM-воркера через Django

**Files:**
- Create: `src/clients/lease.py`
- Test: `tests/test_lease.py` (фейковий Django на `httpx.MockTransport`)

**Протокол:** spec `docs/superpowers/specs/2026-10-22-llm-worker-lease-design.md` §2.3 (шляхи, тіла, коди) і §5.

**Interfaces:**
- Consumes: `LlmSettings`, `LlmTarget`, `ServiceError` (Task 24).
- Produces:
  ```python
  @dataclass(frozen=True)
  class Lease:
      id: str; django_url: str; service_name: str; base_url: str; current_model: str
      worker_token: str; ttl_s: int; heartbeat_interval_s: int; expires_at: datetime
  class LeaseError(Exception):        # .status_code, .detail
  class DjangoLeaseApi:
      def __init__(self, django_url: str, token_file: str, *, timeout_s: float = 10.0, transport=None) -> None
      def acquire(self, client_name: str, service: str | None, ttl_s: int) -> Lease   # 201; 409/503 → LeaseError
      def heartbeat(self, lease: Lease, ttl_s: int | None = None) -> Lease           # 200; 404/410 → LeaseError
      def release(self, lease: Lease) -> None                                        # 204/404 → ok
  class LeaseKeeper:                  # implements LlmTarget
      def __init__(self, settings: LlmSettings, client_name: str, *,
                   api_factory: Callable[[str], DjangoLeaseApi] | None = None,
                   clock: Callable[[], float] = time.monotonic, sleep=time.sleep) -> None
      def start(self) -> Lease              # blocking acquire (primary), then mirrors, then heartbeat thread
      def stop(self) -> None                # stop thread, release all (errors logged)
      def current(self) -> tuple[str, str]  # (lease.base_url, lease.worker_token); not started → ServiceError("llm","unreachable")
      def invalidate(self, kind: str) -> None
      def snapshot(self) -> dict            # {"service","base_url","model","expires_at","django","mirrors":[...],"error"}
  ```

**Поведінка:**
- **`client_name`** = `f"rt-edge@{socket.gethostname()}"`.
- **`start()`:**
  - пробує `django_urls` по черзі. Перший успішний `acquire` дає основний lease;
  - на решті Django — `acquire(service=<primary.service_name>)`. Помилки 409/503/мережа лише логуються, дзеркала не обов'язкові;
  - якщо жоден Django не дав lease → `ServiceError("llm", "unreachable")` з `detail`.
- **Heartbeat-потік** (daemon) кожні `heartbeat_interval_s` викликає `heartbeat` для основного lease і дзеркал:
  - основний повернув новий `worker_token` → оновити атомарно;
  - 404/410 на основному → повний re-acquire (новий воркер), дзеркала перестворюються;
  - мережева помилка → повтор на наступному тіку; після `expires_at` → re-acquire.
- **`invalidate(kind)`:**
  - `"auth"` → синхронний heartbeat основного (свіжий токен);
  - `"unreachable"`/`"timeout"`/`"busy"` → release усіх і re-acquire з `service=None`. Django обере інший вільний воркер, а поточний уже звільнено.
- **Потокобезпечність:** `current()`/`invalidate()` викликаються з pipeline-потоку, heartbeat — з власного; стан під `threading.Lock`.

- [ ] **Step 1: Тести**
```python
def test_acquire_on_first_reachable_django_then_mirrors():
    # django A unreachable, B returns 201 lease(service="w1"), C gets acquire(service="w1") → 201
    # keeper.current() == ("http://w1:8080", "sha256:t1"); C called with body service=="w1"
def test_mirror_failure_is_not_fatal(): # C returns 409 → start() still returns lease
def test_no_django_available_raises(): # all unreachable → ServiceError(kind="unreachable")
def test_heartbeat_refreshes_worker_token(): # heartbeat returns worker_token "sha256:t2" → current()[1]=="sha256:t2"
def test_heartbeat_410_triggers_reacquire(): # → new lease w2; current()[0]=="http://w2:8080"
def test_invalidate_auth_heartbeats_synchronously(): ...
def test_invalidate_unreachable_releases_and_reacquires(): # DELETE called for old lease, then POST acquire with service None
def test_stop_releases_all(): # DELETE on primary and mirrors; stop() idempotent
def test_request_bodies_match_spec(): # POST /api/llm/leases/ {"client_name","service","ttl_s"}; header "Authorization: Token <file content>"
```
Heartbeat-потік у тестах керується через інжектований `sleep`/`clock`, без реальних пауз.
- [ ] **Step 2:** FAIL. **Step 3:** Реалізація. **Step 4:** PASS. **Step 5:** Commit `feat(edge): django lease keeper for llm workers`.

### Task 26: EndpointPool (rt-asr, rt-tts) і монітор стану

**Base:** SP2 Task 8 (рядки 2257–2436) для монітора.

**Files:**
- Create: `src/clients/pool.py`
- Modify: `src/clients/health.py`, `src/clients/__init__.py`
- Test: `tests/test_endpoint_pool.py`, `tests/test_health_monitor.py`

**Interfaces:**
- Consumes: `ClientFactories`, `ServiceError`, `LlmClient` (Task 24); `LeaseKeeper.snapshot` (Task 25).
- Produces:
  ```python
  class EndpointPool(Generic[C]):
      def __init__(self, service: str, urls: Sequence[str], factory: Callable[[str], C], *,
                   down_s: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None
      def update_status(self, url: str, *, ready: bool, pending: int | None) -> None
      def pick(self) -> str          # ready & not down, min pending (None=+inf), tie → list order; else first not-down; else ServiceError(service,"unreachable")
      def pin(self) -> str
      @property
      def pinned_url(self) -> str | None
      def call(self, fn: Callable[[C], T]) -> T   # pinned (pin() if none); retryable → mark down, re-pin, retry once
      def clients(self) -> dict[str, C]
      def close(self) -> None
  @dataclass(frozen=True)
  class SpeechPools:
      asr: EndpointPool[AsrClient]
      tts: EndpointPool[TtsClient] | None
      def by_name(self) -> dict[str, EndpointPool]
      def close(self) -> None
  def build_pools(settings: EdgeSettings, transport=None) -> SpeechPools
  ```
- **Retryable:** `kind in {"unreachable", "timeout"}` або (`kind == "http"` і `status_code == 503`).
- **Monitor deltas (SP2 Task 8):**
  - `ServiceHealthMonitor(pools: SpeechPools, llm: LlmClient | None, lease: LeaseKeeper | None, interval_s=5)`;
  - запис `{service, url, connected, ready, pending, model, device, error}`;
  - для `asr`/`tts` — кожен URL пулу через SP1 `/status` (`ready`, `pending`, `model.id`, `device`); після опитування — `pool.update_status(...)`;
  - для `llm` — один запис: `url` з `lease.snapshot()["base_url"]` (або direct URL); `llm.status()` → `ready = model.loaded`, `model = basename(model.path)`, `pending = None`; плюс `lease` = `lease.snapshot()`;
  - `snapshot()` → `dict[service, list[entry]]`.

- [ ] **Step 1: Тести**
```python
def test_pick_prefers_ready_min_pending():  # a: ready pending 3, b: ready pending 0 → "b"
def test_pick_tie_uses_list_order(): ...
def test_failover_repins_and_retries_once():
    # fn raises ServiceError(kind="unreachable") for url a, succeeds on b → result from b, pinned_url=="b", a down
def test_non_retryable_error_propagates_without_retry():  # kind "auth" → raised, calls==1
def test_down_endpoint_returns_after_down_s():  # fake clock +31 s → a eligible again
def test_no_endpoints_available_raises_unreachable(): ...
def test_monitor_llm_entry_from_lease_and_worker_status():  # status {"model":{"loaded":true,"path":"/m/q.gguf"}} → ready True, model "q.gguf", lease snapshot attached
def test_monitor_updates_pool(): ...
```
- [ ] **Step 2:** FAIL. **Step 3:** Реалізація. **Step 4:** PASS. **Step 5:** Commit `feat(edge): endpoint pools with failover and health monitor`.

### Task 27: Потоковий VAD → PauseSegmenter на edge

**Files:**
- Create: `src/audio/vad_stream.py`
- Test: `tests/test_vad_stream.py`

**Interfaces:**
- Consumes: `PauseSegmenter`, `SegmenterConfig`, `Segment`, `WINDOW_SAMPLES` (Task 9).
- Produces:
  ```python
  class VadModel(Protocol):
      def __call__(self, window_int16: np.ndarray) -> float: ...
  def make_vad() -> VadModel        # pysilero_vad.SileroVoiceActivityDetector; fallback energy RMS ≥ 0.008 → 1.0/0.0
  class VadStream:
      def __init__(self, vad: VadModel, cfg: SegmenterConfig, input_rate: int = 48000) -> None
      def feed_block(self, block: np.ndarray) -> list[Segment]   # float32 mono @ input_rate, будь-якої довжини
      def flush(self) -> Segment | None
      def reconfigure(self, cfg: SegmenterConfig) -> None
      def last_level(self) -> float                              # RMS останнього блоку, для UI
  ```
  Ресемплінг 48k → 16k — `scipy.signal.resample_poly(1, 3)` з переносом стану між блоками: залишок накопичується до повних вікон по 512 семплів.

- [ ] **Step 1: Тести**
```python
def test_blocks_of_any_size_produce_same_segments():  # same 48k signal fed as 1024-blocks vs 4000-blocks → identical segment bounds (±1 window)
def test_flush_after_stop_returns_tail():  # speech then stop → flush() not None
def test_reconfigure_switches_mode(): ...
def test_energy_fallback_when_silero_missing(monkeypatch): ...  # import error → energy vad
```
- [ ] **Step 2:** FAIL. **Step 3:** Реалізація. **Step 4:** PASS. **Step 5:** Commit `feat(edge): streaming VAD into pause segmenter`.

### Task 28: TranslationSystem на пулах, lease і сегментації

**Base:** SP2 Task 9 (рядки 2438–2900). Легасі-залежності вже видалено в Task 2b, тож `src/translation_system.py` переписується під новий конструктор.

**Deltas:**
- **Конструктор:**
  ```python
  TranslationSystem(settings: EdgeSettings, *, pools_factory=build_pools,
                    llm_target_factory: Callable[[LlmSettings], LlmTarget] = default_llm_target,
                    vad_stream_factory: Callable[[SegmenterConfig], VadStream] = lambda cfg: VadStream(make_vad(), cfg))
  def default_llm_target(llm: LlmSettings) -> LlmTarget | None   # lease → LeaseKeeper(llm, client_name); direct → StaticTarget; none → None
  ```
- **Захоплення:** `_pipeline_loop` подає кожен блок у `VadStream` з конфігом за `effective_mode()`. Логіки `MAX_CHUNK_SEC`, overlap 0.35 с і `_is_duplicate_stt` не існує.
- **`start()`:**
  - у режимі `translate`, якщо target — `LeaseKeeper`, викликати `target.start()` **до** запуску захоплення;
  - якщо lease не взято → `ServiceError` → `last_error = "llm: <detail>"`, старт не відбувається;
  - UI-подія `llm_lease` з `snapshot()`.
- **translate:**
  1. `pools.asr.call(lambda c: c.transcribe(seg.audio, language=lang_or_None, prompt=" ".join(last 3 recognized), priority="realtime"))`;
  2. мова = вибрана або визначена;
  3. `self._llm.translate(text, src, context=list(self._translation_context))`, де `_translation_context: deque[tuple[str, str]]` має `maxlen = llm.context_pairs`;
  4. `pools.tts.call(lambda c: c.synthesize(translation, voice, speed))`;
  5. `enqueue_playback(audio, sr)`.
- **transcribe:**
  - `pools.asr.call(lambda c: c.transcribe(seg.audio, language=…, priority="batch", timeout_s=max(60.0, len(seg.audio) / 16000)))`;
  - емітити `transcription_complete`;
  - lease **не** береться.
- **Помилки:**
  - `ServiceError` на будь-якому кроці → фраза пропускається, `last_error = f"{service}: {kind}"`, UI-подія `error`;
  - пайплайн продовжує роботу.
- **Нові публічні методи:**
  ```python
  def enqueue_playback(self, audio: np.ndarray, sample_rate: int) -> None   # extension point (engine TTS later)
  def apply_settings(self, settings: EdgeSettings) -> None
      # live: segmentation, language, llm prompt params; URLs/llm source/mode changed → stop-reconfigure-start
  ```
- **`stop()`:**
  1. `segment = vad_stream.flush()` → обробити його (у transcribe — обов'язково);
  2. дочекатися, доки `_chunk_queue` спорожніє (`join` з timeout `max(60, chunk_s)`);
  3. зупинити worker;
  4. `LeaseKeeper.stop()` (release);
  5. черга сегментів у transcribe не відкидає елементи (`maxsize=0`); у translate — `maxsize=5` з відкиданням найстаріших.
- **`get_stats()`:** `llm_*` (кількість, помилки, середня латентність), `pinned: {asr, tts}`, `lease: snapshot`.

**Extra tests** (`tests/test_translation_system.py`, фейкові пули, фейковий target/keeper і фейковий `VadStream`):
```python
def test_translate_flow_calls_asr_llm_tts_and_playback(): ...
def test_translate_start_acquires_lease_and_stop_releases(): ...
def test_translate_start_fails_cleanly_without_lease(): # keeper.start raises ServiceError → start returns False, last_error startswith "llm:"
def test_llm_context_carries_previous_pairs():  # 2nd call context == [(src1, tr1)]
def test_transcribe_mode_uses_batch_priority_scaled_timeout_and_no_lease(): ...
def test_stop_flushes_and_drains_transcribe_chunk():
    # fake VadStream.flush returns 300 s segment; stop() → asr called with it before stop returns; transcription_complete emitted
def test_service_error_skips_phrase_and_continues(): # llm raises → next segment still processed; last_error=="llm: http"
def test_apply_settings_reconfigures_segmenter_live(): ...
def test_enqueue_playback_plays_external_audio(): ...
```

**Interfaces:** Produces: `TranslationSystem(settings, *, pools_factory, llm_target_factory, vad_stream_factory)` з `.start() -> bool`, `.stop()`, `.apply_settings()`, `.enqueue_playback()`, `.get_stats()`, `.last_error`; `default_llm_target`.

- [ ] **Step 1:** Тести → **Step 2:** FAIL → **Step 3:** реалізація в `src/translation_system.py` → **Step 4:** PASS → **Step 5:** Commit `feat(edge): pipeline with lease-based llm translation and pause segmentation`.

### Task 29: DirectAdapter, контролер, `main.py`

**Base:** SP2 Task 10 (рядки 2902–3241).

**Deltas:**
- `VALID` (2925) — `mt_url` → `llm`;
- тест 2974 (`"mt"` у `last_error`) → `"llm"`;
- `reconfigure_services(settings)` викликає `TranslationSystem.apply_settings(settings)`;
- контролер віддає в UI `lease` зі `get_stats()`.

**Extra test:** `test_reconfigure_segmentation_does_not_restart_pipeline`.

- [ ] **Step 1:** Тести → **Step 2:** FAIL → **Step 3:** реалізація → **Step 4:** PASS → **Step 5:** Commit `feat(edge): adapter and controller on new pipeline`.

### Task 30: UI — статус сервісів, LLM/lease, сегментація

**Base:** SP2 Task 11 (рядки 3243–3612).

**Deltas:**
- **`SERVICES`** (3344): `{"asr": "ASR (rt-asr)", "llm": "Переклад (LLM)", "tts": "TTS (rt-tts)"}`.
  - Для `asr`/`tts` — агрегований статус і закріплений URL.
  - Для `llm` — ім'я воркера з lease, модель, `expires_at`, джерело (`lease`/`direct`).
- **`EndpointsDialog`:**
  - ASR і TTS URL-и — багаторядкові поля (один URL на рядок);
  - LLM: `django_urls` (багаторядкове), `django_token_file`, бажаний воркер `service` (порожньо = авто), `direct_url`, `direct_token_file`, `model`;
  - rt `api_token_file`;
  - режим: combo `auto|transcribe|translate`;
  - мова: combo `auto|pl|uk|ru|en`.
- **Вкладка «Сегментація»:**
  - transcribe: `chunk_min_s`, `chunk_max_s`;
  - translate: `soft_max_ms`, `hard_max_ms`, `end_silence_ms`.
  
  Значення зберігаються в `config.yml` через `ConfigManager.set()` і застосовуються одразу (`reconfigure_services`).
- Тести base (3285–3325) — під нові поля.

- [ ] **Step 1:** Тести → **Step 2:** FAIL → **Step 3:** реалізація → **Step 4:** PASS (`QT_QPA_PLATFORM=offscreen`) → **Step 5:** Commit `feat(ui): endpoints, llm lease and segmentation settings`.

### Task 31: Edge-пакет і HM-модуль

**Base:** SP2 Task 12 (рядки 3614–3716) і SP2 Task 13 (рядки 3718–3936).

**Deltas:**
- **Task 12:** прибрати MT (3673, 3679). `edgeSrc` містить `src/segmentation`: перевірити `nix build .#rt-edge` і `import src.segmentation` у wrapper-і.
- **Task 13 — опції `rt-translator`:**
  - `endpoints.asr` (`listOf str`, default `[ "http://127.0.0.1:8091" ]`);
  - `endpoints.tts` (`listOf str`, `[ ]`);
  - `llm`:
    ```nix
    llm = {
      djangoEndpoints = listOf str [ ];
      djangoTokenFile = nullOr str null;
      service = nullOr str null;
      directUrl = nullOr str null;
      directTokenFile = nullOr str null;
      model = str "local";
    };
    ```
  - `mode` (`nullOr (enum [ "transcribe" "translate" ])`, default `null` → `auto`);
  - `apiTokenFile`, `autostart`, `virtualSinks.enable`.
- **Env:** `RT_EDGE_ASR_URLS`, `RT_EDGE_TTS_URLS`, `RT_EDGE_DJANGO_URLS` (через кому, лише непорожні), `RT_EDGE_DJANGO_TOKEN_FILE`, `RT_EDGE_LLM_SERVICE`, `RT_EDGE_LLM_DIRECT_URL`, `RT_EDGE_LLM_DIRECT_TOKEN_FILE`, `RT_EDGE_LLM_MODEL`, `RT_EDGE_API_TOKEN_FILE`, `RT_EDGE_MODE`.
- **Assertions:**
  - `mode == "translate"` → (`llm.djangoEndpoints != [ ]` або `llm.directUrl != null`) і `endpoints.tts != [ ]`;
  - `llm.djangoEndpoints != [ ]` → `llm.djangoTokenFile != null`;
  - токен-файли не в `/nix/store`.
- Прибрати `endpoints.mt` і removed-option модулі легасі-опцій (base 3750, 3767, 3822, 3852, 3856, 3871–3872).

- [ ] **Step 1:** Тести `hm-edge` (base 3745–3784, оновлені) → **Step 2:** FAIL → **Step 3:** реалізація → **Step 4:** `nix build .#checks.x86_64-linux.{edge-pytest,hm-edge} -L` PASS → **Step 5:** Commit `feat(nix): rt-edge package and Home Manager module`.

---

## Phase G — Документація і реліз

### Task 32: Документ користувача і git-flake доказ

**Base:** SP2 Task 16 (рядки 4281–4365). Скрізь `rt-mt-cpu` → `rt-asr-cpu`.

**Files:**
- Create: `docs/USAGE.md` (українською), `scripts/gen_env_table.py`, `tests/test_gen_env_table.py`
- Modify: `README.md` (посилання на USAGE), `CLAUDE.md` (нова архітектура: `src/services`, `src/segmentation`, `src/clients` з lease, `nix/`, режими, порти; прибрати опис легасі)

`docs/USAGE.md` — структура строго за spec §9 (розділи 1–9). Обов'язковий вміст:
- **Таблиця env** `rt-asr`/`rt-tts`: ключ, default, клас (BOOTSTRAP/TUNABLE/RELOAD). Генерується `scripts/gen_env_table.py` з `ENV_KEYS`/`*_FIELDS`. Тест `test_gen_env_table` перевіряє, що вивід скрипта міститься в `docs/USAGE.md`.
- **`curl`-приклади `/config`:** GET, POST tunable (`chunk_seconds`), POST reload (`model`), reset.
- **LLM через lease:**
  - отримання DRF-токена (`POST /api/api-token-auth/`) у файл;
  - `services.llm.django_urls` / `service`;
  - перевірка lease вручну: `curl` acquire, heartbeat, delete — за spec lease §2.3;
  - що бачить UI;
  - примітка: API lease з'явиться в Django після виконання spec lease в swarm-nix; до того працює лише dev-режим.
- **Dev-режим без Django:** `nix run nixpkgs#llama-cpp -- llama-server -m <gguf> --port 8080 --api-key-file <file>` + `services.llm.direct_url`/`direct_token_file`.
- **Ключі `config.yml` edge** з прикладом для двох ПК: один `translate`, інший `transcribe`.
- **Локальний e2e:**
  1. `nix develop`;
  2. `RT_ASR_BACKEND=faster-whisper python -m src.services.asr`;
  3. `python -m src.services.tts`;
  4. `llama-server` (dev) або lease;
  5. `python -m src.main`.
- **Деплой:**
  - (a) Colmena-only: `services.rtSpeech.asr = { enable = true; package = pkgs.rtSpeech.asr.gpu; device = "cuda"; … }`;
  - (b) deploy-rs — покроковий список змін в інфрі:
    1. flake input `github:`;
    2. overlay;
    3. узагальнення `mkDeployProfiles.contractFor` і `mkEnvFile` (параметри `cfgPath`, `mkEnvLines`);
    4. `applications/services/rt-asr-<variant>.nix` через `mkApplication { name; serviceName = "rt-asr"; appDir = pkgs.rtSpeech.asr.<variant>; stateDir = "/var/lib/rt-asr"; healthCheck.command = lib.rtSpeech.healthCheckCommand "asr" cfg; }`;
    5. clusterops Service + `generate_infra --regenerate`;
    6. CUDA substituter;
    7. per-host `machines/<host>/modules/flake-services/rt-speech.nix`;
  - реліз: commit + push тут → `nix flake update realtime-translation` в інфрі → deploy.
- **Чек-лист e2e:**
  1. `/health` `rt-asr`/`rt-tts`, lease отримано (UI показує воркер);
  2. фраза українською → англійська озвучка в `rt_virtual_output`;
  3. поки триває сесія, воркер не бере WorkItem (`next()` → `paused/leased`); після stop — бере;
  4. 12-хвилинна транскрипція без втрат на `stop`;
  5. зміна `chunk_min_s` в UI без рестарту.

- [ ] **Step 1:** Тест `test_gen_env_table` → **Step 2:** FAIL → **Step 3:** скрипт + `USAGE.md` + README/CLAUDE.md → **Step 4:** PASS.
- [ ] **Step 5:** Git-flake доказ (base Step 5–6): `git clone` у тимчасову теку → `nix build <clone>#rt-asr-cpu`; після push — `nix build github:DohuyaMa/realtime-translation/<rev>#rt-asr-cpu`.
- [ ] **Step 6:** Commit `docs: usage and deployment guide`.

## Відкладено (не в цьому плані)

- WS-стрімінг ASR: SP1 Tasks 8, 9, 12; SP2 Task 14.
- Інфра swarm-nix (SP3) і серверна частина lease (spec `2026-10-22-llm-worker-lease-design.md`: Django `WorkerLease` + API + `next()`; llama: `pause_reason`, без idle-unload під lease, автозавантаження в chat) — окремі сесії в swarm-nix.
- Django/engine (SP4): воркери на tier 2, озвучка з engine на ПК (через `enqueue_playback`), пул TTS/ASR для engine.
- SP5 harness і калібрування (Tasks 1, 13–19, 21), ROCm spike (22), torch TTS.
