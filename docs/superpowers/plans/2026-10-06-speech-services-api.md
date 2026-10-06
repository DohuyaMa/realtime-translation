# Speech services API (ASR / MT / TTS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Три незалежні stateless FastAPI-сервіси `rt-asr` / `rt-mt` / `rt-tts` у `src/services/` зі спільним `src/services/common/`, фоновим завантаженням моделей, `/health`+`/status`, опційним bearer-токеном і fake-backend-ами для тестів.

**Architecture:** Один uvicorn-процес на сервіс; модель живе в процесі, всі звернення до неї серіалізуються одним inference-потоком із пріоритетною чергою (`InferenceWorker`), який async-хендлери викликають через `ServiceRuntime`. Важкі ML-бібліотеки імпортуються лише у фоновому `load()` після старту uvicorn. ASR має серверний VAD/endpointing (`Endpointer` + `StreamSession`) і партіали через periodic re-decode зростаючого буфера (latest-wins, низький пріоритет).

**Tech Stack:** Python 3.13, FastAPI 0.135 / Starlette, uvicorn 0.40, pydantic v2, numpy, scipy, soundfile, loguru, pysilero-vad 3.3, faster-whisper 1.2.1, transformers 5.5 (NLLB-200), kokoro 0.9.4; pytest + `fastapi.testclient`. Усе — з nixpkgs через `nix/devshell.nix` (SP2 Task 1, виконується до цього плану — overview §6).

**Spec:** `docs/superpowers/specs/2026-10-06-speech-services-api-design.md` (спирається на `docs/superpowers/specs/2026-10-06-distributed-speech-services-overview.md`).

## Global Constraints

- Nix-only: жодного `pip install`/venv; нові Python-залежності — лише в `nix/devshell.nix` (створює SP2 Task 1, який за overview §6 виконується **до** цього плану; якщо його ще немає — тимчасово в `flake-global/dev/devshell.nix`, SP2 Task 1 перенесе список). Runtime-залежності bundle — SP2 `nix/packages/deps.nix` (там уже є все з §10.1 spec + `httpx`).
- Межа з SP5: CT2-бекенд MT, ORT-рушій TTS (`RT_TTS_ENGINE`), реєстр моделей (alias-и `RT_MODEL`) і device-залежні defaults підключає **SP5 Task 12a** до backend-ів цього плану; тут — лише `transformers`/`kokoro`(torch)/`faster-whisper` backend-и й інтерфейси (дублюючих задач не додавати). Defaults цього плану (`small`, `facebook/nllb-200-distilled-600M`, `hexgrad/Kokoro-82M`, beam 5/4) — тимчасові до SP5 Task 12a.
- Усі команди запускати через `nix develop -c …` з кореня репо `/home/dmaslo/repos/real-time-transletor`.
- Порти: `rt-asr` 8091, `rt-mt` 8092, `rt-tts` 8093; bind за замовчуванням `127.0.0.1`.
- Конфіг тільки з env `RT_*` (overview §4.2 + додані у spec §4.5); невалідний env → `SettingsError` → exit code 2. `~/.config/real-time-translator/config.yml` нові сервіси **не читають**.
- `RT_DEVICE` ∈ `cpu|cuda|rocm`, default `cpu`; `rocm` передається бібліотекам як `"cuda"`.
- `RT_API_TOKEN_FILE` — буквальний шлях (напр. `/run/credentials/rt-asr.service/api-token`), читається один раз при старті; відсутній/порожній файл → exit 2. `/health` і `/status` — без auth; усі `/v1/*` (і WS) — з auth, якщо токен задано.
- `/health` має відповідати ≤ 10 с після старту на найслабшому CPU-хості: `torch`, `ctranslate2`, `faster_whisper`, `transformers`, `kokoro`, `scipy`, `soundfile`, `sounddevice` **ніколи** не імпортуються на рівні модуля в `src/services/**`.
- uvicorn `workers=1`; один inference-потік на процес; жодного глобального мутабельного per-client стану (мова/голос/швидкість — параметри запиту, env дає дефолти).
- `rt-tts` нічого не відтворює і не імпортує `sounddevice`.
- Помилки `/v1/*` — OpenAI-формат `{"error":{"message","type","code"}}`.
- Legacy-код (`src/whisper`, `src/translate`, `src/tts`, `src/models`, `src/common/ipc.py`, `src/translation_system.py`) не змінювати, окрім Task 7 (спільний `filter_repetitions`).
- Тести — у `tests/services/` з унікальними basename `test_svc_*.py`, **без** `__init__.py` (pytest додає теку в `sys.path`, тому працює `import svc_testutil`).
- Slow-тести позначені `@pytest.mark.slow` і запускаються лише з `RT_SLOW_TESTS=1`.
- Коміти — звичайні повідомлення **без** рядків `Co-Authored-By` (правило користувача).

## Review Focus

- Клієнт обриває WS посеред репліки без `stop` → слот сесії звільняється, pending partial скасовується, сервер не падає (тест у Task 12: `test_disconnect_without_stop_frees_session_slot`).
- Черга до моделі переповнена (`RT_MAX_PENDING`) → HTTP 503 `server_busy` з `Retry-After: 1`, наступні запити знову працюють (тест у Task 5: `test_full_queue_returns_503_server_busy`).
- Бінарні WS-фрейми довільного розміру, не кратні 32-мс вікну (і непарна кількість байтів) → результат ідентичний вирівняній подачі (тести в Task 9: `test_feed_in_odd_sized_chunks_is_equivalent`, `test_finish_flushes_open_utterance_including_tail`).
- TTS-модель нічого не синтезувала (напр. кирилиця англійським голосом) → 200 і валідний WAV з нульовою довжиною даних, а не 500 (тест у Task 16: `test_empty_synthesis_returns_valid_empty_wav`).
- Запит приходить, поки модель ще вантажиться або впала → 503 `model_not_ready` (+`Retry-After: 5`) / `model_failed`, для WS — `error` + close 1013 (тести в Task 5 і Task 12: `test_health_ok_while_loading_and_status_transitions`, `test_failed_load_reported`, `test_model_not_ready`).

---

## File map

```
nix/devshell.nix                               # (SP2 Task 1) перевірити/додати fastapi uvicorn python-multipart httpx websockets pytest-timeout
src/services/__init__.py                       # порожній
src/services/common/{__init__,version,env,settings,worker,errors,auth,runtime,gpu,health,app,logs,serve,audio}.py
src/services/asr/{__init__,__main__,settings,backend,faster_whisper_backend,textfilter,vad,endpointer,stream,http,ws,app}.py
src/services/mt/{__init__,__main__,languages,settings,backend,transformers_backend,http,app}.py
src/services/tts/{__init__,__main__,voices,settings,backend,kokoro_backend,encoding,http,app}.py
src/whisper/whisper_service.py                 # Task 7: імпорт filter_repetitions зі спільного модуля
tests/services/conftest.py, tests/services/svc_testutil.py, tests/services/test_svc_*.py
```

Відповідальності: `common/` нічого не знає про конкретні моделі; `*/backend.py` — інтерфейс (Protocol) + fake + фабрика; `*_backend.py` — реальна модель (lazy imports); `http.py`/`ws.py` — тільки протокол і валідація; `app.py` — збирання; `__main__.py` — entrypoint (`main() -> int`, майбутній `bin/rt-<svc>` у підпроєкті 2).

---

### Task 1: Devshell-залежності та тестовий каркас

**Files:**
- Create: `src/services/__init__.py`
- Create: `src/services/common/__init__.py`
- Create: `src/services/asr/__init__.py`
- Create: `src/services/mt/__init__.py`
- Create: `src/services/tts/__init__.py`
- Create: `tests/services/conftest.py`
- Create: `tests/services/svc_testutil.py`
- Modify (якщо бракує пакетів): `nix/devshell.nix` (fallback: `flake-global/dev/devshell.nix`, якщо SP2 Task 1 ще не виконано)

**Interfaces:**
- Produces: маркер `slow` (пропуск без `RT_SLOW_TESTS=1`); `svc_testutil`: `SR=16000`, `WINDOW=512`, `tone(n_samples, amp=0.1, freq=220.0, sr=SR) -> np.ndarray`, `pcm(audio) -> bytes`, `speech_windows(n) -> bytes`, `silence_windows(n) -> bytes`, `wav_file(audio, sr=SR, fmt="WAV") -> bytes`, `running(app, timeout=5.0)` (context manager → `TestClient` з lifespan і готовою моделлю).

- [ ] **Step 1: Переконатися, що залежностей немає**

Run: `nix develop -c python -c "import fastapi, uvicorn, multipart, httpx, websockets, pytest_timeout"`
Expected: `ModuleNotFoundError: No module named 'fastapi'`

- [ ] **Step 2: Додати пакети в devshell**

У `nix/devshell.nix` (SP2 Task 1 уже містить `fastapi`, `uvicorn`, `python-multipart`, `pytest-timeout`, а `httpx`/`websockets` — через `edge-deps.nix`; тоді цей крок — лише перевірка) або, якщо SP2 Task 1 ще не виконано, у `flake-global/dev/devshell.nix`, у списку `(pkgs.python313.withPackages (ps: with ps; [ ... ]))`, після рядка `pytest` додати відсутні:

```nix
          pytest-timeout
          # speech services API (src/services)
          fastapi
          uvicorn
          python-multipart
          httpx
          websockets
```

- [ ] **Step 3: Перевірити імпорт**

Run: `nix develop -c python -c "import fastapi, uvicorn, multipart, httpx, websockets, pytest_timeout; print(fastapi.__version__)"`
Expected: `0.135.3` (або версія з поточного lock-у; головне — без помилки)

- [ ] **Step 4: Створити порожні пакети**

Створити порожні файли `src/services/__init__.py`, `src/services/common/__init__.py`, `src/services/asr/__init__.py`, `src/services/mt/__init__.py`, `src/services/tts/__init__.py` (0 байт — `__init__` не повинні нічого імпортувати, бо legacy `src/whisper/whisper_service.py` імпортуватиме `src.services.asr.textfilter`).

- [ ] **Step 5: Створити тестовий каркас**

`tests/services/conftest.py`:

```python
import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: real-model test; run with RT_SLOW_TESTS=1")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RT_SLOW_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="real-model test; set RT_SLOW_TESTS=1")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
```

`tests/services/svc_testutil.py`:

```python
"""Helpers for tests/services (importable because tests/services has no __init__.py)."""
import io
from contextlib import contextmanager

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

SR = 16000
WINDOW = 512


def tone(n_samples: int, amp: float = 0.1, freq: float = 220.0, sr: int = SR) -> np.ndarray:
    t = np.arange(n_samples) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def pcm(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


def speech_windows(n: int) -> bytes:
    """n * 32 ms of loud tone (speech for EnergyVad)."""
    return pcm(tone(n * WINDOW))


def silence_windows(n: int) -> bytes:
    return bytes(n * WINDOW * 2)


def wav_file(audio: np.ndarray, sr: int = SR, fmt: str = "WAV") -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format=fmt)
    return buf.getvalue()


@contextmanager
def running(app, timeout: float = 5.0):
    """TestClient with lifespan running and the (fake) model loaded."""
    with TestClient(app) as client:
        assert app.state.runtime.wait_ready(timeout), app.state.runtime.error
        yield client
```

- [ ] **Step 6: Перевірити, що pytest бачить теку**

Run: `nix develop -c pytest tests/services -q`
Expected: `no tests ran` (exit code 5) без помилок імпорту conftest.

- [ ] **Step 7: Commit**

```bash
git add nix/devshell.nix src/services tests/services/conftest.py tests/services/svc_testutil.py   # або flake-global/dev/devshell.nix (fallback)
git commit -m "build(devshell): add fastapi/uvicorn deps and speech services test scaffold"
```

---

### Task 2: common: env-парсинг, BaseSettings, версія

**Files:**
- Create: `src/services/common/version.py`
- Create: `src/services/common/env.py`
- Create: `src/services/common/settings.py`
- Test: `tests/services/test_svc_env_settings.py`

**Interfaces:**
- Produces: `SERVICE_VERSION = "0.1.0"`; `SettingsError(ValueError)`; `env_str(env, key, default)`, `env_int(env, key, default, *, min_value=None, max_value=None)`, `env_float(...)`, `env_bool(env, key, default)`, `env_choice(env, key, default, choices)`; frozen dataclass `BaseSettings(service, backend, host, port, device, model, compute_type, model_dir, api_token_file, log_level, max_pending)` з property `is_gpu`, `torch_device`; `base_from_env(env, *, service, backend_key, backends, default_backend, default_model, compute_cpu, compute_gpu) -> dict`; `apply_model_dir(settings, environ=None)`; `DEFAULT_PORTS`.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_env_settings.py`:

```python
import pytest

from src.services.common.env import SettingsError, env_bool, env_choice, env_float, env_int, env_str
from src.services.common.settings import BaseSettings, apply_model_dir, base_from_env


def _base(env):
    return BaseSettings(**base_from_env(env, service="rt-asr", backend_key="RT_ASR_BACKEND",
                                        backends=("faster-whisper", "fake"), default_backend="faster-whisper",
                                        default_model="small", compute_cpu="int8", compute_gpu="float16"))


def test_env_helpers_defaults_and_parsing():
    env = {"S": " x ", "I": "7", "F": "0.5", "B": "Yes", "C": "CUDA", "E": "  "}
    assert env_str(env, "S", None) == "x"
    assert env_str(env, "E", "d") == "d"
    assert env_int(env, "I", 1) == 7
    assert env_float(env, "F", 1.0) == 0.5
    assert env_bool(env, "B", False) is True
    assert env_choice(env, "C", "cpu", ("cpu", "cuda")) == "cuda"
    assert env_int(env, "MISSING", 3) == 3


@pytest.mark.parametrize("fn,args", [
    (env_int, ({"K": "x"}, "K", 1)),
    (env_float, ({"K": "x"}, "K", 1.0)),
    (env_bool, ({"K": "maybe"}, "K", True)),
    (env_choice, ({"K": "tpu"}, "K", "cpu", ("cpu", "cuda"))),
])
def test_env_helpers_reject_invalid(fn, args):
    with pytest.raises(SettingsError):
        fn(*args)


def test_env_int_range():
    with pytest.raises(SettingsError):
        env_int({"K": "0"}, "K", 1, min_value=1)


def test_base_defaults():
    s = _base({})
    assert (s.service, s.host, s.port, s.device) == ("rt-asr", "127.0.0.1", 8091, "cpu")
    assert (s.model, s.compute_type, s.backend, s.max_pending) == ("small", "int8", "faster-whisper", 16)
    assert s.api_token_file is None and s.log_level == "info"
    assert s.torch_device == "cpu" and not s.is_gpu


def test_base_gpu_and_rocm():
    assert _base({"RT_DEVICE": "cuda"}).compute_type == "float16"
    rocm = _base({"RT_DEVICE": "rocm"})
    assert rocm.torch_device == "cuda" and rocm.is_gpu


def test_base_rejects_bad_port():
    with pytest.raises(SettingsError):
        _base({"RT_PORT": "70000"})


def test_apply_model_dir_sets_hf_home_only_when_unset():
    s = _base({"RT_MODEL_DIR": "/var/lib/rt-asr/models"})
    env = {}
    apply_model_dir(s, env)
    assert env["HF_HOME"] == "/var/lib/rt-asr/models/hf"
    env = {"HF_HOME": "/keep"}
    apply_model_dir(s, env)
    assert env["HF_HOME"] == "/keep"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_env_settings.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.common.env'`

- [ ] **Step 3: Write minimal implementation**

`src/services/common/version.py`:

```python
"""Version reported by /status of every speech service."""

SERVICE_VERSION = "0.1.0"
```

`src/services/common/env.py`:

```python
"""Typed readers for RT_* environment variables."""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class SettingsError(ValueError):
    """Invalid service configuration. main() exits with code 2."""


def env_str(env: Mapping[str, str], key: str, default: Optional[str]) -> Optional[str]:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _check_range(key: str, value: float, min_value: Optional[float], max_value: Optional[float]) -> None:
    if min_value is not None and value < min_value:
        raise SettingsError(f"{key} must be >= {min_value}, got {value}")
    if max_value is not None and value > max_value:
        raise SettingsError(f"{key} must be <= {max_value}, got {value}")


def env_int(env: Mapping[str, str], key: str, default: int, *,
            min_value: Optional[int] = None, max_value: Optional[int] = None) -> int:
    raw = env_str(env, key, None)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer, got {raw!r}") from exc
    _check_range(key, value, min_value, max_value)
    return value


def env_float(env: Mapping[str, str], key: str, default: float, *,
              min_value: Optional[float] = None, max_value: Optional[float] = None) -> float:
    raw = env_str(env, key, None)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be a number, got {raw!r}") from exc
    _check_range(key, value, min_value, max_value)
    return value


def env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env_str(env, key, None)
    if raw is None:
        return default
    low = raw.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    raise SettingsError(f"{key} must be a boolean (true/false), got {raw!r}")


def env_choice(env: Mapping[str, str], key: str, default: str, choices: Sequence[str]) -> str:
    raw = env_str(env, key, None)
    if raw is None:
        return default
    low = raw.lower()
    if low not in choices:
        raise SettingsError(f"{key} must be one of {', '.join(choices)}, got {raw!r}")
    return low
```

`src/services/common/settings.py`:

```python
"""Settings shared by all speech services (overview §4.2)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

from .env import env_choice, env_int, env_str

DEFAULT_PORTS = {"rt-asr": 8091, "rt-mt": 8092, "rt-tts": 8093}
DEVICES = ("cpu", "cuda", "rocm")
LOG_LEVELS = ("trace", "debug", "info", "warning", "error", "critical")


@dataclass(frozen=True)
class BaseSettings:
    service: str
    backend: str
    host: str
    port: int
    device: str
    model: str
    compute_type: str
    model_dir: Optional[str]
    api_token_file: Optional[str]
    log_level: str
    max_pending: int

    @property
    def is_gpu(self) -> bool:
        return self.device != "cpu"

    @property
    def torch_device(self) -> str:
        """Device name for torch / CTranslate2. ROCm builds also use "cuda"."""
        return "cuda" if self.is_gpu else "cpu"


def base_from_env(env: Mapping[str, str], *, service: str, backend_key: str,
                  backends: Sequence[str], default_backend: str, default_model: str,
                  compute_cpu: str, compute_gpu: str) -> Dict[str, Any]:
    device = env_choice(env, "RT_DEVICE", "cpu", DEVICES)
    return dict(
        service=service,
        backend=env_choice(env, backend_key, default_backend, backends),
        host=env_str(env, "RT_HOST", "127.0.0.1"),
        port=env_int(env, "RT_PORT", DEFAULT_PORTS[service], min_value=1, max_value=65535),
        device=device,
        model=env_str(env, "RT_MODEL", default_model),
        compute_type=env_str(env, "RT_COMPUTE_TYPE", compute_cpu if device == "cpu" else compute_gpu),
        model_dir=env_str(env, "RT_MODEL_DIR", None),
        api_token_file=env_str(env, "RT_API_TOKEN_FILE", None),
        log_level=env_choice(env, "RT_LOG_LEVEL", "info", LOG_LEVELS),
        max_pending=env_int(env, "RT_MAX_PENDING", 16, min_value=1),
    )


def apply_model_dir(settings: BaseSettings, environ: Optional[MutableMapping[str, str]] = None) -> None:
    """Point HF_HOME at $RT_MODEL_DIR/hf unless HF_HOME is already set."""
    environ = os.environ if environ is None else environ
    if settings.model_dir and not environ.get("HF_HOME"):
        environ["HF_HOME"] = os.path.join(settings.model_dir, "hf")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_env_settings.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/common/version.py src/services/common/env.py src/services/common/settings.py tests/services/test_svc_env_settings.py
git commit -m "feat(services): env parsing and base settings"
```

---

### Task 3: common: InferenceWorker (пріоритетна черга, один потік)

**Files:**
- Create: `src/services/common/worker.py`
- Test: `tests/services/test_svc_worker.py`

**Interfaces:**
- Produces: `Priority(IntEnum)`: `FINAL=0`, `REQUEST=1`, `PARTIAL=2`; `WorkerBusy(RuntimeError)`; `InferenceWorker(max_pending: int, name="inference")` з `start()`, `stop(timeout=5.0)`, property `pending -> int` (у черзі + виконується), `submit(fn, *args, priority=Priority.REQUEST, bypass_limit=False, **kwargs) -> concurrent.futures.Future`.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_worker.py`:

```python
import concurrent.futures
import threading

import pytest

from src.services.common.worker import InferenceWorker, Priority, WorkerBusy


@pytest.fixture
def worker():
    w = InferenceWorker(max_pending=10)
    w.start()
    yield w
    w.stop()


def _gate(worker):
    started, release = threading.Event(), threading.Event()

    def blocker():
        started.set()
        release.wait(5)
        return "gate"

    fut = worker.submit(blocker)
    assert started.wait(5)
    return release, fut


def test_runs_jobs_and_returns_results(worker):
    assert worker.submit(lambda a, b: a + b, 2, 3).result(5) == 5


def test_priority_order_then_fifo(worker):
    order = []
    release, _ = _gate(worker)
    futs = [
        worker.submit(order.append, "partial", priority=Priority.PARTIAL),
        worker.submit(order.append, "request-1", priority=Priority.REQUEST),
        worker.submit(order.append, "final", priority=Priority.FINAL),
        worker.submit(order.append, "request-2", priority=Priority.REQUEST),
    ]
    release.set()
    concurrent.futures.wait(futs, timeout=5)
    assert order == ["final", "request-1", "request-2", "partial"]


def test_limit_and_bypass():
    w = InferenceWorker(max_pending=2)
    w.start()
    try:
        release, _ = _gate(w)
        w.submit(lambda: None)
        assert w.pending == 2
        with pytest.raises(WorkerBusy):
            w.submit(lambda: None)
        bypass = w.submit(lambda: "ok", bypass_limit=True)
        release.set()
        assert bypass.result(5) == "ok"
    finally:
        w.stop()


def test_cancelled_job_is_skipped(worker):
    called = []
    release, _ = _gate(worker)
    fut = worker.submit(called.append, 1)
    assert fut.cancel()
    release.set()
    worker.submit(lambda: None).result(5)
    assert called == []
    assert worker.pending == 0


def test_exception_propagates(worker):
    def boom():
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        worker.submit(boom).result(5)
    assert worker.submit(lambda: 1).result(5) == 1


def test_stop_cancels_pending_and_rejects_new():
    w = InferenceWorker(max_pending=10)
    w.start()
    release, _ = _gate(w)
    queued = w.submit(lambda: None)
    threading.Timer(0.2, release.set).start()
    w.stop()
    assert queued.cancelled()
    with pytest.raises(WorkerBusy):
        w.submit(lambda: None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_worker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.common.worker'`

- [ ] **Step 3: Write minimal implementation**

`src/services/common/worker.py`:

```python
"""Single inference thread with a priority queue (serialises all model access)."""
from __future__ import annotations

import concurrent.futures
import itertools
import queue
import threading
from enum import IntEnum
from typing import Any, Callable, Optional

from loguru import logger


class Priority(IntEnum):
    FINAL = 0     # realtime ASR finals
    REQUEST = 1   # ordinary HTTP requests
    PARTIAL = 2   # realtime ASR partials (droppable)


class WorkerBusy(RuntimeError):
    """Raised by submit() when the queue is full or the worker is stopping."""


class InferenceWorker:
    def __init__(self, max_pending: int, name: str = "inference") -> None:
        self._max_pending = max_pending
        self._name = name
        self._queue: "queue.PriorityQueue[tuple]" = queue.PriorityQueue()
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._pending = 0
        self._stopping = False
        self._thread: Optional[threading.Thread] = None

    @property
    def pending(self) -> int:
        """Jobs queued plus the job currently running."""
        with self._lock:
            return self._pending

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any, priority: Priority = Priority.REQUEST,
               bypass_limit: bool = False, **kwargs: Any) -> concurrent.futures.Future:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        with self._lock:
            if self._stopping:
                raise WorkerBusy("worker is stopping")
            if not bypass_limit and self._pending >= self._max_pending:
                raise WorkerBusy(f"{self._pending} jobs pending (limit {self._max_pending})")
            self._pending += 1
        self._queue.put((int(priority), next(self._seq), fut, fn, args, kwargs))
        return fut

    def _done(self) -> None:
        with self._lock:
            self._pending -= 1

    def _run(self) -> None:
        while True:
            _, _, fut, fn, args, kwargs = self._queue.get()
            if fut is None:
                return
            try:
                if not fut.set_running_or_notify_cancel():
                    continue
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 — propagated to the caller
                    logger.debug("inference job failed: {}", exc)
                    fut.set_exception(exc)
                else:
                    fut.set_result(result)
            finally:
                self._done()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stopping = True
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item[2] is not None:
                item[2].cancel()
                self._done()
        self._queue.put((-1, next(self._seq), None, None, (), {}))
        if self._thread is not None:
            self._thread.join(timeout)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_worker.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/common/worker.py tests/services/test_svc_worker.py
git commit -m "feat(services): single-thread priority inference worker"
```

---

### Task 4: common: OpenAI-помилки та bearer-auth

**Files:**
- Create: `src/services/common/errors.py`
- Create: `src/services/common/auth.py`
- Test: `tests/services/test_svc_auth.py`

**Interfaces:**
- Consumes: `SettingsError` (Task 2), `WorkerBusy` (Task 3).
- Produces: `ApiError(status: int, code: str, message: str, headers: dict | None = None)`; `error_body(status, code, message) -> dict`; `install_error_handlers(app)` (ApiError → JSON, WorkerBusy → 503 `server_busy` + `Retry-After: 1`); `load_token(path: str | None) -> str | None`; `header_matches(header, token) -> bool`; `make_bearer_dependency(token) -> async dependency(conn: HTTPConnection)`; `ws_authorized(conn, token) -> bool`.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_auth.py`:

```python
import pytest
from fastapi import APIRouter, Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.services.common.auth import load_token, make_bearer_dependency, ws_authorized
from src.services.common.env import SettingsError
from src.services.common.errors import install_error_handlers


def _app(token):
    app = FastAPI()
    install_error_handlers(app)
    v1 = APIRouter(prefix="/v1", dependencies=[Depends(make_bearer_dependency(token))])

    @v1.get("/ping")
    async def ping():
        return {"pong": True}

    app.include_router(v1)

    @app.websocket("/v1/ws")
    async def ws(websocket: WebSocket):
        if not ws_authorized(websocket, token):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    return app


def test_load_token(tmp_path):
    assert load_token(None) is None
    f = tmp_path / "token"
    f.write_text("  s3cret\n")
    assert load_token(str(f)) == "s3cret"


@pytest.mark.parametrize("content", [None, "   \n"])
def test_load_token_fails_closed(tmp_path, content):
    f = tmp_path / "token"
    if content is not None:
        f.write_text(content)
    with pytest.raises(SettingsError):
        load_token(str(f))


def test_no_token_means_open():
    assert TestClient(_app(None)).get("/v1/ping").status_code == 200


def test_bearer_required():
    client = TestClient(_app("s3cret"))
    r = client.get("/v1/ping")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert r.headers["www-authenticate"] == "Bearer"
    assert client.get("/v1/ping", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/ping", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/v1/ping", headers={"Authorization": "bearer s3cret"}).status_code == 200


def test_ws_token():
    client = TestClient(_app("s3cret"))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/ws"):
            pass
    assert exc.value.code == 1008
    with client.websocket_connect("/v1/ws", headers={"Authorization": "Bearer s3cret"}) as ws:
        assert ws.receive_json() == {"ok": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.common.auth'`

- [ ] **Step 3: Write minimal implementation**

`src/services/common/errors.py`:

```python
"""OpenAI-style error responses."""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .worker import WorkerBusy


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str,
                 headers: Optional[Dict[str, str]] = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def error_body(status: int, code: str, message: str) -> dict:
    if status == 401:
        type_ = "authentication_error"
    elif status >= 500:
        type_ = "server_error"
    else:
        type_ = "invalid_request_error"
    return {"error": {"message": message, "type": type_, "code": code}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=error_body(exc.status, exc.code, exc.message),
                            headers=exc.headers)

    @app.exception_handler(WorkerBusy)
    async def _busy(_: Request, exc: WorkerBusy) -> JSONResponse:
        return JSONResponse(status_code=503, content=error_body(503, "server_busy", str(exc)),
                            headers={"Retry-After": "1"})
```

`src/services/common/auth.py`:

```python
"""Optional bearer-token auth (RT_API_TOKEN_FILE)."""
from __future__ import annotations

import hmac
from pathlib import Path
from typing import Callable, Optional

from starlette.requests import HTTPConnection

from .env import SettingsError
from .errors import ApiError


def load_token(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        token = Path(path).read_text().strip()
    except OSError as exc:
        raise SettingsError(f"RT_API_TOKEN_FILE {path!r} is not readable: {exc}") from exc
    if not token:
        raise SettingsError(f"RT_API_TOKEN_FILE {path!r} is empty")
    return token


def header_matches(header: Optional[str], token: str) -> bool:
    if not header:
        return False
    scheme, _, value = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), token.encode())


def make_bearer_dependency(token: Optional[str]) -> Callable:
    async def require_bearer(conn: HTTPConnection) -> None:
        if token is None:
            return
        if not header_matches(conn.headers.get("authorization"), token):
            raise ApiError(401, "unauthorized", "Missing or invalid bearer token",
                           headers={"WWW-Authenticate": "Bearer"})

    return require_bearer


def ws_authorized(conn: HTTPConnection, token: Optional[str]) -> bool:
    return token is None or header_matches(conn.headers.get("authorization"), token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_auth.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/common/errors.py src/services/common/auth.py tests/services/test_svc_auth.py
git commit -m "feat(services): OpenAI-style errors and optional bearer auth"
```

---

### Task 5: common: ServiceRuntime, /health, /status, app factory, logging, entrypoint

**Files:**
- Create: `src/services/common/runtime.py`
- Create: `src/services/common/gpu.py`
- Create: `src/services/common/health.py`
- Create: `src/services/common/app.py`
- Create: `src/services/common/logs.py`
- Create: `src/services/common/serve.py`
- Test: `tests/services/test_svc_runtime_health.py`

**Interfaces:**
- Consumes: `BaseSettings`, `base_from_env`, `apply_model_dir`, `SettingsError` (Task 2); `InferenceWorker`, `Priority` (Task 3); `ApiError`, `install_error_handlers`, `make_bearer_dependency` (Task 4).
- Produces: `ModelState(str, Enum)` (`loading|ready|failed`); `ServiceRuntime(service: str, backend_factory: Callable[[], Any], max_pending: int)` з атрибутами `backend`, `state`, `error`, `load_seconds`, `worker` і методами `start()`, `stop()`, `wait_ready(timeout) -> bool`, `require_ready() -> backend` (503 `model_not_ready`/`model_failed`), `submit_threadsafe(fn, *args, priority=REQUEST, bypass_limit=False, **kw) -> Future`, `async run(fn, *args, priority=REQUEST, **kw)`; `VramProbe(device, runner=subprocess.run, clock=time.monotonic, ttl=5.0, pid=None).read() -> int | None`; `build_health_router(runtime, settings, vram)`; `LifespanHook = Callable[[ServiceRuntime], Callable[[], None]]`; `create_service_app(*, settings, runtime, token, routers=(), ws_routers=(), lifespan_hooks=(), vram=None) -> FastAPI` (HTTP-роутери під `/v1` з bearer-залежністю; WS-роутери під `/v1` без неї — WS перевіряє токен сам; `app.state.runtime`, `app.state.settings`); `setup_logging(level)`; `run_service(settings_loader, app_factory) -> int` (exit 2 на `SettingsError`).
- Примітка: `logs.py`, а не `logging.py` — щоб не затіняти stdlib. `run_service` імпортує `uvicorn` усередині функції.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_runtime_health.py`:

```python
import threading

from fastapi import APIRouter
from fastapi.testclient import TestClient

from src.services.common.app import create_service_app
from src.services.common.gpu import VramProbe
from src.services.common.runtime import ServiceRuntime
from src.services.common.settings import BaseSettings, base_from_env


class GatedBackend:
    name = "gated"
    model_id = "gated-model"

    def __init__(self, gate=None, fail=None):
        self.gate = gate
        self.fail = fail

    def load(self):
        if self.gate is not None:
            assert self.gate.wait(5)
        if self.fail:
            raise RuntimeError(self.fail)

    def echo(self, x):
        return x


def _settings():
    return BaseSettings(**base_from_env({"RT_MODEL": "gated-model"}, service="rt-mt", backend_key="RT_MT_BACKEND",
                                        backends=("fake",), default_backend="fake", default_model="m",
                                        compute_cpu="float32", compute_gpu="float16"))


def _app(backend, hooks=()):
    settings = _settings()
    runtime = ServiceRuntime("rt-mt", lambda: backend, settings.max_pending)
    router = APIRouter()

    @router.get("/echo")
    async def echo(x: str):
        b = runtime.require_ready()
        return {"x": await runtime.run(b.echo, x)}

    return create_service_app(settings=settings, runtime=runtime, token=None, routers=[router],
                              lifespan_hooks=hooks), runtime


def test_health_ok_while_loading_and_status_transitions():
    gate = threading.Event()
    app, runtime = _app(GatedBackend(gate))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        status = client.get("/status").json()
        assert status["ready"] is False and status["model"]["state"] == "loading"
        r = client.get("/v1/echo", params={"x": "a"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_not_ready"
        assert r.headers["retry-after"] == "5"
        gate.set()
        assert runtime.wait_ready(5)
        status = client.get("/status").json()
        assert status["service"] == "rt-mt" and status["version"] == "0.1.0"
        assert status["device"] == "cpu" and status["backend"] == "fake"
        assert status["model"]["id"] == "gated-model" and status["model"]["loaded"] is True
        assert status["ready"] is True and status["vram_mb"] is None and status["pending"] == 0
        assert client.get("/v1/echo", params={"x": "a"}).json() == {"x": "a"}


def test_failed_load_reported():
    app, runtime = _app(GatedBackend(fail="no weights"))
    with TestClient(app) as client:
        assert not runtime.wait_ready(5)
        status = client.get("/status").json()
        assert status["ready"] is False and status["model"]["state"] == "failed"
        assert "no weights" in status["model"]["error"]
        r = client.get("/v1/echo", params={"x": "a"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_failed"


def test_lifespan_hooks_started_and_stopped():
    events = []

    def hook(runtime):
        events.append(("start", runtime.service))
        return lambda: events.append(("stop", runtime.service))

    app, _ = _app(GatedBackend(), hooks=[hook])
    with TestClient(app):
        assert events == [("start", "rt-mt")]
    assert events == [("start", "rt-mt"), ("stop", "rt-mt")]


def test_vram_probe_parses_own_pid_and_caches():
    calls = []

    class Result:
        stdout = "111, 500\n222, 300\n111, 12\n"

    def runner(*args, **kwargs):
        calls.append(args)
        return Result()

    clock = iter([0.0, 1.0, 10.0])
    probe = VramProbe("cuda", runner=runner, clock=lambda: next(clock), pid=111)
    assert probe.read() == 512
    assert probe.read() == 512
    assert len(calls) == 1
    probe.read()
    assert len(calls) == 2
    assert VramProbe("cpu", runner=runner).read() is None


def test_vram_probe_handles_missing_nvidia_smi():
    def runner(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    assert VramProbe("cuda", runner=runner).read() is None


def test_full_queue_returns_503_server_busy():
    settings = BaseSettings(**{**_settings().__dict__, "max_pending": 1})
    backend = GatedBackend()
    runtime = ServiceRuntime("rt-mt", lambda: backend, settings.max_pending)
    router = APIRouter()

    @router.get("/echo")
    async def echo(x: str):
        return {"x": await runtime.run(runtime.require_ready().echo, x)}

    app = create_service_app(settings=settings, runtime=runtime, token=None, routers=[router])
    release = threading.Event()
    with TestClient(app) as client:
        assert runtime.wait_ready(5)
        blocker = runtime.submit_threadsafe(release.wait, 5)
        r = client.get("/v1/echo", params={"x": "a"})
        release.set()
        blocker.result(5)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "server_busy" and r.headers["retry-after"] == "1"
        assert client.get("/v1/echo", params={"x": "b"}).json() == {"x": "b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_runtime_health.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.common.app'`

- [ ] **Step 3: Write minimal implementation**

`src/services/common/runtime.py`:

```python
"""Model lifecycle (background load) + access to the inference worker."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from enum import Enum
from typing import Any, Callable, Optional

from loguru import logger

from .errors import ApiError
from .worker import InferenceWorker, Priority


class ModelState(str, Enum):
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


class ServiceRuntime:
    def __init__(self, service: str, backend_factory: Callable[[], Any], max_pending: int) -> None:
        self.service = service
        self._factory = backend_factory
        self.worker = InferenceWorker(max_pending, name=f"{service}-inference")
        self.backend: Any = None
        self.state = ModelState.LOADING
        self.error: Optional[str] = None
        self.load_seconds: Optional[float] = None
        self._settled = threading.Event()

    def start(self) -> None:
        self.worker.start()
        threading.Thread(target=self._load, name=f"{self.service}-loader", daemon=True).start()

    def _load(self) -> None:
        t0 = time.monotonic()
        try:
            backend = self._factory()
            logger.info("{}: loading model {} ({})", self.service, getattr(backend, "model_id", "?"),
                        getattr(backend, "name", "?"))
            backend.load()
        except Exception as exc:  # noqa: BLE001 — reported via /status
            logger.exception("{}: model load failed", self.service)
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = ModelState.FAILED
        else:
            self.backend = backend
            self.load_seconds = round(time.monotonic() - t0, 3)
            self.state = ModelState.READY
            logger.info("{}: model ready in {:.1f}s", self.service, self.load_seconds)
        finally:
            self._settled.set()

    def stop(self) -> None:
        self.worker.stop()

    def wait_ready(self, timeout: float) -> bool:
        self._settled.wait(timeout)
        return self.state is ModelState.READY

    def require_ready(self) -> Any:
        if self.state is ModelState.READY:
            return self.backend
        if self.state is ModelState.FAILED:
            raise ApiError(503, "model_failed", f"Model failed to load: {self.error}")
        raise ApiError(503, "model_not_ready", "Model is still loading", headers={"Retry-After": "5"})

    def submit_threadsafe(self, fn: Callable[..., Any], *args: Any, priority: Priority = Priority.REQUEST,
                          bypass_limit: bool = False, **kwargs: Any) -> concurrent.futures.Future:
        """Thread-safe entry into the inference queue (HTTP, WS and the future Django worker)."""
        return self.worker.submit(fn, *args, priority=priority, bypass_limit=bypass_limit, **kwargs)

    async def run(self, fn: Callable[..., Any], *args: Any, priority: Priority = Priority.REQUEST,
                  **kwargs: Any) -> Any:
        return await asyncio.wrap_future(self.submit_threadsafe(fn, *args, priority=priority, **kwargs))
```

`src/services/common/gpu.py`:

```python
"""Best-effort VRAM usage of this process (CUDA only, via nvidia-smi)."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Callable, Optional


class VramProbe:
    def __init__(self, device: str, runner: Callable = subprocess.run,
                 clock: Callable[[], float] = time.monotonic, ttl: float = 5.0,
                 pid: Optional[int] = None) -> None:
        self._device = device
        self._runner = runner
        self._clock = clock
        self._ttl = ttl
        self._pid = pid if pid is not None else os.getpid()
        self._at: Optional[float] = None
        self._value: Optional[int] = None

    def read(self) -> Optional[int]:
        if self._device != "cuda":
            return None
        now = self._clock()
        if self._at is not None and now - self._at < self._ttl:
            return self._value
        self._at = now
        self._value = self._query()
        return self._value

    def _query(self) -> Optional[int]:
        try:
            out = self._runner(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        total = None
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2 and parts[0] == str(self._pid) and parts[1].isdigit():
                total = (total or 0) + int(parts[1])
        return total
```

`src/services/common/health.py`:

```python
"""/health (liveness) and /status (readiness)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from .gpu import VramProbe
from .runtime import ModelState, ServiceRuntime
from .settings import BaseSettings
from .version import SERVICE_VERSION


def build_health_router(runtime: ServiceRuntime, settings: BaseSettings, vram: VramProbe) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @router.get("/status")
    async def status() -> dict:
        ready = runtime.state is ModelState.READY
        return {
            "service": settings.service,
            "version": SERVICE_VERSION,
            "device": settings.device,
            "backend": settings.backend,
            "model": {
                "id": settings.model,
                "loaded": ready,
                "state": runtime.state.value,
                "load_seconds": runtime.load_seconds,
                "error": runtime.error,
            },
            "ready": ready,
            "vram_mb": await asyncio.to_thread(vram.read),
            "pending": runtime.worker.pending,
        }

    return router
```

`src/services/common/app.py`:

```python
"""FastAPI app skeleton shared by rt-asr, rt-mt and rt-tts."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable, Optional, Sequence

from fastapi import APIRouter, Depends, FastAPI

from .auth import make_bearer_dependency
from .errors import install_error_handlers
from .gpu import VramProbe
from .health import build_health_router
from .runtime import ServiceRuntime
from .settings import BaseSettings
from .version import SERVICE_VERSION

# A hook gets the runtime at startup and returns a stop function (Django worker, sub-project 4).
LifespanHook = Callable[[ServiceRuntime], Callable[[], None]]


def create_service_app(*, settings: BaseSettings, runtime: ServiceRuntime, token: Optional[str],
                       routers: Sequence[APIRouter] = (), ws_routers: Sequence[APIRouter] = (),
                       lifespan_hooks: Sequence[LifespanHook] = (),
                       vram: Optional[VramProbe] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime.start()
        stops = [hook(runtime) for hook in lifespan_hooks]
        try:
            yield
        finally:
            for stop in reversed(stops):
                stop()
            runtime.stop()

    app = FastAPI(title=settings.service, version=SERVICE_VERSION, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(build_health_router(runtime, settings, vram or VramProbe(settings.device)))

    v1 = APIRouter(prefix="/v1", dependencies=[Depends(make_bearer_dependency(token))])
    for router in routers:
        v1.include_router(router)
    app.include_router(v1)
    for router in ws_routers:  # WS handlers check the token themselves (close 1008)
        app.include_router(router, prefix="/v1")
    return app
```

`src/services/common/logs.py`:

```python
"""Loguru setup + routing of stdlib logging (uvicorn) into loguru."""
from __future__ import annotations

import logging
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level.upper(), backtrace=False, diagnose=False)
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
```

`src/services/common/serve.py`:

```python
"""Common main() for `python -m src.services.<svc>`."""
from __future__ import annotations

import os
import sys
from typing import Callable, Mapping

from fastapi import FastAPI

from .env import SettingsError
from .logs import setup_logging
from .settings import BaseSettings, apply_model_dir


def run_service(settings_loader: Callable[[Mapping[str, str]], BaseSettings],
                app_factory: Callable[[BaseSettings], FastAPI]) -> int:
    try:
        settings = settings_loader(os.environ)
        setup_logging(settings.log_level)
        apply_model_dir(settings)
        app = app_factory(settings)
    except SettingsError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, log_config=None,
                log_level=settings.log_level)
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_runtime_health.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/common/runtime.py src/services/common/gpu.py src/services/common/health.py src/services/common/app.py src/services/common/logs.py src/services/common/serve.py tests/services/test_svc_runtime_health.py
git commit -m "feat(services): runtime with background model load, health/status, app factory"
```

---

### Task 6: common: аудіо-хелпери

**Files:**
- Create: `src/services/common/audio.py`
- Test: `tests/services/test_svc_audio.py`

**Interfaces:**
- Produces: `TARGET_RATE = 16000`; `AudioDecodeError(ValueError)`; `resample(audio, src_rate, dst_rate) -> np.ndarray[float32]`; `decode_audio_file(data: bytes, target_rate=16000) -> np.ndarray[float32]` (mono); `pcm16_to_float(data: bytes) -> np.ndarray` (ігнорує непарний останній байт); `float_to_pcm16(audio) -> bytes` (clip до [-1, 1]). `scipy` і `soundfile` імпортуються всередині функцій.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_audio.py`:

```python
import numpy as np
import pytest

from src.services.common.audio import (AudioDecodeError, decode_audio_file, float_to_pcm16, pcm16_to_float,
                                       resample)
from svc_testutil import tone, wav_file


def test_decode_wav_16k_mono():
    audio = decode_audio_file(wav_file(tone(16000)))
    assert audio.dtype == np.float32 and audio.shape == (16000,)
    assert abs(float(np.max(np.abs(audio))) - 0.1) < 0.01


def test_decode_stereo_48k_flac_to_16k_mono():
    stereo = np.stack([tone(48000, sr=48000), tone(48000, sr=48000)], axis=1)
    audio = decode_audio_file(wav_file(stereo, sr=48000, fmt="FLAC"))
    assert audio.shape == (16000,)


@pytest.mark.parametrize("data", [b"", b"not audio at all"])
def test_decode_rejects_garbage(data):
    with pytest.raises(AudioDecodeError):
        decode_audio_file(data)


def test_pcm_roundtrip_and_odd_bytes():
    x = np.array([0.0, 0.5, -0.5, 1.5], dtype=np.float32)
    back = pcm16_to_float(float_to_pcm16(x))
    assert np.allclose(back, [0.0, 0.5, -0.5, 1.0], atol=1e-3)
    assert pcm16_to_float(b"\x00\x00\x01").shape == (1,)


def test_resample_identity_and_ratio():
    x = tone(24000, sr=24000)
    assert resample(x, 24000, 24000) is not None
    assert resample(x, 24000, 16000).shape == (16000,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_audio.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.common.audio'`

- [ ] **Step 3: Write minimal implementation**

`src/services/common/audio.py`:

```python
"""Audio helpers: file decoding, PCM16 <-> float32, resampling."""
from __future__ import annotations

import io
import math

import numpy as np

TARGET_RATE = 16000


class AudioDecodeError(ValueError):
    pass


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio.astype(np.float32, copy=False)
    from scipy.signal import resample_poly

    g = math.gcd(src_rate, dst_rate)
    return resample_poly(audio, dst_rate // g, src_rate // g).astype(np.float32)


def decode_audio_file(data: bytes, target_rate: int = TARGET_RATE) -> np.ndarray:
    """Any libsndfile format (WAV/FLAC/OGG/MP3) -> float32 mono at target_rate."""
    if not data:
        raise AudioDecodeError("empty audio file")
    import soundfile as sf

    try:
        audio, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001 — libsndfile raises various types
        raise AudioDecodeError(f"cannot decode audio: {exc}") from exc
    if audio.shape[0] == 0:
        raise AudioDecodeError("audio has no samples")
    return resample(audio.mean(axis=1), int(rate), target_rate)


def pcm16_to_float(data: bytes) -> np.ndarray:
    usable = len(data) - (len(data) % 2)
    return np.frombuffer(data[:usable], dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_audio.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/common/audio.py tests/services/test_svc_audio.py
git commit -m "feat(services): audio decode/resample/PCM helpers"
```

---

### Task 7: ASR: спільний filter_repetitions (+ legacy імпортує його)

**Files:**
- Create: `src/services/asr/textfilter.py`
- Modify: `src/whisper/whisper_service.py`
- Test: `tests/services/test_svc_asr_textfilter.py`

**Interfaces:**
- Produces: `normalize_word(w) -> str`, `filter_repetitions(text) -> str` (логіка 1:1 з legacy `_filter_repetitions`).

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_asr_textfilter.py`:

```python
import pytest

from src.services.asr.textfilter import filter_repetitions


@pytest.mark.parametrize("text,expected", [
    ("hello hello hello", "hello"),
    ("I think think think", "I think"),
    ("я не знаю, я не знаю", "я не знаю,"),
    ("Добре. Добре", "Добре."),
    ("one two three", "one two three"),
    ("", ""),
    ("single", "single"),
])
def test_filter_repetitions(text, expected):
    assert filter_repetitions(text) == expected


def test_legacy_whisper_service_uses_shared_filter():
    pytest.importorskip("faster_whisper")
    from src.whisper import whisper_service

    assert whisper_service._filter_repetitions is filter_repetitions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_asr_textfilter.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.asr.textfilter'`

- [ ] **Step 3: Write minimal implementation**

`src/services/asr/textfilter.py`:

```python
"""Collapse Whisper repetition loops (moved from src/whisper/whisper_service.py)."""
from __future__ import annotations

import re
from typing import List


def normalize_word(w: str) -> str:
    """Strip leading/trailing punctuation for comparison."""
    return re.sub(r"^[^\w]+|[^\w]+$", "", w, flags=re.UNICODE).lower()


def filter_repetitions(text: str) -> str:
    """Collapse consecutive repeated words or short phrases (window up to 5 words).

    Examples:
        "hello hello hello"      -> "hello"
        "I think think think"    -> "I think"
        "я не знаю, я не знаю"  -> "я не знаю,"
        "Добре. Добре"           -> "Добре."
    """
    if not text:
        return text
    words = text.split()
    if len(words) < 2:
        return text

    normed = [normalize_word(w) for w in words]

    result: List[str] = []
    i = 0
    while i < len(words):
        result.append(words[i])
        moved = False
        for win in range(1, min(6, len(words) - i + 1)):
            seq_n = normed[i: i + win]
            j = i + win
            count = 0
            while j + win <= len(words) and normed[j: j + win] == seq_n:
                j += win
                count += 1
            if count > 0:
                if win > 1:
                    result.extend(words[i + 1: i + win])
                i = j
                moved = True
                break
        if not moved:
            i += 1

    return " ".join(result)
```

Потім у `src/whisper/whisper_service.py`:

1. Видалити функції `_normalize_word` і `_filter_repetitions` (зараз рядки 26–71, від `def _normalize_word(w: str) -> str:` до `return " ".join(result)` включно).
2. Видалити `import re` (рядок 4) і замінити `from typing import List, Optional` на `from typing import Optional` (обидва більше не використовуються).
3. Після `from ..core.runtime import get_runtime_config` додати:

```python
from ..services.asr.textfilter import filter_repetitions as _filter_repetitions
```

Виклик `_filter_repetitions(s.text.strip())` у `run_server` лишається без змін.

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_asr_textfilter.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/asr/textfilter.py src/whisper/whisper_service.py tests/services/test_svc_asr_textfilter.py
git commit -m "refactor(asr): move repetition filter to src/services/asr/textfilter"
```

---

### Task 8: ASR: VAD та Endpointer

**Files:**
- Create: `src/services/asr/vad.py`
- Create: `src/services/asr/endpointer.py`
- Test: `tests/services/test_svc_asr_vad.py`
- Test: `tests/services/test_svc_asr_endpointer.py`

**Interfaces:**
- Consumes: `pcm16_to_float` (Task 6).
- Produces: `WINDOW_SAMPLES=512`, `WINDOW_BYTES=1024`, `WINDOW_MS=32`; `VadModel` Protocol (`prob(window: bytes) -> float`, `reset()`); `EnergyVad(threshold_rms=0.008)`; `SileroVad()` (один на сесію, stateful); `make_vad() -> VadModel` (Silero, fallback Energy); події `START`, `END`, `DISCARD`, `SPLIT` (рядки); `EndpointConfig(threshold=0.5, min_speech_ms=400, endpoint_silence_ms=700, max_utterance_ms=10000, window_ms=32)`; `Endpointer(cfg)` з `step(prob) -> str | None`, `finish() -> str | None`, `reset()`, атрибутами `in_speech`, `speech_ms`, `silence_ms`, `utterance_ms`.
- Значення за замовчуванням узяті з edge-пайплайна `src/translation_system.py:362-367`.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_asr_vad.py`:

```python
import sys

from src.services.asr.vad import WINDOW_BYTES, EnergyVad, SileroVad, make_vad
from svc_testutil import silence_windows, speech_windows


def test_energy_vad():
    vad = EnergyVad()
    assert vad.prob(speech_windows(1)) == 1.0
    assert vad.prob(silence_windows(1)) == 0.0


def test_make_vad_prefers_silero():
    vad = make_vad()
    assert isinstance(vad, SileroVad)
    assert 0.0 <= vad.prob(bytes(WINDOW_BYTES)) <= 1.0


def test_make_vad_falls_back_to_energy(monkeypatch):
    monkeypatch.setitem(sys.modules, "pysilero_vad", None)
    assert isinstance(make_vad(), EnergyVad)
```

`tests/services/test_svc_asr_endpointer.py`:

```python
from src.services.asr.endpointer import DISCARD, END, SPLIT, START, EndpointConfig, Endpointer

CFG = EndpointConfig(threshold=0.5, min_speech_ms=400, endpoint_silence_ms=700, max_utterance_ms=10000)


def run(probs, cfg=CFG):
    ep = Endpointer(cfg)
    return [(i, e) for i, e in enumerate(ep.step(p) for p in probs) if e is not None], ep


def test_speech_then_silence_ends_utterance():
    events, _ = run([0.0] * 3 + [0.9] * 20 + [0.1] * 22)
    # 22 silent windows * 32 ms = 704 ms >= 700 ms
    assert events == [(3, START), (44, END)]


def test_short_noise_is_discarded():
    events, _ = run([0.9] * 5 + [0.0] * 22)  # 160 ms of speech < 400 ms
    assert events == [(0, START), (26, DISCARD)]


def test_short_pause_does_not_end_utterance():
    events, _ = run([0.9] * 15 + [0.0] * 10 + [0.9] * 15 + [0.0] * 22)
    assert [e for _, e in events] == [START, END]


def test_max_length_splits_and_continues():
    cfg = EndpointConfig(max_utterance_ms=1024)
    events, ep = run([0.9] * 70, cfg)  # 32 windows = 1024 ms
    assert events == [(0, START), (31, SPLIT), (63, SPLIT)]
    assert ep.in_speech


def test_finish():
    _, ep = run([0.9] * 20)
    assert ep.finish() == END
    assert ep.finish() is None
    _, ep = run([0.9] * 3)
    assert ep.finish() == DISCARD
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_asr_vad.py tests/services/test_svc_asr_endpointer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.asr.vad'`

- [ ] **Step 3: Write minimal implementation**

`src/services/asr/vad.py`:

```python
"""Per-session voice activity detection on 512-sample (32 ms) PCM16 windows."""
from __future__ import annotations

from typing import Protocol

import numpy as np
from loguru import logger

from ..common.audio import pcm16_to_float

WINDOW_SAMPLES = 512
WINDOW_BYTES = WINDOW_SAMPLES * 2
WINDOW_MS = 32


class VadModel(Protocol):
    def prob(self, window: bytes) -> float: ...

    def reset(self) -> None: ...


class EnergyVad:
    """RMS fallback (same threshold as the edge pipeline); deterministic for tests."""

    def __init__(self, threshold_rms: float = 0.008) -> None:
        self._threshold = threshold_rms

    def prob(self, window: bytes) -> float:
        x = pcm16_to_float(window)
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        return 1.0 if rms >= self._threshold else 0.0

    def reset(self) -> None:
        pass


class SileroVad:
    def __init__(self) -> None:
        from pysilero_vad import SileroVoiceActivityDetector

        self._detector = SileroVoiceActivityDetector()

    def prob(self, window: bytes) -> float:
        return float(self._detector.process_chunk(window))

    def reset(self) -> None:
        self._detector.reset()


def make_vad() -> VadModel:
    try:
        return SileroVad()
    except Exception as exc:  # noqa: BLE001 — ImportError or model load failure
        logger.warning("Silero VAD unavailable ({}), using energy VAD", exc)
        return EnergyVad()
```

`src/services/asr/endpointer.py`:

```python
"""Speech endpointing state machine over per-window speech probabilities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .vad import WINDOW_MS

START = "start"      # speech began in this window
END = "end"          # utterance finished (silence or stop)
DISCARD = "discard"  # too little speech — drop the utterance
SPLIT = "split"      # max length reached — finish and continue with a new utterance


@dataclass(frozen=True)
class EndpointConfig:
    threshold: float = 0.5
    min_speech_ms: int = 400
    endpoint_silence_ms: int = 700
    max_utterance_ms: int = 10000
    window_ms: int = WINDOW_MS


class Endpointer:
    def __init__(self, cfg: EndpointConfig) -> None:
        self.cfg = cfg
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.utterance_ms = 0

    def step(self, prob: float) -> Optional[str]:
        c = self.cfg
        speech = prob >= c.threshold
        if not self.in_speech:
            if not speech:
                return None
            self.in_speech = True
            self.speech_ms = c.window_ms
            self.silence_ms = 0
            self.utterance_ms = c.window_ms
            return START
        self.utterance_ms += c.window_ms
        if speech:
            self.speech_ms += c.window_ms
            self.silence_ms = 0
        else:
            self.silence_ms += c.window_ms
        if self.silence_ms >= c.endpoint_silence_ms:
            event = END if self.speech_ms >= c.min_speech_ms else DISCARD
            self.reset()
            return event
        if self.utterance_ms >= c.max_utterance_ms:
            if self.speech_ms >= c.min_speech_ms:
                self.speech_ms = 0
                self.silence_ms = 0
                self.utterance_ms = 0
                return SPLIT
            self.reset()
            return DISCARD
        return None

    def finish(self) -> Optional[str]:
        if not self.in_speech:
            return None
        event = END if self.speech_ms >= self.cfg.min_speech_ms else DISCARD
        self.reset()
        return event
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_asr_vad.py tests/services/test_svc_asr_endpointer.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/asr/vad.py src/services/asr/endpointer.py tests/services/test_svc_asr_vad.py tests/services/test_svc_asr_endpointer.py
git commit -m "feat(asr): per-session VAD and endpointing state machine"
```

---

### Task 9: ASR: StreamSession (partial/final дії, pre-roll)

**Files:**
- Create: `src/services/asr/stream.py`
- Test: `tests/services/test_svc_asr_stream_session.py`

**Interfaces:**
- Consumes: `Endpointer`, `EndpointConfig`, події (Task 8); `WINDOW_BYTES`, `WINDOW_SAMPLES`, `VadModel` (Task 8); `TARGET_RATE`, `pcm16_to_float` (Task 6).
- Produces: frozen dataclass `PartialRequest(utterance_id: int, audio: np.ndarray, start: float)`, `FinalRequest(utterance_id: int, audio: np.ndarray, start: float, end: float)`, `Action = PartialRequest | FinalRequest`; `StreamSession(vad, cfg, *, partials: bool, partial_interval_ms: int, preroll_ms: int)` з `feed(data: bytes) -> list[Action]` і `finish() -> list[Action]`. `audio` у partial — **весь** буфер репліки (re-decode зростаючого буфера); `start`/`end` — секунди від початку сесії.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_asr_stream_session.py`:

```python
from src.services.asr.endpointer import EndpointConfig
from src.services.asr.stream import FinalRequest, PartialRequest, StreamSession
from src.services.asr.vad import EnergyVad
from svc_testutil import silence_windows, speech_windows


def session(partials=True, cfg=EndpointConfig(), preroll_ms=288):
    return StreamSession(EnergyVad(), cfg, partials=partials, partial_interval_ms=600, preroll_ms=preroll_ms)


def test_single_utterance_with_preroll_partials_and_final():
    s = session()
    actions = s.feed(silence_windows(16) + speech_windows(32) + silence_windows(25))
    finals = [a for a in actions if isinstance(a, FinalRequest)]
    partials = [a for a in actions if isinstance(a, PartialRequest)]
    assert len(finals) == 1
    final = finals[0]
    assert final.utterance_id == 1
    # preroll: 288 ms = 9 windows before the first speech window (window 16)
    assert final.start == (16 - 9) * 512 / 16000
    assert final.end == (16 + 32 + 22) * 512 / 16000
    assert len(final.audio) == (9 + 32 + 22) * 512
    assert len(partials) >= 2
    assert all(p.utterance_id == 1 and p.start == final.start for p in partials)
    # first partial once >= 400 ms of speech (13 windows) and >= 600 ms buffered
    assert len(partials[0].audio) == (9 + 13) * 512
    assert actions.index(final) == len(actions) - 1


def test_feed_in_odd_sized_chunks_is_equivalent():
    data = silence_windows(16) + speech_windows(32) + silence_windows(25)
    s = session()
    actions = []
    for i in range(0, len(data), 333):
        actions.extend(s.feed(data[i:i + 333]))
    finals = [a for a in actions if isinstance(a, FinalRequest)]
    assert len(finals) == 1 and len(finals[0].audio) == (9 + 32 + 22) * 512


def test_two_utterances_get_increasing_ids():
    s = session(partials=False)
    actions = s.feed(speech_windows(20) + silence_windows(25) + speech_windows(20) + silence_windows(25))
    assert [a.utterance_id for a in actions] == [1, 2]
    assert actions[1].start >= actions[0].end


def test_noise_produces_nothing():
    assert session().feed(speech_windows(5) + silence_windows(30)) == []


def test_partials_disabled():
    actions = session(partials=False).feed(speech_windows(40) + silence_windows(25))
    assert [type(a) for a in actions] == [FinalRequest]


def test_split_at_max_length():
    s = session(partials=False, cfg=EndpointConfig(max_utterance_ms=1024))
    actions = s.feed(speech_windows(70))
    assert [a.utterance_id for a in actions] == [1, 2]
    assert actions[1].start == actions[0].end


def test_finish_flushes_open_utterance_including_tail():
    s = session(partials=False, preroll_ms=0)
    assert s.feed(speech_windows(20) + b"\x01\x00" * 100) == []
    actions = s.finish()
    assert len(actions) == 1 and len(actions[0].audio) == 20 * 512 + 100
    assert s.finish() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_asr_stream_session.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.asr.stream'`

- [ ] **Step 3: Write minimal implementation**

`src/services/asr/stream.py`:

```python
"""Synchronous streaming session: PCM bytes in, partial/final decode requests out."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Union

import numpy as np

from ..common.audio import TARGET_RATE, pcm16_to_float
from .endpointer import DISCARD, END, SPLIT, START, EndpointConfig, Endpointer
from .vad import WINDOW_BYTES, WINDOW_SAMPLES, VadModel


@dataclass(frozen=True)
class PartialRequest:
    utterance_id: int
    audio: np.ndarray
    start: float


@dataclass(frozen=True)
class FinalRequest:
    utterance_id: int
    audio: np.ndarray
    start: float
    end: float


Action = Union[PartialRequest, FinalRequest]


class StreamSession:
    def __init__(self, vad: VadModel, cfg: EndpointConfig, *, partials: bool,
                 partial_interval_ms: int, preroll_ms: int) -> None:
        self._vad = vad
        self._ep = Endpointer(cfg)
        self._min_speech_ms = cfg.min_speech_ms
        self._partials = partials
        self._partial_interval = partial_interval_ms * TARGET_RATE // 1000
        self._preroll: deque = deque(maxlen=preroll_ms // cfg.window_ms)
        self._pending = bytearray()
        self._utt: Optional[bytearray] = None
        self._utt_id = 0
        self._next_id = 1
        self._utt_start = 0          # sample index
        self._samples = 0            # samples consumed by VAD so far
        self._last_partial_len = 0   # utterance length (samples) at the last partial

    def feed(self, data: bytes) -> List[Action]:
        self._pending.extend(data)
        actions: List[Action] = []
        while len(self._pending) >= WINDOW_BYTES:
            window = bytes(self._pending[:WINDOW_BYTES])
            del self._pending[:WINDOW_BYTES]
            actions.extend(self._window(window))
        return actions

    def finish(self) -> List[Action]:
        event = self._ep.finish()
        actions: List[Action] = []
        if self._utt is not None:
            tail = bytes(self._pending[: len(self._pending) - len(self._pending) % 2])
            self._utt.extend(tail)
            self._samples += len(tail) // 2
            if event == END:
                actions.append(self._final())
            self._utt = None
        self._pending.clear()
        return actions

    def _begin(self, start_sample: int, initial: bytes) -> None:
        self._utt = bytearray(initial)
        self._utt_start = start_sample
        self._utt_id = self._next_id
        self._next_id += 1
        self._last_partial_len = 0

    def _final(self) -> FinalRequest:
        return FinalRequest(self._utt_id, pcm16_to_float(bytes(self._utt)),
                            self._utt_start / TARGET_RATE, self._samples / TARGET_RATE)

    def _window(self, window: bytes) -> List[Action]:
        event = self._ep.step(self._vad.prob(window))
        position = self._samples
        self._samples += WINDOW_SAMPLES
        if event == START:
            preroll = b"".join(self._preroll)
            self._preroll.clear()
            self._begin(position - len(preroll) // 2, preroll + window)
            return []
        if self._utt is None:
            if self._preroll.maxlen:
                self._preroll.append(window)
            return []
        self._utt.extend(window)
        if event in (END, SPLIT):
            final = self._final()
            if event == SPLIT:
                self._begin(self._samples, b"")
            else:
                self._utt = None
            return [final]
        if event == DISCARD:
            self._utt = None
            return []
        if self._partials and self._ep.speech_ms >= self._min_speech_ms:
            length = len(self._utt) // 2
            if length - self._last_partial_len >= self._partial_interval:
                self._last_partial_len = length
                return [PartialRequest(self._utt_id, pcm16_to_float(bytes(self._utt)),
                                       self._utt_start / TARGET_RATE)]
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_asr_stream_session.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/asr/stream.py tests/services/test_svc_asr_stream_session.py
git commit -m "feat(asr): streaming session producing partial/final decode requests"
```

---

### Task 10: ASR: settings, backend-інтерфейс, fake і faster-whisper backend

**Files:**
- Create: `src/services/asr/settings.py`
- Create: `src/services/asr/backend.py`
- Create: `src/services/asr/faster_whisper_backend.py`
- Test: `tests/services/test_svc_asr_backend.py`

**Interfaces:**
- Consumes: `BaseSettings`, `base_from_env`, `env_*` (Task 2); `TARGET_RATE` (Task 6).
- Produces: `ASR_BACKENDS = ("faster-whisper", "fake")`; `normalize_language(value) -> str | None` (`None`/`""`/`"auto"` → `None`, інакше lower-case); `AsrSettings(BaseSettings)` з полями `language, beam_size, vad_filter, vad_threshold, min_speech_ms, endpoint_silence_ms, max_utterance_s, preroll_ms, partials, partial_interval_ms, max_sessions, max_upload_mb` і `AsrSettings.from_env(env=os.environ)`; `DecodeOptions(language, prompt, temperature=0.0, beam_size=5, vad_filter=False, without_timestamps=False)`; `Segment(start, end, text)`; `Transcription(text, language, duration, segments)`; `AsrBackend` Protocol (`name`, `model_id`, `load()`, `transcribe(audio, opts) -> Transcription`); `FakeAsrBackend(model_id="fake", text_fn=None, load_delay=0.0, fail_on_load=None, decode_delay=0.0)` з `calls: list[tuple[int, DecodeOptions]]` і текстом `f"speech {duration:.2f}s"`; `make_backend(settings) -> AsrBackend`; `FasterWhisperBackend(settings)`, `VAD_PARAMETERS`.
- `filter_repetitions` backend **не** застосовує — це робить API-шар (Task 11/12).

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_asr_backend.py`:

```python
import sys
import types

import numpy as np

from src.services.asr.backend import DecodeOptions, FakeAsrBackend, make_backend
from src.services.asr.faster_whisper_backend import VAD_PARAMETERS, FasterWhisperBackend
from src.services.asr.settings import AsrSettings


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


def test_make_backend_selects_fake_and_faster_whisper():
    assert isinstance(make_backend(AsrSettings.from_env({"RT_ASR_BACKEND": "fake"})), FakeAsrBackend)
    assert isinstance(make_backend(AsrSettings.from_env({})), FasterWhisperBackend)


def test_faster_whisper_backend_maps_options(monkeypatch):
    created, calls = {}, []

    class WhisperModel:
        def __init__(self, model, device, compute_type):
            created.update(model=model, device=device, compute_type=compute_type)

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter([_Seg(0.0, 0.5, " Привіт "), _Seg(0.5, 1.0, "світ")]), types.SimpleNamespace(language="uk")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel))
    backend = FasterWhisperBackend(AsrSettings.from_env({"RT_DEVICE": "rocm", "RT_MODEL": "medium"}))
    backend.load()
    assert created == {"model": "medium", "device": "cuda", "compute_type": "float16"}
    result = backend.transcribe(np.zeros(16000, np.float32),
                                DecodeOptions(language=None, prompt="ctx", beam_size=2, vad_filter=True))
    assert result.text == "Привіт світ" and result.language == "uk" and result.duration == 1.0
    assert [s.text for s in result.segments] == ["Привіт", "світ"]
    kw = calls[0]
    assert kw["initial_prompt"] == "ctx" and kw["beam_size"] == 2 and kw["vad_parameters"] == VAD_PARAMETERS
    assert kw["condition_on_previous_text"] is False
    backend.transcribe(np.zeros(160, np.float32), DecodeOptions(language="en", prompt=None))
    assert "vad_parameters" not in calls[1] and calls[1]["initial_prompt"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_asr_backend.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.asr.backend'`

- [ ] **Step 3: Write minimal implementation**

`src/services/asr/settings.py`:

```python
"""rt-asr settings from env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from ..common.env import env_bool, env_float, env_int, env_str
from ..common.settings import BaseSettings, base_from_env

ASR_BACKENDS = ("faster-whisper", "fake")


def normalize_language(value: Optional[str]) -> Optional[str]:
    """None / "" / "auto" mean autodetect."""
    if value is None or value.strip() == "" or value.strip().lower() == "auto":
        return None
    return value.strip().lower()


@dataclass(frozen=True)
class AsrSettings(BaseSettings):
    language: Optional[str] = None
    beam_size: int = 5
    vad_filter: bool = True
    vad_threshold: float = 0.5
    min_speech_ms: int = 400
    endpoint_silence_ms: int = 700
    max_utterance_s: float = 10.0
    preroll_ms: int = 300
    partials: bool = True
    partial_interval_ms: int = 600
    max_sessions: int = 4
    max_upload_mb: int = 25

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "AsrSettings":
        base = base_from_env(env, service="rt-asr", backend_key="RT_ASR_BACKEND", backends=ASR_BACKENDS,
                             default_backend="faster-whisper", default_model="small",
                             compute_cpu="int8", compute_gpu="float16")
        return cls(
            **base,
            language=normalize_language(env_str(env, "RT_ASR_LANGUAGE", None)),
            beam_size=env_int(env, "RT_ASR_BEAM_SIZE", 5, min_value=1, max_value=10),
            vad_filter=env_bool(env, "RT_ASR_VAD", True),
            vad_threshold=env_float(env, "RT_ASR_VAD_THRESHOLD", 0.5, min_value=0.0, max_value=1.0),
            min_speech_ms=env_int(env, "RT_ASR_MIN_SPEECH_MS", 400, min_value=32),
            endpoint_silence_ms=env_int(env, "RT_ASR_ENDPOINT_SILENCE_MS", 700, min_value=96),
            max_utterance_s=env_float(env, "RT_ASR_MAX_UTTERANCE_S", 10.0, min_value=1.0, max_value=30.0),
            preroll_ms=env_int(env, "RT_ASR_PREROLL_MS", 300, min_value=0, max_value=2000),
            partials=env_bool(env, "RT_ASR_PARTIALS", True),
            partial_interval_ms=env_int(env, "RT_ASR_PARTIAL_INTERVAL_MS", 600, min_value=100),
            max_sessions=env_int(env, "RT_ASR_MAX_SESSIONS", 4, min_value=1),
            max_upload_mb=env_int(env, "RT_ASR_MAX_UPLOAD_MB", 25, min_value=1),
        )
```

`src/services/asr/backend.py`:

```python
"""ASR backend interface + fake backend."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol, Tuple

import numpy as np

from ..common.audio import TARGET_RATE
from .settings import AsrSettings


@dataclass(frozen=True)
class DecodeOptions:
    language: Optional[str]
    prompt: Optional[str]
    temperature: float = 0.0
    beam_size: int = 5
    vad_filter: bool = False
    without_timestamps: bool = False


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcription:
    text: str
    language: Optional[str]
    duration: float
    segments: List[Segment]


class AsrBackend(Protocol):
    name: str
    model_id: str

    def load(self) -> None: ...

    def transcribe(self, audio: np.ndarray, opts: DecodeOptions) -> Transcription: ...


class FakeAsrBackend:
    """Deterministic backend for tests and GPU-less edge development (RT_ASR_BACKEND=fake)."""

    name = "fake"

    def __init__(self, model_id: str = "fake",
                 text_fn: Optional[Callable[[np.ndarray, DecodeOptions], str]] = None,
                 load_delay: float = 0.0, fail_on_load: Optional[str] = None,
                 decode_delay: float = 0.0) -> None:
        self.model_id = model_id
        self._text_fn = text_fn
        self._load_delay = load_delay
        self._fail_on_load = fail_on_load
        self._decode_delay = decode_delay
        self.calls: List[Tuple[int, DecodeOptions]] = []

    def load(self) -> None:
        time.sleep(self._load_delay)
        if self._fail_on_load:
            raise RuntimeError(self._fail_on_load)

    def transcribe(self, audio: np.ndarray, opts: DecodeOptions) -> Transcription:
        self.calls.append((len(audio), opts))
        time.sleep(self._decode_delay)
        duration = len(audio) / TARGET_RATE
        text = self._text_fn(audio, opts) if self._text_fn else f"speech {duration:.2f}s"
        segments = [Segment(0.0, duration, text)] if text else []
        return Transcription(text=text, language=opts.language or "en", duration=duration, segments=segments)


def make_backend(settings: AsrSettings) -> AsrBackend:
    if settings.backend == "fake":
        return FakeAsrBackend(model_id=settings.model)
    from .faster_whisper_backend import FasterWhisperBackend

    return FasterWhisperBackend(settings)
```

`src/services/asr/faster_whisper_backend.py`:

```python
"""faster-whisper backend (decode params carried over from src/whisper/whisper_service.py)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from ..common.audio import TARGET_RATE
from .backend import DecodeOptions, Segment, Transcription
from .settings import AsrSettings

VAD_PARAMETERS = dict(threshold=0.35, min_speech_duration_ms=100, min_silence_duration_ms=500, speech_pad_ms=800)


class FasterWhisperBackend:
    name = "faster-whisper"

    def __init__(self, settings: AsrSettings) -> None:
        self._settings = settings
        self.model_id = settings.model
        self._model: Optional[Any] = None

    def load(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(self.model_id, device=self._settings.torch_device,
                                   compute_type=self._settings.compute_type)

    def transcribe(self, audio: np.ndarray, opts: DecodeOptions) -> Transcription:
        kwargs: dict = dict(
            language=opts.language,
            beam_size=opts.beam_size,
            temperature=opts.temperature,
            initial_prompt=opts.prompt or None,
            vad_filter=opts.vad_filter,
            no_speech_threshold=0.4,
            condition_on_previous_text=False,
            without_timestamps=opts.without_timestamps,
        )
        if opts.vad_filter:
            kwargs["vad_parameters"] = dict(VAD_PARAMETERS)
        segments, info = self._model.transcribe(audio, **kwargs)
        segs = [Segment(float(s.start), float(s.end), s.text.strip()) for s in segments]
        text = " ".join(s.text for s in segs if s.text).strip()
        language = getattr(info, "language", None) or opts.language
        return Transcription(text=text, language=language, duration=len(audio) / TARGET_RATE, segments=segs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_asr_backend.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/asr/settings.py src/services/asr/backend.py src/services/asr/faster_whisper_backend.py tests/services/test_svc_asr_backend.py
git commit -m "feat(asr): settings, backend protocol, fake and faster-whisper backends"
```

---

### Task 11: ASR: POST /v1/audio/transcriptions, app factory, entrypoint

**Files:**
- Create: `src/services/asr/http.py`
- Create: `src/services/asr/app.py`
- Create: `src/services/asr/__main__.py`
- Test: `tests/services/test_svc_asr_http.py`

**Interfaces:**
- Consumes: `ServiceRuntime`, `create_service_app`, `LifespanHook`, `run_service` (Task 5); `load_token`, `ApiError` (Task 4); `Priority` (Task 3); `decode_audio_file`, `AudioDecodeError` (Task 6); `filter_repetitions` (Task 7); `AsrSettings`, `normalize_language`, `DecodeOptions`, `make_backend` (Task 10).
- Produces: `build_http_router(settings, runtime) -> APIRouter`; `create_app(settings=None, backend_factory=None, lifespan_hooks=()) -> FastAPI` (Task 12 додає параметр `vad_factory` і WS-роутер); `python -m src.services.asr` → `main() -> int`.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_asr_http.py`:

```python
import pytest

from src.services.asr.app import create_app
from src.services.asr.backend import FakeAsrBackend, Segment, Transcription
from src.services.asr.settings import AsrSettings
from svc_testutil import running, tone, wav_file


def make(backend=None, **env):
    settings = AsrSettings.from_env({"RT_ASR_BACKEND": "fake", **env})
    backend = backend or FakeAsrBackend()
    return create_app(settings, backend_factory=lambda: backend), backend


def post(client, data=None, **form):
    data = wav_file(tone(16000)) if data is None else data
    return client.post("/v1/audio/transcriptions", files={"file": ("a.wav", data, "audio/wav")}, data=form)


def test_json_response_and_options_reach_backend():
    app, backend = make(RT_ASR_LANGUAGE="uk", RT_ASR_BEAM_SIZE="3")
    with running(app) as client:
        r = post(client, prompt="context", temperature="0.2")
    assert r.status_code == 200
    assert r.json() == {"text": "speech 1.00s", "language": "uk"}
    n, opts = backend.calls[0]
    assert n == 16000
    assert (opts.language, opts.prompt, opts.temperature, opts.beam_size, opts.vad_filter) == \
        ("uk", "context", 0.2, 3, True)


def test_request_language_overrides_default_and_auto_means_detect():
    app, backend = make(RT_ASR_LANGUAGE="uk")
    with running(app) as client:
        post(client, language="pl")
        post(client, language="auto")
    assert backend.calls[0][1].language == "pl"
    assert backend.calls[1][1].language == "uk"


def test_verbose_json_and_text_and_repetition_filter():
    backend = FakeAsrBackend()
    backend.transcribe = lambda audio, opts: Transcription(
        "hello hello hello", "en", 1.0, [Segment(0.0, 1.0, "hello hello hello")])
    app, _ = make(backend)
    with running(app) as client:
        verbose = post(client, response_format="verbose_json").json()
        text = post(client, response_format="text")
    assert verbose == {"text": "hello", "language": "en", "duration": 1.0,
                       "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "hello"}]}
    assert text.headers["content-type"].startswith("text/plain") and text.text == "hello"


@pytest.mark.parametrize("data,code,status", [
    (b"garbage", "invalid_audio", 400),
    (b"", "invalid_audio", 400),
])
def test_invalid_audio(data, code, status):
    app, _ = make()
    with running(app) as client:
        r = post(client, data=data)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_unsupported_format_and_upload_limit():
    app, _ = make(RT_ASR_MAX_UPLOAD_MB="1")
    with running(app) as client:
        assert post(client, response_format="srt").json()["error"]["code"] == "unsupported_format"
        big = post(client, data=b"\0" * (1024 * 1024 + 10))
    assert big.status_code == 413


def test_auth_applies_to_transcriptions(tmp_path):
    token = tmp_path / "api-token"
    token.write_text("t0k")
    app, _ = make(RT_API_TOKEN_FILE=str(token))
    with running(app) as client:
        assert post(client).status_code == 401
        ok = client.post("/v1/audio/transcriptions", headers={"Authorization": "Bearer t0k"},
                         files={"file": ("a.wav", wav_file(tone(1600)), "audio/wav")})
        assert ok.status_code == 200
        assert client.get("/status").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_asr_http.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.asr.app'`

- [ ] **Step 3: Write minimal implementation**

`src/services/asr/http.py`:

```python
"""POST /v1/audio/transcriptions (OpenAI-compatible)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import PlainTextResponse

from ..common.audio import AudioDecodeError, decode_audio_file
from ..common.errors import ApiError
from ..common.runtime import ServiceRuntime
from ..common.worker import Priority
from .backend import DecodeOptions
from .settings import AsrSettings, normalize_language
from .textfilter import filter_repetitions

RESPONSE_FORMATS = ("json", "verbose_json", "text")


def build_http_router(settings: AsrSettings, runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()
    limit = settings.max_upload_mb * 1024 * 1024

    @router.post("/audio/transcriptions")
    async def transcriptions(
        file: UploadFile = File(...),
        model: Optional[str] = Form(None),  # accepted for OpenAI compatibility, ignored
        language: Optional[str] = Form(None),
        prompt: Optional[str] = Form(None),
        temperature: float = Form(0.0, ge=0.0, le=1.0),
        response_format: str = Form("json"),
    ):
        if response_format not in RESPONSE_FORMATS:
            raise ApiError(400, "unsupported_format", f"response_format must be one of {RESPONSE_FORMATS}")
        backend = runtime.require_ready()
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise ApiError(413, "payload_too_large", f"file exceeds {settings.max_upload_mb} MB")
        try:
            audio = decode_audio_file(data)
        except AudioDecodeError as exc:
            raise ApiError(400, "invalid_audio", str(exc)) from exc
        opts = DecodeOptions(
            language=normalize_language(language) or settings.language,
            prompt=prompt or None,
            temperature=temperature,
            beam_size=settings.beam_size,
            vad_filter=settings.vad_filter,
        )
        result = await runtime.run(backend.transcribe, audio, opts, priority=Priority.REQUEST)
        text = filter_repetitions(result.text.strip())
        if response_format == "text":
            return PlainTextResponse(text)
        body = {"text": text, "language": result.language}
        if response_format == "verbose_json":
            body["duration"] = round(result.duration, 3)
            body["segments"] = [
                {"id": i, "start": round(s.start, 3), "end": round(s.end, 3), "text": filter_repetitions(s.text)}
                for i, s in enumerate(result.segments)
            ]
        return body

    return router
```

`src/services/asr/app.py`:

```python
"""rt-asr FastAPI app factory."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from fastapi import FastAPI

from ..common.app import LifespanHook, create_service_app
from ..common.auth import load_token
from ..common.runtime import ServiceRuntime
from .backend import AsrBackend, make_backend
from .http import build_http_router
from .settings import AsrSettings


def create_app(settings: Optional[AsrSettings] = None,
               backend_factory: Optional[Callable[[], AsrBackend]] = None,
               lifespan_hooks: Sequence[LifespanHook] = ()) -> FastAPI:
    settings = settings or AsrSettings.from_env()
    token = load_token(settings.api_token_file)
    runtime = ServiceRuntime(settings.service, backend_factory or (lambda: make_backend(settings)),
                             settings.max_pending)
    return create_service_app(
        settings=settings, runtime=runtime, token=token,
        routers=[build_http_router(settings, runtime)],
        lifespan_hooks=lifespan_hooks,
    )
```

`src/services/asr/__main__.py`:

```python
"""python -m src.services.asr"""
import sys

from ..common.serve import run_service
from .app import create_app
from .settings import AsrSettings


def main() -> int:
    return run_service(AsrSettings.from_env, lambda settings: create_app(settings))


if __name__ == "__main__":
    sys.exit(main())
```

Перевірка entrypoint вручну (після Step 4):

Run: `RT_PORT=18091 RT_ASR_BACKEND=fake timeout 5 nix develop -c python -m src.services.asr & sleep 4; curl -s localhost:18091/health; curl -s localhost:18091/status`
Expected: `{"status":"ok"}` і `/status` з `"ready":true`, `"backend":"fake"`.

Run: `RT_DEVICE=tpu nix develop -c python -m src.services.asr; echo exit=$?`
Expected: `configuration error: RT_DEVICE must be one of cpu, cuda, rocm, got 'tpu'` і `exit=2`.

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_asr_http.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/asr/http.py src/services/asr/app.py src/services/asr/__main__.py tests/services/test_svc_asr_http.py
git commit -m "feat(asr): OpenAI-compatible transcriptions endpoint and entrypoint"
```

---

### Task 12: ASR: WS /v1/audio/stream (server VAD, партіали, фінали)

**Files:**
- Create: `src/services/asr/ws.py`
- Modify: `src/services/asr/app.py`
- Test: `tests/services/test_svc_asr_ws.py`

**Interfaces:**
- Consumes: `StreamSession`, `PartialRequest`, `FinalRequest`, `Action` (Task 9); `EndpointConfig` (Task 8); `VadModel`, `make_vad` (Task 8); `ws_authorized` (Task 4); `ModelState`, `ServiceRuntime` (Task 5); `Priority`, `WorkerBusy` (Task 3); `DecodeOptions` (Task 10); `filter_repetitions` (Task 7).
- Produces: `ProtocolError(code, message)`; `StartMessage(language, prompt, partials)`; `parse_control(message) -> dict`; `parse_start(message) -> StartMessage`; `build_ws_router(settings, runtime, vad_factory, token) -> APIRouter`; `create_app(settings=None, backend_factory=None, vad_factory=None, lifespan_hooks=())`.
- Протокол (spec §6.2): `start` → `ready{session_id}`; binary PCM16 16 kHz; `partial{utterance_id,text,start}`; `final{utterance_id,text,start,end,language}`; `stop` → фінали → `done` → close 1000. Close-коди: 1008 auth, 1013 ліміт сесій / модель не готова, 1003 помилка протоколу.
- Партіали: одна in-flight на сесію (latest-wins), `Priority.PARTIAL`, `beam_size=1`, `without_timestamps=True`; при `WorkerBusy` — пропуск. Фінали: `Priority.FINAL`, `bypass_limit=True`, скасовують partial своєї репліки, надсилаються строго по порядку окремою корутиною. Контекст `prompt` = `start.prompt` + 3 останні фінали сесії (≤200 символів).

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_asr_ws.py`:

```python
import threading

import pytest
from starlette.websockets import WebSocketDisconnect

from src.services.asr.app import create_app
from src.services.asr.backend import FakeAsrBackend
from src.services.asr.settings import AsrSettings
from src.services.asr.vad import EnergyVad
from svc_testutil import running, silence_windows, speech_windows

START = {"type": "start", "language": "uk", "sample_rate": 16000}


def make(backend=None, **env):
    settings = AsrSettings.from_env({"RT_ASR_BACKEND": "fake", "RT_ASR_PREROLL_MS": "0", **env})
    backend = backend or FakeAsrBackend()
    return create_app(settings, backend_factory=lambda: backend, vad_factory=EnergyVad), backend


def collect_until_done(ws):
    messages = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] in ("done", "error"):
            return messages


def test_partial_then_final_then_done():
    app, backend = make()
    with running(app) as client, client.websocket_connect("/v1/audio/stream") as ws:
        ws.send_json(START)
        ready = ws.receive_json()
        assert ready["type"] == "ready" and len(ready["session_id"]) == 32
        ws.send_bytes(speech_windows(30))
        partial = ws.receive_json()
        assert partial["type"] == "partial" and partial["utterance_id"] == 1
        assert partial["text"].startswith("speech ") and partial["start"] == 0.0
        ws.send_bytes(silence_windows(25))
        ws.send_json({"type": "stop"})
        messages = [m for m in collect_until_done(ws) if m["type"] != "partial"]
    final, done = messages
    assert final == {"type": "final", "utterance_id": 1, "text": "speech 1.66s", "start": 0.0,
                     "end": 1.664, "language": "uk"}
    assert done == {"type": "done"}
    partial_opts = [o for _, o in backend.calls if o.beam_size == 1]
    final_opts = [o for _, o in backend.calls if o.beam_size == 5]
    assert partial_opts and partial_opts[0].without_timestamps and not partial_opts[0].vad_filter
    assert len(final_opts) == 1 and not final_opts[0].without_timestamps


def test_finals_in_order_and_context_prompt():
    backend = FakeAsrBackend(decode_delay=0.05)
    app, _ = make(backend, RT_ASR_PARTIALS="false")
    with running(app) as client, client.websocket_connect("/v1/audio/stream") as ws:
        ws.send_json({**START, "prompt": "Glossary"})
        ws.receive_json()
        for _ in range(3):
            ws.send_bytes(speech_windows(20) + silence_windows(25))
        ws.send_json({"type": "stop"})
        messages = collect_until_done(ws)
    assert [m["utterance_id"] for m in messages if m["type"] == "final"] == [1, 2, 3]
    assert backend.calls[0][1].prompt == "Glossary"


def test_stop_flushes_open_utterance():
    app, _ = make(RT_ASR_PARTIALS="false")
    with running(app) as client, client.websocket_connect("/v1/audio/stream") as ws:
        ws.send_json(START)
        ws.receive_json()
        ws.send_bytes(speech_windows(20))
        ws.send_json({"type": "stop"})
        messages = collect_until_done(ws)
    assert [m["type"] for m in messages] == ["final", "done"]


def test_sessions_are_independent():
    app, _ = make(RT_ASR_PARTIALS="false")
    with running(app) as client, \
            client.websocket_connect("/v1/audio/stream") as a, \
            client.websocket_connect("/v1/audio/stream") as b:
        a.send_json({**START, "language": "uk"})
        b.send_json({**START, "language": "pl"})
        a.receive_json()
        b.receive_json()
        for ws in (a, b):
            ws.send_bytes(speech_windows(20) + silence_windows(25))
            ws.send_json({"type": "stop"})
        assert collect_until_done(a)[0]["language"] == "uk"
        assert collect_until_done(b)[0]["language"] == "pl"


@pytest.mark.parametrize("first,code", [
    ({"type": "stop"}, "protocol_error"),
    ({"type": "start", "sample_rate": 48000}, "unsupported_sample_rate"),
    ({"type": "start", "sample_rate": "fast"}, "protocol_error"),
])
def test_protocol_errors(first, code):
    app, _ = make()
    with running(app) as client, client.websocket_connect("/v1/audio/stream") as ws:
        ws.send_json(first)
        assert ws.receive_json()["code"] == code
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1003


def test_binary_before_start_is_protocol_error():
    app, _ = make()
    with running(app) as client, client.websocket_connect("/v1/audio/stream") as ws:
        ws.send_bytes(speech_windows(1))
        assert ws.receive_json()["code"] == "protocol_error"


def test_session_limit():
    app, _ = make(RT_ASR_MAX_SESSIONS="1")
    with running(app) as client, client.websocket_connect("/v1/audio/stream") as first:
        first.send_json(START)
        first.receive_json()
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/v1/audio/stream"):
                pass
        assert exc.value.code == 1013


def test_model_not_ready():
    gate = threading.Event()

    class Slow(FakeAsrBackend):
        def load(self):
            gate.wait(5)

    app, _ = make(Slow())
    try:
        from fastapi.testclient import TestClient
        with TestClient(app) as client, client.websocket_connect("/v1/audio/stream") as ws:
            ws.send_json(START)
            assert ws.receive_json()["code"] == "model_not_ready"
    finally:
        gate.set()


def test_ws_requires_token(tmp_path):
    token = tmp_path / "api-token"
    token.write_text("t0k")
    app, _ = make(RT_API_TOKEN_FILE=str(token))
    with running(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/v1/audio/stream"):
                pass
        assert exc.value.code == 1008
        with client.websocket_connect("/v1/audio/stream", headers={"Authorization": "Bearer t0k"}) as ws:
            ws.send_json(START)
            assert ws.receive_json()["type"] == "ready"


def test_disconnect_without_stop_frees_session_slot():
    app, _ = make(RT_ASR_MAX_SESSIONS="1")
    with running(app) as client:
        with client.websocket_connect("/v1/audio/stream") as ws:
            ws.send_json(START)
            ws.receive_json()
            ws.send_bytes(speech_windows(30))
        # the first client vanished mid-utterance; a new session must be accepted
        with client.websocket_connect("/v1/audio/stream") as ws:
            ws.send_json(START)
            assert ws.receive_json()["type"] == "ready"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_asr_ws.py -q`
Expected: FAIL — `ImportError`/`TypeError: create_app() got an unexpected keyword argument 'vad_factory'`

- [ ] **Step 3: Write minimal implementation**

`src/services/asr/ws.py`:

```python
"""WS /v1/audio/stream — realtime transcription with server-side VAD."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from ..common.auth import ws_authorized
from ..common.runtime import ModelState, ServiceRuntime
from ..common.worker import Priority, WorkerBusy
from .backend import DecodeOptions
from .endpointer import EndpointConfig
from .settings import AsrSettings, normalize_language
from .stream import Action, FinalRequest, PartialRequest, StreamSession
from .textfilter import filter_repetitions
from .vad import VadModel

CONTEXT_FINALS = 3
CONTEXT_CHARS = 200


class ProtocolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StartMessage:
    language: Optional[str]
    prompt: Optional[str]
    partials: bool


def parse_control(message: dict) -> dict:
    if message.get("text") is None:
        raise ProtocolError("protocol_error", "expected a JSON control message")
    try:
        data = json.loads(message["text"])
    except json.JSONDecodeError as exc:
        raise ProtocolError("protocol_error", f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or "type" not in data:
        raise ProtocolError("protocol_error", "control message must be an object with 'type'")
    return data


def parse_start(message: dict) -> StartMessage:
    data = parse_control(message)
    if data["type"] != "start":
        raise ProtocolError("protocol_error", "first message must be {'type': 'start'}")
    try:
        rate = int(data.get("sample_rate", 16000))
    except (TypeError, ValueError) as exc:
        raise ProtocolError("protocol_error", "sample_rate must be an integer") from exc
    if rate != 16000:
        raise ProtocolError("unsupported_sample_rate", "only 16000 Hz PCM16 mono is supported")
    return StartMessage(language=normalize_language(data.get("language")),
                        prompt=data.get("prompt") or None,
                        partials=bool(data.get("partials", True)))


class _Session:
    """Per-connection state: decode scheduling, ordering of finals, rolling context."""

    def __init__(self, ws: WebSocket, settings: AsrSettings, runtime: ServiceRuntime, backend: Any,
                 start: StartMessage) -> None:
        self.ws = ws
        self.settings = settings
        self.runtime = runtime
        self.backend = backend
        self.start = start
        self.language = start.language or settings.language
        self.send_lock = asyncio.Lock()
        self.finals: "asyncio.Queue[Optional[Tuple[FinalRequest, concurrent.futures.Future]]]" = asyncio.Queue()
        self.finalized_id = 0
        self.partial: Optional[Tuple[int, concurrent.futures.Future]] = None
        self.context: List[str] = []
        self.tasks: List[asyncio.Task] = []

    async def send(self, payload: dict) -> None:
        async with self.send_lock:
            await self.ws.send_json(payload)

    def prompt(self) -> Optional[str]:
        parts = ([self.start.prompt] if self.start.prompt else []) + self.context[-CONTEXT_FINALS:]
        text = " ".join(parts).strip()
        return text[-CONTEXT_CHARS:] if text else None

    def options(self, final: bool) -> DecodeOptions:
        return DecodeOptions(language=self.language, prompt=self.prompt(), temperature=0.0,
                             beam_size=self.settings.beam_size if final else 1,
                             vad_filter=False, without_timestamps=not final)

    def dispatch(self, actions: List[Action]) -> None:
        for action in actions:
            if isinstance(action, FinalRequest):
                self._final(action)
            else:
                self._partial(action)

    def _partial(self, req: PartialRequest) -> None:
        if self.partial is not None and not self.partial[1].done():
            return  # latest-wins: one partial in flight per session
        try:
            fut = self.runtime.submit_threadsafe(self.backend.transcribe, req.audio, self.options(final=False),
                                                 priority=Priority.PARTIAL)
        except WorkerBusy:
            return
        self.partial = (req.utterance_id, fut)
        self.tasks.append(asyncio.create_task(self._deliver_partial(req, fut)))

    async def _deliver_partial(self, req: PartialRequest, fut: concurrent.futures.Future) -> None:
        try:
            result = await asyncio.wrap_future(fut)
        except (asyncio.CancelledError, concurrent.futures.CancelledError):
            return
        except Exception:  # noqa: BLE001 — partial failures are not fatal
            logger.exception("partial decode failed")
            return
        text = filter_repetitions(result.text.strip())
        if text and req.utterance_id > self.finalized_id:
            await self.send({"type": "partial", "utterance_id": req.utterance_id, "text": text,
                             "start": round(req.start, 3)})

    def _final(self, req: FinalRequest) -> None:
        self.finalized_id = req.utterance_id
        if self.partial is not None and self.partial[0] == req.utterance_id:
            self.partial[1].cancel()
        fut = self.runtime.submit_threadsafe(self.backend.transcribe, req.audio, self.options(final=True),
                                             priority=Priority.FINAL, bypass_limit=True)
        self.finals.put_nowait((req, fut))

    async def send_finals(self) -> None:
        while True:
            item = await self.finals.get()
            if item is None:
                return
            req, fut = item
            try:
                result = await asyncio.wrap_future(fut)
            except Exception as exc:  # noqa: BLE001
                logger.exception("final decode failed")
                await self.send({"type": "error", "code": "decode_failed", "message": str(exc)})
                continue
            text = filter_repetitions(result.text.strip())
            if not text:
                continue
            self.context.append(text)
            await self.send({"type": "final", "utterance_id": req.utterance_id, "text": text,
                             "start": round(req.start, 3), "end": round(req.end, 3),
                             "language": result.language})

    def cancel_all(self) -> None:
        if self.partial is not None:
            self.partial[1].cancel()
        for task in self.tasks:
            task.cancel()


def build_ws_router(settings: AsrSettings, runtime: ServiceRuntime, vad_factory: Callable[[], VadModel],
                    token: Optional[str]) -> APIRouter:
    router = APIRouter()
    active = {"sessions": 0}
    endpoint_cfg = EndpointConfig(threshold=settings.vad_threshold, min_speech_ms=settings.min_speech_ms,
                                  endpoint_silence_ms=settings.endpoint_silence_ms,
                                  max_utterance_ms=int(settings.max_utterance_s * 1000))

    async def fail(ws: WebSocket, code: str, message: str, close_code: int) -> None:
        await ws.send_json({"type": "error", "code": code, "message": message})
        await ws.close(code=close_code)

    async def run(ws: WebSocket) -> None:
        try:
            start = parse_start(await ws.receive())
        except ProtocolError as exc:
            await fail(ws, exc.code, exc.message, 1003)
            return
        if runtime.state is not ModelState.READY:
            await fail(ws, "model_not_ready", f"model state: {runtime.state.value}", 1013)
            return
        stream = StreamSession(vad_factory(), endpoint_cfg, partials=settings.partials and start.partials,
                               partial_interval_ms=settings.partial_interval_ms, preroll_ms=settings.preroll_ms)
        session = _Session(ws, settings, runtime, runtime.backend, start)
        await session.send({"type": "ready", "session_id": uuid.uuid4().hex})
        sender = asyncio.create_task(session.send_finals())
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(message.get("code", 1000))
                if message.get("bytes") is not None:
                    session.dispatch(stream.feed(message["bytes"]))
                    continue
                try:
                    control = parse_control(message)
                except ProtocolError as exc:
                    await fail(ws, exc.code, exc.message, 1003)
                    return
                if control["type"] != "stop":
                    await fail(ws, "protocol_error", f"unexpected message type {control['type']!r}", 1003)
                    return
                session.dispatch(stream.finish())
                break
            session.finals.put_nowait(None)
            await sender
            await session.send({"type": "done"})
            await ws.close(code=1000)
        finally:
            if not sender.done():
                sender.cancel()
            session.cancel_all()

    @router.websocket("/audio/stream")
    async def audio_stream(ws: WebSocket) -> None:
        if not ws_authorized(ws, token):
            await ws.close(code=1008)
            return
        if active["sessions"] >= settings.max_sessions:
            await ws.close(code=1013)
            return
        active["sessions"] += 1
        try:
            await ws.accept()
            await run(ws)
        except WebSocketDisconnect:
            logger.debug("client disconnected")
        finally:
            active["sessions"] -= 1

    return router
```

Замінити `src/services/asr/app.py` повністю (додано `vad_factory` і WS-роутер):

`src/services/asr/app.py`:

```python
"""rt-asr FastAPI app factory."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from fastapi import FastAPI

from ..common.app import LifespanHook, create_service_app
from ..common.auth import load_token
from ..common.runtime import ServiceRuntime
from .backend import AsrBackend, make_backend
from .http import build_http_router
from .settings import AsrSettings
from .vad import VadModel, make_vad
from .ws import build_ws_router


def create_app(settings: Optional[AsrSettings] = None,
               backend_factory: Optional[Callable[[], AsrBackend]] = None,
               vad_factory: Optional[Callable[[], VadModel]] = None,
               lifespan_hooks: Sequence[LifespanHook] = ()) -> FastAPI:
    settings = settings or AsrSettings.from_env()
    token = load_token(settings.api_token_file)
    runtime = ServiceRuntime(settings.service, backend_factory or (lambda: make_backend(settings)),
                             settings.max_pending)
    return create_service_app(
        settings=settings, runtime=runtime, token=token,
        routers=[build_http_router(settings, runtime)],
        ws_routers=[build_ws_router(settings, runtime, vad_factory or make_vad, token)],
        lifespan_hooks=lifespan_hooks,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_asr_ws.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/asr/ws.py src/services/asr/app.py tests/services/test_svc_asr_ws.py
git commit -m "feat(asr): realtime WebSocket stream with server-side VAD and partials"
```

---

### Task 13: MT: мови, settings, backend-и (fake + transformers NLLB)

**Files:**
- Create: `src/services/mt/languages.py`
- Create: `src/services/mt/settings.py`
- Create: `src/services/mt/backend.py`
- Create: `src/services/mt/transformers_backend.py`
- Test: `tests/services/test_svc_mt_backend.py`

**Interfaces:**
- Consumes: `BaseSettings`, `base_from_env`, `env_*`, `SettingsError` (Task 2).
- Produces: `LANGUAGES: dict[iso, (nllb, name)]`; `FLORES_RE`; `UnsupportedLanguage(ValueError)`; `to_nllb(code) -> str`; `list_languages() -> list[dict]`; `MT_BACKENDS = ("transformers", "fake")`; `MtSettings(BaseSettings)` (`source_lang, target_lang, num_beams, max_length, repetition_penalty, max_batch, max_chars`) + `MtSettings.from_env(env=os.environ)` (валідує мови за замовчуванням); `MtBackend` Protocol (`translate(texts, src_nllb, tgt_nllb, num_beams) -> list[str]`); `FakeMtBackend(model_id="fake", load_delay=0.0, fail_on_load=None)` (`f"[{tgt}] {text}"`, `calls`); `make_backend(settings)`; `TransformersNllbBackend(settings)` — `tokenizer.convert_tokens_to_ids(code)` замість видаленого у transformers 5 `lang_code_to_id`; `dtype=` (не `torch_dtype=`); `tokenizer.src_lang = src` під серіалізацією inference-потоку; мікробатчі по 16; невідомий токенізатору код → `UnsupportedLanguage`.
- Підпроєкт 5 додасть `ctranslate2_backend.py` з тим самим `MtBackend` і значенням `RT_MT_BACKEND=ctranslate2`.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_mt_backend.py`:

```python
from src.services.mt.backend import FakeMtBackend, make_backend
from src.services.mt.settings import MtSettings
from src.services.mt.transformers_backend import TransformersNllbBackend


def test_make_backend():
    assert isinstance(make_backend(MtSettings.from_env({"RT_MT_BACKEND": "fake"})), FakeMtBackend)
    assert isinstance(make_backend(MtSettings.from_env({})), TransformersNllbBackend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_mt_backend.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.mt.backend'`

- [ ] **Step 3: Write minimal implementation**

`src/services/mt/languages.py`:

```python
"""ISO 639-1 <-> NLLB-200 (FLORES-200) language codes."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

LANGUAGES: Dict[str, Tuple[str, str]] = {
    "af": ("afr_Latn", "Afrikaans"),
    "am": ("amh_Ethi", "Amharic"),
    "ar": ("arb_Arab", "Arabic"),
    "az": ("azj_Latn", "Azerbaijani"),
    "be": ("bel_Cyrl", "Belarusian"),
    "bg": ("bul_Cyrl", "Bulgarian"),
    "bn": ("ben_Beng", "Bengali"),
    "bs": ("bos_Latn", "Bosnian"),
    "ca": ("cat_Latn", "Catalan"),
    "cs": ("ces_Latn", "Czech"),
    "cy": ("cym_Latn", "Welsh"),
    "da": ("dan_Latn", "Danish"),
    "de": ("deu_Latn", "German"),
    "el": ("ell_Grek", "Greek"),
    "en": ("eng_Latn", "English"),
    "es": ("spa_Latn", "Spanish"),
    "et": ("est_Latn", "Estonian"),
    "fa": ("pes_Arab", "Persian"),
    "fi": ("fin_Latn", "Finnish"),
    "fr": ("fra_Latn", "French"),
    "ga": ("gle_Latn", "Irish"),
    "gl": ("glg_Latn", "Galician"),
    "he": ("heb_Hebr", "Hebrew"),
    "hi": ("hin_Deva", "Hindi"),
    "hr": ("hrv_Latn", "Croatian"),
    "hu": ("hun_Latn", "Hungarian"),
    "hy": ("hye_Armn", "Armenian"),
    "id": ("ind_Latn", "Indonesian"),
    "is": ("isl_Latn", "Icelandic"),
    "it": ("ita_Latn", "Italian"),
    "ja": ("jpn_Jpan", "Japanese"),
    "ka": ("kat_Geor", "Georgian"),
    "kk": ("kaz_Cyrl", "Kazakh"),
    "ko": ("kor_Hang", "Korean"),
    "lt": ("lit_Latn", "Lithuanian"),
    "lv": ("lvs_Latn", "Latvian"),
    "mk": ("mkd_Cyrl", "Macedonian"),
    "nl": ("nld_Latn", "Dutch"),
    "no": ("nob_Latn", "Norwegian Bokmal"),
    "pl": ("pol_Latn", "Polish"),
    "pt": ("por_Latn", "Portuguese"),
    "ro": ("ron_Latn", "Romanian"),
    "ru": ("rus_Cyrl", "Russian"),
    "sk": ("slk_Latn", "Slovak"),
    "sl": ("slv_Latn", "Slovenian"),
    "sq": ("als_Latn", "Albanian"),
    "sr": ("srp_Cyrl", "Serbian"),
    "sv": ("swe_Latn", "Swedish"),
    "sw": ("swh_Latn", "Swahili"),
    "ta": ("tam_Taml", "Tamil"),
    "th": ("tha_Thai", "Thai"),
    "tr": ("tur_Latn", "Turkish"),
    "uk": ("ukr_Cyrl", "Ukrainian"),
    "ur": ("urd_Arab", "Urdu"),
    "uz": ("uzn_Latn", "Uzbek"),
    "vi": ("vie_Latn", "Vietnamese"),
    "zh": ("zho_Hans", "Chinese (Simplified)"),
}

FLORES_RE = re.compile(r"^[a-z]{3}_[A-Z][a-z]{3}$")


class UnsupportedLanguage(ValueError):
    pass


def to_nllb(code: str) -> str:
    """ISO 639-1 ("uk") or a raw NLLB code ("ukr_Cyrl") -> NLLB code."""
    value = (code or "").strip()
    if FLORES_RE.match(value):
        return value
    entry = LANGUAGES.get(value.lower())
    if entry is None:
        raise UnsupportedLanguage(f"unsupported language {code!r}")
    return entry[0]


def list_languages() -> List[dict]:
    return [{"code": iso, "nllb": nllb, "name": name} for iso, (nllb, name) in sorted(LANGUAGES.items())]
```

`src/services/mt/settings.py`:

```python
"""rt-mt settings from env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from ..common.env import SettingsError, env_float, env_int, env_str
from ..common.settings import BaseSettings, base_from_env
from .languages import UnsupportedLanguage, to_nllb

MT_BACKENDS = ("transformers", "fake")


@dataclass(frozen=True)
class MtSettings(BaseSettings):
    source_lang: str = "uk"
    target_lang: str = "en"
    num_beams: int = 4
    max_length: int = 256
    repetition_penalty: float = 1.2
    max_batch: int = 64
    max_chars: int = 2000

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "MtSettings":
        base = base_from_env(env, service="rt-mt", backend_key="RT_MT_BACKEND", backends=MT_BACKENDS,
                             default_backend="transformers", default_model="facebook/nllb-200-distilled-600M",
                             compute_cpu="float32", compute_gpu="float16")
        source = env_str(env, "RT_MT_SOURCE_LANG", "uk")
        target = env_str(env, "RT_MT_TARGET_LANG", "en")
        for key, value in (("RT_MT_SOURCE_LANG", source), ("RT_MT_TARGET_LANG", target)):
            try:
                to_nllb(value)
            except UnsupportedLanguage as exc:
                raise SettingsError(f"{key}: {exc}") from exc
        return cls(
            **base,
            source_lang=source,
            target_lang=target,
            num_beams=env_int(env, "RT_MT_NUM_BEAMS", 4, min_value=1, max_value=8),
            max_length=env_int(env, "RT_MT_MAX_LENGTH", 256, min_value=8, max_value=1024),
            repetition_penalty=env_float(env, "RT_MT_REPETITION_PENALTY", 1.2, min_value=1.0, max_value=3.0),
            max_batch=env_int(env, "RT_MT_MAX_BATCH", 64, min_value=1),
            max_chars=env_int(env, "RT_MT_MAX_CHARS", 2000, min_value=1),
        )
```

`src/services/mt/backend.py`:

```python
"""MT backend interface + fake backend."""
from __future__ import annotations

import time
from typing import List, Optional, Protocol, Tuple

from .settings import MtSettings


class MtBackend(Protocol):
    name: str
    model_id: str

    def load(self) -> None: ...

    def translate(self, texts: List[str], src: str, tgt: str, num_beams: int) -> List[str]: ...


class FakeMtBackend:
    name = "fake"

    def __init__(self, model_id: str = "fake", load_delay: float = 0.0,
                 fail_on_load: Optional[str] = None) -> None:
        self.model_id = model_id
        self._load_delay = load_delay
        self._fail_on_load = fail_on_load
        self.calls: List[Tuple[List[str], str, str, int]] = []

    def load(self) -> None:
        time.sleep(self._load_delay)
        if self._fail_on_load:
            raise RuntimeError(self._fail_on_load)

    def translate(self, texts: List[str], src: str, tgt: str, num_beams: int) -> List[str]:
        self.calls.append((list(texts), src, tgt, num_beams))
        return [f"[{tgt}] {t}" for t in texts]


def make_backend(settings: MtSettings) -> MtBackend:
    if settings.backend == "fake":
        return FakeMtBackend(model_id=settings.model)
    from .transformers_backend import TransformersNllbBackend

    return TransformersNllbBackend(settings)
```

`src/services/mt/transformers_backend.py`:

```python
"""NLLB-200 via HuggingFace transformers (sub-project 5 adds a CTranslate2 twin)."""
from __future__ import annotations

from typing import Any, List, Optional

from loguru import logger

from .languages import UnsupportedLanguage
from .settings import MtSettings

MICRO_BATCH = 16
MAX_INPUT_TOKENS = 512


class TransformersNllbBackend:
    name = "transformers"

    def __init__(self, settings: MtSettings) -> None:
        self._settings = settings
        self.model_id = settings.model
        self._tokenizer: Optional[Any] = None
        self._model: Optional[Any] = None

    def load(self) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        s = self._settings
        half = s.is_gpu and s.compute_type in ("float16", "int8_float16")
        if s.compute_type.startswith("int8") and not half:
            logger.warning("compute_type {} is not supported by the transformers backend; using float32",
                           s.compute_type)
        dtype = torch.float16 if half else torch.float32
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id, dtype=dtype).to(s.torch_device).eval()

    def _lang_id(self, code: str) -> int:
        token_id = self._tokenizer.convert_tokens_to_ids(code)
        if token_id is None or token_id == self._tokenizer.unk_token_id:
            raise UnsupportedLanguage(f"language {code!r} is not known to {self.model_id}")
        return token_id

    def translate(self, texts: List[str], src: str, tgt: str, num_beams: int) -> List[str]:
        import torch

        self._lang_id(src)
        bos = self._lang_id(tgt)
        tok, model = self._tokenizer, self._model
        tok.src_lang = src  # safe: all calls are serialised by the inference worker
        out: List[str] = []
        for i in range(0, len(texts), MICRO_BATCH):
            batch = texts[i: i + MICRO_BATCH]
            enc = tok(batch, return_tensors="pt", padding=True, truncation=True,
                      max_length=MAX_INPUT_TOKENS).to(model.device)
            with torch.inference_mode():
                generated = model.generate(**enc, forced_bos_token_id=bos, num_beams=num_beams,
                                           max_length=self._settings.max_length,
                                           repetition_penalty=self._settings.repetition_penalty,
                                           early_stopping=num_beams > 1)
            out.extend(t.strip() for t in tok.batch_decode(generated, skip_special_tokens=True))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_mt_backend.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/mt/languages.py src/services/mt/settings.py src/services/mt/backend.py src/services/mt/transformers_backend.py tests/services/test_svc_mt_backend.py
git commit -m "feat(mt): language map, settings and NLLB backends"
```

---

### Task 14: MT: POST /v1/translate, GET /v1/languages, app, entrypoint

**Files:**
- Create: `src/services/mt/http.py`
- Create: `src/services/mt/app.py`
- Create: `src/services/mt/__main__.py`
- Test: `tests/services/test_svc_mt.py`

**Interfaces:**
- Consumes: Task 13 (усе); `ServiceRuntime`, `create_service_app`, `LifespanHook`, `run_service` (Task 5); `ApiError`, `load_token` (Task 4); `Priority` (Task 3).
- Produces: `TranslateRequest` (pydantic: `text: str | list[str]`, `source_lang`, `target_lang`, `num_beams` 1..8); `build_http_router(settings, runtime)`; `create_app(settings=None, backend_factory=None, lifespan_hooks=())`; `python -m src.services.mt`.
- Відповідь: `{"translations": [..], "source_lang", "target_lang", "model": backend.model_id}` — коди мов відлунюються як передані; `translations` завжди список.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_mt.py`:

```python
import pytest

from src.services.common.env import SettingsError
from src.services.mt.app import create_app
from src.services.mt.backend import FakeMtBackend
from src.services.mt.languages import LANGUAGES, UnsupportedLanguage, list_languages, to_nllb
from src.services.mt.settings import MtSettings
from svc_testutil import running


def make(backend=None, **env):
    settings = MtSettings.from_env({"RT_MT_BACKEND": "fake", **env})
    backend = backend or FakeMtBackend()
    return create_app(settings, backend_factory=lambda: backend), backend


@pytest.mark.parametrize("code,nllb", [("uk", "ukr_Cyrl"), ("EN", "eng_Latn"), ("ar", "arb_Arab"),
                                       ("zh", "zho_Hans"), ("ukr_Cyrl", "ukr_Cyrl"), ("lij_Latn", "lij_Latn")])
def test_to_nllb(code, nllb):
    assert to_nllb(code) == nllb


@pytest.mark.parametrize("code", ["auto", "", "xx", "ukr_cyrl", "english"])
def test_to_nllb_rejects(code):
    with pytest.raises(UnsupportedLanguage):
        to_nllb(code)


def test_language_table_is_well_formed():
    assert len(LANGUAGES) >= 50
    langs = list_languages()
    assert [l["code"] for l in langs] == sorted(LANGUAGES)
    assert {"code": "uk", "nllb": "ukr_Cyrl", "name": "Ukrainian"} in langs


def test_settings_validate_default_languages():
    with pytest.raises(SettingsError):
        MtSettings.from_env({"RT_MT_SOURCE_LANG": "auto"})


def test_single_text_uses_env_defaults():
    app, backend = make(RT_MT_SOURCE_LANG="pl", RT_MT_TARGET_LANG="de", RT_MT_NUM_BEAMS="2")
    with running(app) as client:
        r = client.post("/v1/translate", json={"text": "Cześć"})
    assert r.status_code == 200
    assert r.json() == {"translations": ["[deu_Latn] Cześć"], "source_lang": "pl", "target_lang": "de",
                        "model": "fake"}
    assert backend.calls == [(["Cześć"], "pol_Latn", "deu_Latn", 2)]


def test_batch_keeps_order_and_skips_empty():
    app, backend = make()
    with running(app) as client:
        r = client.post("/v1/translate", json={"text": ["a", "", "  ", "b"], "source_lang": "uk",
                                               "target_lang": "en", "num_beams": 1})
    assert r.json()["translations"] == ["[eng_Latn] a", "", "", "[eng_Latn] b"]
    assert backend.calls == [(["a", "b"], "ukr_Cyrl", "eng_Latn", 1)]


def test_same_language_is_passthrough():
    app, backend = make()
    with running(app) as client:
        r = client.post("/v1/translate", json={"text": ["x"], "source_lang": "en", "target_lang": "eng_Latn"})
    assert r.json()["translations"] == ["x"] and backend.calls == []


def test_per_request_languages_do_not_leak_between_requests():
    app, backend = make()
    with running(app) as client:
        a = client.post("/v1/translate", json={"text": "t", "source_lang": "uk", "target_lang": "en"})
        b = client.post("/v1/translate", json={"text": "t", "source_lang": "uk", "target_lang": "pl"})
        c = client.post("/v1/translate", json={"text": "t"})
    assert [x.json()["translations"][0] for x in (a, b, c)] == ["[eng_Latn] t", "[pol_Latn] t", "[eng_Latn] t"]


@pytest.mark.parametrize("body,status,code", [
    ({"text": "x", "target_lang": "auto"}, 400, "unsupported_language"),
    ({"text": ["x"] * 3}, 413, "payload_too_large"),
    ({"text": "x" * 11}, 413, "payload_too_large"),
])
def test_validation_errors(body, status, code):
    app, _ = make(RT_MT_MAX_BATCH="2", RT_MT_MAX_CHARS="10")
    with running(app) as client:
        r = client.post("/v1/translate", json=body)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_pydantic_validation():
    app, _ = make()
    with running(app) as client:
        assert client.post("/v1/translate", json={"text": [1, {}]}).status_code == 422
        assert client.post("/v1/translate", json={"text": "x", "num_beams": 9}).status_code == 422


def test_languages_endpoint():
    app, _ = make()
    with running(app) as client:
        body = client.get("/v1/languages").json()
    assert {"code": "uk", "nllb": "ukr_Cyrl", "name": "Ukrainian"} in body["languages"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_mt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.mt.app'`

- [ ] **Step 3: Write minimal implementation**

`src/services/mt/http.py`:

```python
"""POST /v1/translate, GET /v1/languages."""
from __future__ import annotations

from typing import List, Optional, Union

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..common.errors import ApiError
from ..common.runtime import ServiceRuntime
from ..common.worker import Priority
from .languages import UnsupportedLanguage, list_languages, to_nllb
from .settings import MtSettings


class TranslateRequest(BaseModel):
    text: Union[str, List[str]]
    source_lang: Optional[str] = None
    target_lang: Optional[str] = None
    num_beams: Optional[int] = Field(default=None, ge=1, le=8)


def build_http_router(settings: MtSettings, runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/translate")
    async def translate(req: TranslateRequest) -> dict:
        backend = runtime.require_ready()
        texts = [req.text] if isinstance(req.text, str) else list(req.text)
        if len(texts) > settings.max_batch:
            raise ApiError(413, "payload_too_large", f"at most {settings.max_batch} texts per request")
        if any(len(t) > settings.max_chars for t in texts):
            raise ApiError(413, "payload_too_large", f"each text must be at most {settings.max_chars} characters")
        source = req.source_lang or settings.source_lang
        target = req.target_lang or settings.target_lang
        try:
            src, tgt = to_nllb(source), to_nllb(target)
        except UnsupportedLanguage as exc:
            raise ApiError(400, "unsupported_language", str(exc)) from exc
        if src == tgt:
            translations = list(texts)
        else:
            translations = [""] * len(texts)
            todo = [i for i, t in enumerate(texts) if t.strip()]
            if todo:
                try:
                    out = await runtime.run(backend.translate, [texts[i] for i in todo], src, tgt,
                                            req.num_beams or settings.num_beams, priority=Priority.REQUEST)
                except UnsupportedLanguage as exc:
                    raise ApiError(400, "unsupported_language", str(exc)) from exc
                for i, translated in zip(todo, out):
                    translations[i] = translated
        return {"translations": translations, "source_lang": source, "target_lang": target,
                "model": backend.model_id}

    @router.get("/languages")
    async def languages() -> dict:
        return {"languages": list_languages()}

    return router
```

`src/services/mt/app.py`:

```python
"""rt-mt FastAPI app factory."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from fastapi import FastAPI

from ..common.app import LifespanHook, create_service_app
from ..common.auth import load_token
from ..common.runtime import ServiceRuntime
from .backend import MtBackend, make_backend
from .http import build_http_router
from .settings import MtSettings


def create_app(settings: Optional[MtSettings] = None,
               backend_factory: Optional[Callable[[], MtBackend]] = None,
               lifespan_hooks: Sequence[LifespanHook] = ()) -> FastAPI:
    settings = settings or MtSettings.from_env()
    token = load_token(settings.api_token_file)
    runtime = ServiceRuntime(settings.service, backend_factory or (lambda: make_backend(settings)),
                             settings.max_pending)
    return create_service_app(settings=settings, runtime=runtime, token=token,
                              routers=[build_http_router(settings, runtime)], lifespan_hooks=lifespan_hooks)
```

`src/services/mt/__main__.py`:

```python
"""python -m src.services.mt"""
import sys

from ..common.serve import run_service
from .app import create_app
from .settings import MtSettings


def main() -> int:
    return run_service(MtSettings.from_env, lambda settings: create_app(settings))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_mt.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/mt/http.py src/services/mt/app.py src/services/mt/__main__.py tests/services/test_svc_mt.py
git commit -m "feat(mt): translate and languages endpoints"
```

---

### Task 15: TTS: голоси, settings, WAV-кодування, backend-и (fake + Kokoro)

**Files:**
- Create: `src/services/tts/voices.py`
- Create: `src/services/tts/settings.py`
- Create: `src/services/tts/encoding.py`
- Create: `src/services/tts/backend.py`
- Create: `src/services/tts/kokoro_backend.py`
- Test: `tests/services/test_svc_tts_kokoro_backend.py`

**Interfaces:**
- Consumes: `BaseSettings`, `base_from_env`, `env_*`, `SettingsError` (Task 2).
- Produces: `KOKORO_VOICES` (54 ids), `LANGUAGE_BY_PREFIX`, `GENDER_BY_LETTER`, `Voice(id, language, gender)`, `catalog() -> list[Voice]`; `TTS_BACKENDS = ("kokoro", "fake")`; `TtsSettings(BaseSettings)` (`voice, speed, sample_rate, max_chars`) + `from_env` (невідомий голос → `SettingsError`); `STREAMING_SIZE`, `wav_header(sample_rate, data_bytes | None) -> bytes`, `wav_bytes(pcm, sample_rate) -> bytes`; `UnsupportedVoiceLanguage(RuntimeError)`; `TtsBackend` Protocol (`name, model_id, sample_rate, load(), voices(), prepare_voice(voice), synthesize(text, voice, speed) -> Iterator[np.ndarray]`); `FakeTtsBackend(model_id="fake", load_delay=0.0, fail_on_load=None, unsupported_prefixes=())` (синус 440 Гц, 0.05 с × довжина речення / speed, амплітуда залежить від голосу; `calls`); `make_backend(settings)`; `KokoroBackend(settings)` (один `KModel`, кеш `KPipeline` на префікс голосу; `kokoro` імпортується лише в `load()`/`prepare_voice()`).

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_tts_kokoro_backend.py`:

```python
import sys
import types

import numpy as np
import pytest

from src.services.tts.backend import FakeTtsBackend, UnsupportedVoiceLanguage, make_backend
from src.services.tts.kokoro_backend import KokoroBackend
from src.services.tts.settings import TtsSettings


class _Tensor:
    def __init__(self, arr):
        self.arr = arr

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.arr


@pytest.fixture
def fake_kokoro(monkeypatch):
    created = []

    class KModel:
        def __init__(self, repo_id):
            self.repo_id = repo_id

        def to(self, device):
            self.device = device
            return self

        def eval(self):
            return self

    class KPipeline:
        def __init__(self, lang_code, repo_id, model):
            if lang_code == "j":
                raise ImportError("pyopenjtalk missing")
            created.append(lang_code)
            self.model = model

        def __call__(self, text, voice, speed):
            yield types.SimpleNamespace(audio=_Tensor(np.ones((1, 10), np.float32)))
            yield types.SimpleNamespace(audio=None)

    monkeypatch.setitem(sys.modules, "kokoro", types.SimpleNamespace(KModel=KModel, KPipeline=KPipeline))
    return created


def test_make_backend():
    assert isinstance(make_backend(TtsSettings.from_env({"RT_TTS_BACKEND": "fake"})), FakeTtsBackend)
    assert isinstance(make_backend(TtsSettings.from_env({})), KokoroBackend)


def test_kokoro_backend_caches_pipelines_per_language(fake_kokoro):
    backend = KokoroBackend(TtsSettings.from_env({"RT_DEVICE": "cuda"}))
    backend.load()
    assert fake_kokoro == ["a"] and backend._model.device == "cuda"
    chunks = list(backend.synthesize("hi", "af_bella", 1.0))
    assert len(chunks) == 1 and chunks[0].shape == (10,) and chunks[0].dtype == np.float32
    list(backend.synthesize("hi", "bf_emma", 1.0))
    list(backend.synthesize("hi", "am_adam", 1.0))
    assert fake_kokoro == ["a", "b"]


def test_kokoro_backend_unsupported_language(fake_kokoro):
    backend = KokoroBackend(TtsSettings.from_env({}))
    backend.load()
    with pytest.raises(UnsupportedVoiceLanguage):
        backend.prepare_voice("jf_alpha")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_tts_kokoro_backend.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.tts.backend'`

- [ ] **Step 3: Write minimal implementation**

`src/services/tts/voices.py`:

```python
"""Static voice catalogue of hexgrad/Kokoro-82M v1.0."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

KOKORO_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova",
    "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis", "ef_dora", "em_alex", "em_santa", "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola", "jf_alpha", "jf_gongitsune",
    "jf_nezumi", "jf_tebukuro", "jm_kumo", "pf_dora", "pm_alex", "pm_santa", "zf_xiaobei", "zf_xiaoni",
    "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
)

LANGUAGE_BY_PREFIX = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr", "h": "hi", "i": "it",
                      "j": "ja", "p": "pt-br", "z": "zh"}
GENDER_BY_LETTER = {"f": "female", "m": "male"}


@dataclass(frozen=True)
class Voice:
    id: str
    language: str
    gender: str


def catalog() -> List[Voice]:
    return [Voice(v, LANGUAGE_BY_PREFIX[v[0]], GENDER_BY_LETTER[v[1]]) for v in KOKORO_VOICES]
```

`src/services/tts/settings.py`:

```python
"""rt-tts settings from env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from ..common.env import SettingsError, env_float, env_int, env_str
from ..common.settings import BaseSettings, base_from_env
from .voices import KOKORO_VOICES

TTS_BACKENDS = ("kokoro", "fake")


@dataclass(frozen=True)
class TtsSettings(BaseSettings):
    voice: str = "af_heart"
    speed: float = 1.0
    sample_rate: int = 24000
    max_chars: int = 4096

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "TtsSettings":
        base = base_from_env(env, service="rt-tts", backend_key="RT_TTS_BACKEND", backends=TTS_BACKENDS,
                             default_backend="kokoro", default_model="hexgrad/Kokoro-82M",
                             compute_cpu="float32", compute_gpu="float32")
        voice = env_str(env, "RT_TTS_VOICE", "af_heart")
        if voice not in KOKORO_VOICES:
            raise SettingsError(f"RT_TTS_VOICE: unknown voice {voice!r}")
        return cls(
            **base,
            voice=voice,
            speed=env_float(env, "RT_TTS_SPEED", 1.0, min_value=0.25, max_value=4.0),
            sample_rate=env_int(env, "RT_TTS_SAMPLE_RATE", 24000, min_value=8000, max_value=48000),
            max_chars=env_int(env, "RT_TTS_MAX_CHARS", 4096, min_value=1),
        )
```

`src/services/tts/encoding.py`:

```python
"""PCM16 WAV encoding (complete and streaming)."""
from __future__ import annotations

import struct
from typing import Optional

STREAMING_SIZE = 0xFFFFFFFF


def wav_header(sample_rate: int, data_bytes: Optional[int]) -> bytes:
    """44-byte PCM16 mono header; data_bytes=None -> streaming header with 0xFFFFFFFF sizes."""
    data_size = STREAMING_SIZE if data_bytes is None else data_bytes
    riff_size = STREAMING_SIZE if data_bytes is None else 36 + data_bytes
    return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", riff_size, b"WAVE", b"fmt ", 16, 1, 1,
                       sample_rate, sample_rate * 2, 2, 16, b"data", data_size)


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    return wav_header(sample_rate, len(pcm)) + pcm
```

`src/services/tts/backend.py`:

```python
"""TTS backend interface + fake backend."""
from __future__ import annotations

import re
import time
from typing import Iterator, List, Optional, Protocol, Sequence, Tuple

import numpy as np

from .settings import TtsSettings
from .voices import Voice, catalog


class UnsupportedVoiceLanguage(RuntimeError):
    """The voice exists but its language pipeline cannot be built on this host."""


class TtsBackend(Protocol):
    name: str
    model_id: str
    sample_rate: int

    def load(self) -> None: ...

    def voices(self) -> List[Voice]: ...

    def prepare_voice(self, voice: str) -> None: ...

    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[np.ndarray]: ...


class FakeTtsBackend:
    name = "fake"
    sample_rate = 24000

    def __init__(self, model_id: str = "fake", load_delay: float = 0.0, fail_on_load: Optional[str] = None,
                 unsupported_prefixes: Sequence[str] = ()) -> None:
        self.model_id = model_id
        self._load_delay = load_delay
        self._fail_on_load = fail_on_load
        self._unsupported = tuple(unsupported_prefixes)
        self.calls: List[Tuple[str, str, float]] = []

    def load(self) -> None:
        time.sleep(self._load_delay)
        if self._fail_on_load:
            raise RuntimeError(self._fail_on_load)

    def voices(self) -> List[Voice]:
        return catalog()

    def prepare_voice(self, voice: str) -> None:
        if voice[0] in self._unsupported:
            raise UnsupportedVoiceLanguage(f"language pipeline {voice[0]!r} unavailable")

    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[np.ndarray]:
        self.calls.append((text, voice, speed))
        amplitude = 0.1 + (sum(map(ord, voice)) % 50) / 100.0
        for sentence in (s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s):
            n = max(1, int(self.sample_rate * 0.05 * len(sentence) / speed))
            t = np.arange(n) / self.sample_rate
            yield (amplitude * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)


def make_backend(settings: TtsSettings) -> TtsBackend:
    if settings.backend == "fake":
        return FakeTtsBackend(model_id=settings.model)
    from .kokoro_backend import KokoroBackend

    return KokoroBackend(settings)
```

`src/services/tts/kokoro_backend.py`:

```python
"""Kokoro-82M backend: one KModel, one cached KPipeline (G2P) per language prefix."""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

import numpy as np

from .backend import UnsupportedVoiceLanguage
from .settings import TtsSettings
from .voices import Voice, catalog


class KokoroBackend:
    name = "kokoro"
    sample_rate = 24000

    def __init__(self, settings: TtsSettings) -> None:
        self._settings = settings
        self.model_id = settings.model
        self._model: Optional[Any] = None
        self._pipelines: Dict[str, Any] = {}

    def load(self) -> None:
        from kokoro import KModel

        self._model = KModel(repo_id=self.model_id).to(self._settings.torch_device).eval()
        self.prepare_voice(self._settings.voice)

    def voices(self) -> List[Voice]:
        return catalog()

    def prepare_voice(self, voice: str) -> None:
        lang = voice[0]
        if lang in self._pipelines:
            return
        from kokoro import KPipeline

        try:
            self._pipelines[lang] = KPipeline(lang_code=lang, repo_id=self.model_id, model=self._model)
        except Exception as exc:  # noqa: BLE001 — missing G2P deps surface as various errors
            raise UnsupportedVoiceLanguage(f"voice {voice!r}: language pipeline {lang!r} unavailable: {exc}") from exc

    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[np.ndarray]:
        self.prepare_voice(voice)
        for result in self._pipelines[voice[0]](text, voice=voice, speed=speed):
            if result.audio is not None:
                yield result.audio.detach().cpu().numpy().astype(np.float32).reshape(-1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_tts_kokoro_backend.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/tts/voices.py src/services/tts/settings.py src/services/tts/encoding.py src/services/tts/backend.py src/services/tts/kokoro_backend.py tests/services/test_svc_tts_kokoro_backend.py
git commit -m "feat(tts): voices, settings, WAV encoding and Kokoro backend without playback"
```

---

### Task 16: TTS: POST /v1/audio/speech (wav|pcm, stream), GET /v1/audio/voices, app, entrypoint

**Files:**
- Create: `src/services/tts/http.py`
- Create: `src/services/tts/app.py`
- Create: `src/services/tts/__main__.py`
- Test: `tests/services/test_svc_tts.py`

**Interfaces:**
- Consumes: Task 15 (усе); `float_to_pcm16`, `resample` (Task 6); `ServiceRuntime`, `create_service_app`, `LifespanHook`, `run_service` (Task 5); `ApiError`, `load_token` (Task 4); `Priority` (Task 3).
- Produces: `SpeechRequest` (pydantic: `input`, `voice?`, `speed?` 0.25..4.0, `response_format="wav"`, `stream=False`, `model?`); `build_http_router(settings, runtime)`; `create_app(settings=None, backend_factory=None, lifespan_hooks=())`; `python -m src.services.tts`.
- `prepare_voice` викликається через worker **до** відповіді (400 `unsupported_voice_language` і для `stream=true`); стрімінг — job у inference-потоці кладе PCM-чанки в `asyncio.Queue` через `loop.call_soon_threadsafe`; обрив клієнта ставить `cancel`-event і скасовує future.

- [ ] **Step 1: Write the failing test**

`tests/services/test_svc_tts.py`:

```python
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from src.services.common.env import SettingsError
from src.services.tts.app import create_app
from src.services.tts.backend import FakeTtsBackend
from src.services.tts.encoding import wav_bytes, wav_header
from src.services.tts.settings import TtsSettings
from src.services.tts.voices import KOKORO_VOICES, catalog
from svc_testutil import running

ROOT = Path(__file__).resolve().parents[2]


def make(backend=None, **env):
    settings = TtsSettings.from_env({"RT_TTS_BACKEND": "fake", **env})
    backend = backend or FakeTtsBackend()
    return create_app(settings, backend_factory=lambda: backend), backend


def parse_wav(data):
    riff, riff_size, wave, fmt, _, pcm_fmt, channels, rate, byte_rate, align, bits, data_id, size = \
        struct.unpack("<4sI4s4sIHHIIHH4sI", data[:44])
    assert (riff, wave, fmt, data_id) == (b"RIFF", b"WAVE", b"fmt ", b"data")
    assert (pcm_fmt, channels, bits, align, byte_rate) == (1, 1, 16, 2, rate * 2)
    return rate, riff_size, size, data[44:]


def test_wav_header_complete_and_streaming():
    rate, riff_size, size, pcm = parse_wav(wav_bytes(b"\x01\x00" * 10, 24000))
    assert (rate, riff_size, size, len(pcm)) == (24000, 56, 20, 20)
    _, riff_size, size, _ = parse_wav(wav_header(16000, None))
    assert riff_size == size == 0xFFFFFFFF


def test_voice_catalog():
    assert len(KOKORO_VOICES) == 54 and len(set(KOKORO_VOICES)) == 54
    heart = next(v for v in catalog() if v.id == "af_heart")
    assert (heart.language, heart.gender) == ("en-us", "female")
    with pytest.raises(SettingsError):
        TtsSettings.from_env({"RT_TTS_VOICE": "nobody"})


def test_wav_response_with_defaults():
    app, backend = make()
    with running(app) as client:
        r = client.post("/v1/audio/speech", json={"input": "Hello there. Bye!"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav" and r.headers["x-sample-rate"] == "24000"
    rate, _, size, pcm = parse_wav(r.content)
    assert rate == 24000 and size == len(pcm) > 0
    assert backend.calls == [("Hello there. Bye!", "af_heart", 1.0)]


def test_pcm_response_resampled():
    app, _ = make(RT_TTS_SAMPLE_RATE="16000")
    with running(app) as client:
        r = client.post("/v1/audio/speech", json={"input": "abcd", "response_format": "pcm", "speed": 2.0})
    assert r.headers["content-type"] == "audio/pcm" and r.headers["x-sample-rate"] == "16000"
    # fake: 0.05 s * 4 chars / speed 2 = 0.1 s -> 1600 samples at 16 kHz
    assert len(r.content) == 1600 * 2


def test_streaming_wav():
    app, _ = make()
    with running(app) as client:
        with client.stream("POST", "/v1/audio/speech",
                           json={"input": "One. Two. Three.", "stream": True}) as r:
            body = b"".join(r.iter_bytes())
    _, riff_size, _, pcm = parse_wav(body)
    assert riff_size == 0xFFFFFFFF and len(pcm) > 0


def test_streaming_pcm_equals_non_streaming():
    app, _ = make()
    with running(app) as client:
        full = client.post("/v1/audio/speech", json={"input": "One. Two.", "response_format": "pcm"}).content
        streamed = client.post("/v1/audio/speech",
                               json={"input": "One. Two.", "response_format": "pcm", "stream": True}).content
    assert streamed == full


def test_voices_are_per_request():
    app, backend = make()
    with running(app) as client:
        a = client.post("/v1/audio/speech", json={"input": "same", "voice": "af_heart", "response_format": "pcm"})
        b = client.post("/v1/audio/speech", json={"input": "same", "voice": "am_adam", "response_format": "pcm"})
        c = client.post("/v1/audio/speech", json={"input": "same", "response_format": "pcm"})
    assert a.content != b.content and a.content == c.content
    assert [v for _, v, _ in backend.calls] == ["af_heart", "am_adam", "af_heart"]


@pytest.mark.parametrize("body,status,code", [
    ({"input": "   "}, 400, "invalid_request"),
    ({"input": "x" * 11}, 413, "payload_too_large"),
    ({"input": "x", "voice": "nobody"}, 400, "unknown_voice"),
    ({"input": "x", "response_format": "mp3"}, 400, "unsupported_format"),
    ({"input": "x", "voice": "jf_alpha"}, 400, "unsupported_voice_language"),
])
def test_validation_errors(body, status, code):
    app, _ = make(FakeTtsBackend(unsupported_prefixes=("j",)), RT_TTS_MAX_CHARS="10")
    with running(app) as client:
        r = client.post("/v1/audio/speech", json=body)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_speed_bounds():
    app, _ = make()
    with running(app) as client:
        assert client.post("/v1/audio/speech", json={"input": "x", "speed": 5}).status_code == 422


def test_voices_endpoint():
    app, _ = make(RT_TTS_VOICE="bf_emma")
    with running(app) as client:
        body = client.get("/v1/audio/voices").json()
    assert body["default"] == "bf_emma" and len(body["voices"]) == 54
    assert {"id": "am_adam", "language": "en-us", "gender": "male"} in body["voices"]


def test_tts_service_never_imports_sounddevice():
    code = ("import sys\n"
            "from fastapi.testclient import TestClient\n"
            "from src.services.tts.app import create_app\n"
            "from src.services.tts.settings import TtsSettings\n"
            "app = create_app(TtsSettings.from_env({'RT_TTS_BACKEND': 'fake'}))\n"
            "with TestClient(app) as c:\n"
            "    app.state.runtime.wait_ready(5)\n"
            "    assert c.post('/v1/audio/speech', json={'input': 'hi'}).status_code == 200\n"
            "print('sounddevice' in sys.modules)\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_empty_synthesis_returns_valid_empty_wav():
    class Silent(FakeTtsBackend):
        def synthesize(self, text, voice, speed):
            return iter(())

    app, _ = make(Silent())
    with running(app) as client:
        r = client.post("/v1/audio/speech", json={"input": "Привіт"})
    assert r.status_code == 200
    rate, riff_size, size, pcm = parse_wav(r.content)
    assert (riff_size, size, pcm) == (36, 0, b"")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix develop -c pytest tests/services/test_svc_tts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.tts.app'`

- [ ] **Step 3: Write minimal implementation**

`src/services/tts/http.py`:

```python
"""POST /v1/audio/speech, GET /v1/audio/voices. No playback on the server, ever."""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

import numpy as np
from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from ..common.audio import float_to_pcm16, resample
from ..common.errors import ApiError
from ..common.runtime import ServiceRuntime
from ..common.worker import Priority
from .backend import UnsupportedVoiceLanguage
from .encoding import wav_bytes, wav_header
from .settings import TtsSettings

FORMATS = {"wav": "audio/wav", "pcm": "audio/pcm"}
_DONE = object()


class SpeechRequest(BaseModel):
    input: str
    voice: Optional[str] = None
    speed: Optional[float] = Field(default=None, ge=0.25, le=4.0)
    response_format: str = "wav"
    stream: bool = False
    model: Optional[str] = None  # accepted for OpenAI compatibility, ignored


def build_http_router(settings: TtsSettings, runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/audio/speech")
    async def speech(req: SpeechRequest):
        backend = runtime.require_ready()
        text = req.input.strip()
        if not text:
            raise ApiError(400, "invalid_request", "input must not be empty")
        if len(req.input) > settings.max_chars:
            raise ApiError(413, "payload_too_large", f"input exceeds {settings.max_chars} characters")
        fmt = req.response_format.lower()
        if fmt not in FORMATS:
            raise ApiError(400, "unsupported_format", "response_format must be 'wav' or 'pcm'")
        voice = req.voice or settings.voice
        if voice not in {v.id for v in backend.voices()}:
            raise ApiError(400, "unknown_voice", f"unknown voice {voice!r}")
        speed = req.speed or settings.speed
        rate = settings.sample_rate
        headers = {"X-Sample-Rate": str(rate)}
        try:
            await runtime.run(backend.prepare_voice, voice, priority=Priority.REQUEST)
        except UnsupportedVoiceLanguage as exc:
            raise ApiError(400, "unsupported_voice_language", str(exc)) from exc

        def convert(chunk: np.ndarray) -> bytes:
            return float_to_pcm16(resample(chunk, backend.sample_rate, rate))

        if not req.stream:
            def job() -> bytes:
                return b"".join(convert(c) for c in backend.synthesize(text, voice, speed))

            pcm = await runtime.run(job, priority=Priority.REQUEST)
            body = wav_bytes(pcm, rate) if fmt == "wav" else pcm
            return Response(body, media_type=FORMATS[fmt], headers=headers)

        loop = asyncio.get_running_loop()
        chunks: "asyncio.Queue[Any]" = asyncio.Queue()
        cancel = threading.Event()

        def stream_job() -> None:
            try:
                for chunk in backend.synthesize(text, voice, speed):
                    if cancel.is_set():
                        break
                    loop.call_soon_threadsafe(chunks.put_nowait, convert(chunk))
            except Exception as exc:  # noqa: BLE001 — reported by ending the stream
                loop.call_soon_threadsafe(chunks.put_nowait, exc)
                return
            loop.call_soon_threadsafe(chunks.put_nowait, _DONE)

        fut = runtime.submit_threadsafe(stream_job, priority=Priority.REQUEST)

        async def body():
            try:
                if fmt == "wav":
                    yield wav_header(rate, None)
                while True:
                    item = await chunks.get()
                    if item is _DONE:
                        return
                    if isinstance(item, Exception):
                        logger.error("streaming synthesis failed: {}", item)
                        return
                    yield item
            finally:
                cancel.set()
                fut.cancel()

        return StreamingResponse(body(), media_type=FORMATS[fmt], headers=headers)

    @router.get("/audio/voices")
    async def voices() -> dict:
        backend = runtime.require_ready()
        return {"voices": [{"id": v.id, "language": v.language, "gender": v.gender} for v in backend.voices()],
                "default": settings.voice}

    return router
```

`src/services/tts/app.py`:

```python
"""rt-tts FastAPI app factory."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from fastapi import FastAPI

from ..common.app import LifespanHook, create_service_app
from ..common.auth import load_token
from ..common.runtime import ServiceRuntime
from .backend import TtsBackend, make_backend
from .http import build_http_router
from .settings import TtsSettings


def create_app(settings: Optional[TtsSettings] = None,
               backend_factory: Optional[Callable[[], TtsBackend]] = None,
               lifespan_hooks: Sequence[LifespanHook] = ()) -> FastAPI:
    settings = settings or TtsSettings.from_env()
    token = load_token(settings.api_token_file)
    runtime = ServiceRuntime(settings.service, backend_factory or (lambda: make_backend(settings)),
                             settings.max_pending)
    return create_service_app(settings=settings, runtime=runtime, token=token,
                              routers=[build_http_router(settings, runtime)], lifespan_hooks=lifespan_hooks)
```

`src/services/tts/__main__.py`:

```python
"""python -m src.services.tts"""
import sys

from ..common.serve import run_service
from .app import create_app
from .settings import TtsSettings


def main() -> int:
    return run_service(TtsSettings.from_env, lambda settings: create_app(settings))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix develop -c pytest tests/services/test_svc_tts.py -q`
Expected: PASS (усі тести файлів зелені)

- [ ] **Step 5: Commit**

```bash
git add src/services/tts/http.py src/services/tts/app.py src/services/tts/__main__.py tests/services/test_svc_tts.py
git commit -m "feat(tts): OpenAI-compatible speech and voices endpoints"
```

---

### Task 17: Гарантія lazy imports і slow-тести з реальними моделями

**Files:**
- Test: `tests/services/test_svc_lazy_imports.py`
- Test: `tests/services/test_svc_asr_real.py`
- Test: `tests/services/test_svc_mt_real.py`
- Test: `tests/services/test_svc_tts_real.py`

**Interfaces:**
- Consumes: `create_app` трьох сервісів (Tasks 11–16), `*Settings.from_env`, `FasterWhisperBackend`, `TransformersNllbBackend`, `KokoroBackend`, `DecodeOptions`, `resample`, `LANGUAGES`.
- Produces: регресійний захист крос-вимоги deploy-rs (`/health` ≤ 10 с) і opt-in перевірку реальних моделей.

- [ ] **Step 1: Write the lazy-import test**

`tests/services/test_svc_lazy_imports.py`:

```python
"""Heavy ML libraries are imported only by the background model loader (deploy-rs needs /health within ~10 s)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import sys
from fastapi.testclient import TestClient
from src.services.asr.app import create_app as asr_app
from src.services.asr.settings import AsrSettings
from src.services.mt.app import create_app as mt_app
from src.services.mt.settings import MtSettings
from src.services.tts.app import create_app as tts_app
from src.services.tts.settings import TtsSettings

# Real backends selected (defaults). TestClient without `with` does not run the lifespan,
# so the loader thread never starts: everything imported here is import-time cost.
for app in (asr_app(AsrSettings.from_env({})), mt_app(MtSettings.from_env({})), tts_app(TtsSettings.from_env({}))):
    assert TestClient(app).get("/health").json() == {"status": "ok"}
heavy = ("torch", "ctranslate2", "faster_whisper", "transformers", "kokoro", "sounddevice", "scipy",
         "onnxruntime", "huggingface_hub")   # last two: SP5 engines/model_store (Task 12a) must stay lazy too
print("HEAVY=" + ",".join(m for m in heavy if m in sys.modules))
"""


def test_creating_apps_does_not_import_heavy_libraries():
    out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "HEAVY=", out.stdout
```

- [ ] **Step 2: Run it**

Run: `nix develop -c pytest tests/services/test_svc_lazy_imports.py -q`
Expected: PASS. Якщо FAIL з `HEAVY=torch,...` — знайти модульний імпорт (`grep -rn "^import torch\|^from transformers\|^from kokoro\|^from faster_whisper\|^import scipy\|^from scipy\|^import soundfile" src/services`) і перенести його всередину функції. Тест має впасти, якщо тимчасово додати `import torch` на початок `src/services/mt/transformers_backend.py` — перевірити це вручну й відкотити.

- [ ] **Step 3: Write the slow tests**

`tests/services/test_svc_tts_real.py`:

```python
import numpy as np
import pytest

from src.services.tts.kokoro_backend import KokoroBackend
from src.services.tts.settings import TtsSettings


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_kokoro_synthesizes_speech_on_cpu():
    backend = KokoroBackend(TtsSettings.from_env({}))
    backend.load()
    audio = np.concatenate(list(backend.synthesize("Hello world, this is a test.", "af_heart", 1.0)))
    assert audio.dtype == np.float32
    assert len(audio) / backend.sample_rate >= 0.5
    assert float(np.max(np.abs(audio))) > 0.01
```

`tests/services/test_svc_asr_real.py`:

```python
import time

import numpy as np
import pytest
from loguru import logger

from src.services.asr.backend import DecodeOptions
from src.services.asr.faster_whisper_backend import FasterWhisperBackend
from src.services.asr.settings import AsrSettings
from src.services.common.audio import resample
from src.services.tts.kokoro_backend import KokoroBackend
from src.services.tts.settings import TtsSettings


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_faster_whisper_tiny_transcribes_kokoro_speech():
    tts = KokoroBackend(TtsSettings.from_env({}))
    tts.load()
    speech = np.concatenate(list(tts.synthesize("Hello world, this is a test.", "af_heart", 1.0)))
    audio = resample(speech, tts.sample_rate, 16000)

    asr = FasterWhisperBackend(AsrSettings.from_env({"RT_MODEL": "tiny"}))
    asr.load()
    t0 = time.monotonic()
    result = asr.transcribe(audio, DecodeOptions(language="en", prompt=None, beam_size=5))
    logger.info("tiny decode of {:.1f}s audio took {:.2f}s", len(audio) / 16000, time.monotonic() - t0)
    assert "hello" in result.text.lower()
    assert result.language == "en"
```

`tests/services/test_svc_mt_real.py`:

```python
import pytest

from src.services.mt.languages import LANGUAGES
from src.services.mt.settings import MtSettings
from src.services.mt.transformers_backend import TransformersNllbBackend


@pytest.fixture(scope="module")
def backend():
    b = TransformersNllbBackend(MtSettings.from_env({}))
    b.load()
    return b


@pytest.mark.slow
@pytest.mark.timeout(1200)
def test_nllb_translates_uk_to_en(backend):
    out = backend.translate(["Привіт, світе", "Дякую"], "ukr_Cyrl", "eng_Latn", 4)
    assert len(out) == 2
    assert all(t and t.isascii() for t in out), out


@pytest.mark.slow
@pytest.mark.timeout(1200)
def test_every_mapped_code_exists_in_tokenizer(backend):
    tok = backend._tokenizer
    missing = [code for code, _ in LANGUAGES.values() if tok.convert_tokens_to_ids(code) == tok.unk_token_id]
    assert missing == []
```

- [ ] **Step 4: Перевірити, що без прапорця вони пропускаються**

Run: `nix develop -c pytest tests/services -q`
Expected: усі швидкі тести PASS, 4 тести `skipped` (`real-model test; set RT_SLOW_TESTS=1`).

- [ ] **Step 5: Запустити slow-тести (потрібна мережа при першому запуску: Kokoro-82M, faster-whisper-tiny, NLLB-600M ≈ 2.5 GB)**

Run: `RT_SLOW_TESTS=1 nix develop -c pytest tests/services -m slow -q`
Expected: 4 passed. Якщо `test_every_mapped_code_exists_in_tokenizer` падає — виправити/прибрати відповідні записи в `src/services/mt/languages.py` за списком `missing` (spec Q9) і перезапустити.

- [ ] **Step 6: Commit**

```bash
git add tests/services/test_svc_lazy_imports.py tests/services/test_svc_asr_real.py tests/services/test_svc_mt_real.py tests/services/test_svc_tts_real.py
git commit -m "test(services): lazy-import guard and opt-in real-model tests"
```

---

### Task 18: Фінальна перевірка

**Files:** — (лише перевірки; виправлення — у відповідних файлах попередніх задач)

- [ ] **Step 1: Увесь новий тестовий набір**

Run: `nix develop -c pytest tests/services -q`
Expected: усі PASS (≈120), 4 skipped.

- [ ] **Step 2: Legacy-тести не зламані змінами Task 7**

Run: `nix develop -c pytest tests -q --ignore=tests/services -x -k "not gpu"`
Expected: той самий результат, що й на `main` до початку роботи (порівняти з `git stash`-прогоном, якщо є падіння — переконатися, що вони існували раніше).

- [ ] **Step 3: Lint / format нових файлів**

Run: `nix develop -c sh -c "black --check src/services tests/services || true; flake8 --max-line-length 120 src/services tests/services || true"`
Expected: якщо `black`/`flake8` доступні в devshell — без зауважень (виправити, якщо є); якщо команд немає — пропустити (не встановлювати через pip).

- [ ] **Step 4: Smoke-запуск трьох сервісів з fake backend-ами**

```bash
nix develop -c sh -c '
RT_PORT=18091 RT_ASR_BACKEND=fake python -m src.services.asr & A=$!
RT_PORT=18092 RT_MT_BACKEND=fake python -m src.services.mt & M=$!
RT_PORT=18093 RT_TTS_BACKEND=fake python -m src.services.tts & T=$!
sleep 4
curl -s localhost:18091/status; echo
curl -s -X POST localhost:18092/v1/translate -H "content-type: application/json" -d "{\"text\":\"Привіт\"}"; echo
curl -s -X POST localhost:18093/v1/audio/speech -H "content-type: application/json" -d "{\"input\":\"Hello.\"}" -o /tmp/rt-tts-smoke.wav; file /tmp/rt-tts-smoke.wav
kill $A $M $T'
```

Expected: `/status` з `"ready":true`; переклад `{"translations":["[eng_Latn] Привіт"],...}`; `RIFF (little-endian) data, WAVE audio, Microsoft PCM, 16 bit, mono 24000 Hz`.

- [ ] **Step 5: (опційно, якщо є GPU) Реальний ASR на CUDA**

Run: `RT_PORT=18091 RT_DEVICE=cuda RT_MODEL=small nix develop -c python -m src.services.asr` і в іншому терміналі `curl -s localhost:18091/status` до `"ready":true`, далі `curl -s -F file=@<будь-який wav> localhost:18091/v1/audio/transcriptions`.
Expected: `/health` відповідає одразу після старту; `vram_mb` — число після завантаження.

- [ ] **Step 6: Commit (якщо були виправлення)**

```bash
git add -A src/services tests/services
git commit -m "chore(services): final verification fixes"
```

---

## Self-review (виконано автором плану)

- Покриття spec: §4.1 структура → Tasks 1–16; §4.3–4.4 runtime/конкурентність → Tasks 3, 5; §4.5 env → Tasks 2, 10, 13, 15; §4.6 `/health`/`/status`/помилки → Tasks 4, 5; §4.7 backend-и → Tasks 10, 13, 15; §5 auth → Tasks 4, 11, 12; §6 ASR → Tasks 7–12; §7 MT → Tasks 13–14; §8 TTS → Tasks 15–16; §9 Django hook (`lifespan_hooks`, `submit_threadsafe`) → Task 5; §10.1 devshell → Task 1; §10.3 legacy → Task 7; §11 тести + lazy imports → Tasks 2–17.
- Код задач 2–17 попередньо прогнано в ізольованому scratch-дереві: 120 швидких тестів PASS (3 прогони поспіль, без флейків); slow ASR (`tiny`) і TTS (Kokoro) PASS у `nix develop`; slow MT не запускався (немає NLLB у кеші).
- Типи/імена узгоджені між задачами (див. блоки **Interfaces**).
