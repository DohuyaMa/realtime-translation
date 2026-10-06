# Django/engine integration (rt-asr / rt-mt / rt-tts) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Зробити `rt-asr` / `rt-mt` / `rt-tts` споживачами WorkItem-черги Django `engine` (batch), так щоб engine-workflow міг ланцюжити ASR→MT→TTS, не зачепивши існуючих llama-воркерів.

**Architecture:** Django отримує `WorkerType.payload_contract` (`structured_json` → claim лише через `/next/?artifact_types=`, без LLM-retry-переписування prompt), узагальнений deferred-parent "чекати на зовнішній WorkItem" (успіх і помилка), per-row claim-таймаут, `LLMService.kind` + `port` при реєстрації, і новий app `speech` (AudioBlob за посиланням, step-handler'и `speech_*`, blueprints). Сервіси отримують пакет `src/services/common/django_worker/` (register → poll → execute через inference-чергу runtime → complete), підключений через `lifespan_hooks` підпроєкту 1.

**Tech Stack:** Django 5 + DRF + PostgreSQL + pytest-django (swarm-nix, `nix develop ./flake`); Python 3.13 + FastAPI + httpx + pydantic v2 + pytest (цей репо, `nix develop`).

**Spec:** [`docs/superpowers/specs/2026-10-06-django-engine-integration-design.md`](../specs/2026-10-06-django-engine-integration-design.md) (читати разом з планом; overview — `../specs/2026-10-06-distributed-speech-services-overview.md`; SP1 — `../specs/2026-10-06-speech-services-api-design.md`).

## Global Constraints

- Два репо: **Part A** — `~/repos/swarm-nix/django_api` (Django); **Part B** — `~/repos/real-time-transletor` (сервіси); **Part C** — живий e2e. Part A не залежить від Part B; Part B потребує реалізованого підпроєкту 1 (`src/services/common/{runtime,app,health,audio}.py`, `src/services/{asr,mt,tts}/{backend,app,__main__}.py`).
- swarm-nix: усі `python`/`pytest`/`manage.py` — лише в `nix develop ./flake` (з кореня swarm-nix): `nix develop ./flake --command bash -c "cd django_api && python -m pytest <path> -v"`. Ніколи `pip install`.
- swarm-nix: **не** запускати `manage.py migrate` проти живої БД у межах задач — лише `makemigrations --check`/тести (pytest мігрує ізольовану `knowledge_base_test`). Застосування міграцій + `sudo systemctl restart obsidian-django.service` — окремий крок C-1 з дозволу власника.
- swarm-nix тести: спільна test-БД — collision-proof значення (`zzq…`-префікси/uuid), фільтри `next()` тестувати на `pk__in`-скоупі; не стверджувати точні кількості по всій таблиці.
- Цей репо: залежності лише через flake (`nix/devshell.nix` після SP2 Task 1 — overview §6; runtime-залежності bundle — `nix/packages/deps.nix`, `httpx` там уже є), ніколи pip/venv.
- Коміти: англійською, **без** рядків `Co-Authored-By` (правило користувача). Кожен репо комітиться окремо.
- Wire-контракт (spec §3.2) — незмінний між частинами: payload/результат мають `"v": 1`; artifact_types: `asr_transcribe`, `mt_translate`, `tts_synthesize`; worker_types: `audio` (ASR, TTS), `mt` (MT); capabilities: `speech_transcribe`, `speech_translate`, `speech_synthesize`, `speech_gc_blobs`.
- Blob-шляхи у payload — **відносні**, лише з префіксом `/api/speech/`; download: `/api/speech/blobs/<blob_uuid>/content/?work_item=<wi_uuid>`; upload: `/api/speech/blobs/?work_item=<wi_uuid>`.
- Ліміти (spec §3.6/§3.7): input blob ≤ 52428800 B; output ≤ 209715200 B; MT ≤ 20000 символів/WorkItem і ≤ 1000/елемент; TTS ≤ 4000 символів; blob TTL 7 днів; ASR timeout `clamp(ceil(d*1.5)+120, 300, 3600)` або 1800; MT 300; TTS `clamp(len//10+120, 300, 1800)`; `max_retries=2`.
- Service env (spec §5.2): `RT_WORKER_ENABLED` (false), `RT_DJANGO_ENDPOINTS`, `RT_DJANGO_BOOTSTRAP_TOKEN_FILE`, `RT_SERVICE_NAME` (`<hostname>-rt-<svc>`), `RT_WORKER_POLL_INTERVAL` (10), `RT_WORKER_MAX_DOWNLOAD_BYTES` (104857600), `RT_PORT`. Bootstrap-токен — лише з файлу.
- Python-стиль swarm-nix: `logger = logging.getLogger(__name__)`, без `print`/голого `except:`; DRF-помилки `{"error","code","details"}`; моделі з docstring/`__str__`/`Meta`; `ruff check <app>/`.

## Review Focus

1. **Існуючий llama-воркер поллить `/next/` без `artifact_types` під час активної speech-черги** — має отримувати лише свої задачі, ніколи `audio`/`mt`. Тест: Task A2 `test_legacy_poll_never_sees_structured` (без tier і з tier).
2. **Невідповідний результат воркера (валідатор відхилив) для structured-задачі** — повторна видача з **тим самим** JSON payload, без LLM-реконсолідації. Тест: Task A4.
3. **Зовнішня дитина впала термінально** — deferred parent і workflow не висять, а падають з видимою помилкою. Тест: Task A3 `test_error_child_fails_parent` + HTTP-рівень.
4. **Воркеру підсунули абсолютний URL / шлях поза `/api/speech/` у payload** — відмова без відправки токена. Тест: Task B3 `test_blobref_rejects_foreign_paths` (test_jobs.py) і `test_fetch_blob_never_calls_foreign_host` (test_client.py).
5. **Django недоступний / токен-файл відсутній при `RT_WORKER_ENABLED=true`** — HTTP API сервісу працює, `/status.worker.error` пояснює причину, воркер не падає циклом. Тест: Task B5 `test_enabled_without_token_file_reports_error_and_starts_no_thread`, Task B4 `test_transport_error_backs_off_endpoint_and_tries_next`.

---

## File Structure

### Part A — `~/repos/swarm-nix/django_api`

| Файл | Дія | Відповідальність |
|---|---|---|
| `engine/models/workitem.py` | Modify | `WorkerType.PayloadContract` + поле `payload_contract` |
| `engine/migrations/0078_workertype_payload_contract.py` | Create | AddField + сідінг `audio`/`mt` |
| `engine/constants.py` | Modify | `WORKER_TYPE_DEFINITIONS['audio'/'mt']`, `WorkerTypeName.AUDIO/MT` |
| `engine/management/commands/bootstrap_blueprints.py` | Modify | переносить `payload_contract` |
| `engine/management/commands/dump_workertypes.py` | Modify | дампить `payload_contract` |
| `engine/blueprints/worker_types.yaml` | Modify | `audio`, `mt` |
| `engine/services/providers.py` | Modify | `get_structured_worker_types()` |
| `engine/views_work_item.py` | Modify | `parse_artifact_types`, `apply_claim_scope`, гілки `next()`, structured-retry, резолв parent на термінальній помилці |
| `engine/services/workitem_factory.py` | Modify | `resolve_deferred_parent_waiting_on` через `finish_deferred_parent`, success+error |
| `engine/services/queue_health.py` | Modify | `overdue_claims(queryset, now)` |
| `engine/management/commands/engine_poll.py`, `reset_stale_workitems.py` | Modify | per-row timeout |
| `llm_services/constants.py`, `models.py`, `migrations/0007_llmservice_kind.py`, `api_token_views.py`, `admin.py` | Modify/Create | `kind`, `port` |
| `llm_services/services/health_service.py`, `selectors.py` | Modify | dispatch за kind, фікс `model.path`, `push_runtime_config` лише llm |
| `speech/` (новий app) | Create | див. spec §3.6 |
| `knowledge_base_api/settings.py`, `urls.py` | Modify | `INSTALLED_APPS += speech`, `SPEECH_*`, `/api/speech/` |
| `engine/blueprints/workflows/speech.yaml`, `meta_schedules.yaml` | Create/Modify | 3 workflow + schedule |
| `context/docs/adr/ADR-057-…`, `context/docs/reference/{tier,speech}.md`, `context/{tasks,ui-tasks,technical-debt}.md`, `AGENTS.md`, `speech/context/README.md`, `llm_services/context/README.md` | Create/Modify | документація |

### Part B — `~/repos/real-time-transletor`

| Файл | Дія | Відповідальність |
|---|---|---|
| `src/services/common/django_worker/__init__.py` | Create | публічне API |
| `…/errors.py` | Create | `WorkerError`, `TransportError`, `AuthError`, `JobError` |
| `…/config.py` | Create | `WorkerConfig`, `WorkerConfigError` |
| `…/auth.py` | Create | `TokenManager` |
| `…/client.py` | Create | `DjangoEndpointClient` |
| `…/jobs.py` | Create | `ensure_speech_path`, `BlobRef`, `UploadSpec`, `JobContext`, `run_job` |
| `…/loop.py` | Create | `WorkerLoop`, `WorkerStatus` |
| `…/hooks.py` | Create | `build_django_worker_hooks`, `runtime_can_accept` |
| `src/services/common/runtime.py`, `health.py` | Modify | `status_extras` |
| `src/services/{asr,mt,tts}/django_job.py` | Create | адаптери payload→backend→результат |
| `src/services/{asr,mt,tts}/__main__.py` | Modify | передача hooks |
| `tests/services/django_worker/conftest.py` | Create | `FakeDjango`, `FakeRuntime`, фікстури |
| `tests/services/django_worker/test_*.py` | Create | тести |

---

# Part A — swarm-nix/django_api

Усі шляхи в Part A — відносно `~/repos/swarm-nix/django_api`; команди запускаються з `~/repos/swarm-nix`.
Скорочення: `DJTEST <args>` ≡ `nix develop ./flake --command bash -c "cd django_api && python -m pytest <args>"`;
`DJMANAGE <args>` ≡ `nix develop ./flake --command bash -c "cd django_api && python manage.py <args>"`.

Перед стартом Part A прочитати (обов'язкові гайди за `.claude/rules/django/*`): `context/docs/workflow-engine/deferred-parent-workitem-guide.md`, `context/docs/workflow-engine/plugin-writing-guide.md`, `context/docs/development/app-structure-guide.md`, `context/docs/development/coding-guidelines.md`.

### Task A1: `WorkerType.payload_contract` + worker types `audio`/`mt`

**Files:**
- Modify: `engine/models/workitem.py` (клас `WorkerType`, рядки 18-47)
- Create: `engine/migrations/0078_workertype_payload_contract.py`
- Modify: `engine/constants.py` (`WORKER_TYPE_DEFINITIONS` ~рядок 86; `WorkerTypeName` ~рядок 171)
- Modify: `engine/management/commands/bootstrap_blueprints.py` (`_seed_worker_types`, ~рядок 108)
- Modify: `engine/management/commands/dump_workertypes.py` (~рядки 30-60)
- Modify: `engine/blueprints/worker_types.yaml`
- Modify: `engine/services/providers.py`
- Test: `engine/tests/test_workertype_payload_contract.py`

**Interfaces:**
- Produces: `WorkerType.PayloadContract.{LLM_PROMPT,STRUCTURED_JSON}` (`'llm_prompt'`, `'structured_json'`); `engine.services.providers.get_structured_worker_types() -> list[str]`; `WorkerTypeName.AUDIO == 'audio'`, `WorkerTypeName.MT == 'mt'`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_workertype_payload_contract.py
"""WorkerType.payload_contract (spec 2026-10-06 django-engine-integration §3.1)."""
import pytest

from engine.constants import WORKER_TYPE_DEFINITIONS, WorkerTypeName
from engine.models import WorkerType
from engine.services.providers import get_structured_worker_types


@pytest.mark.django_db
def test_speech_worker_types_are_seeded_structured_and_active():
    audio = WorkerType.objects.get(name='audio')
    mt = WorkerType.objects.get(name='mt')
    assert (audio.payload_contract, audio.is_active, audio.tier) == ('structured_json', True, None)
    assert (mt.payload_contract, mt.is_active, mt.tier) == ('structured_json', True, None)


@pytest.mark.django_db
def test_llm_worker_type_defaults_to_llm_prompt():
    assert WorkerType.objects.get(name='llm').payload_contract == 'llm_prompt'


@pytest.mark.django_db
def test_get_structured_worker_types_lists_only_active_structured():
    WorkerType.objects.create(name='zzq_struct_on', display_name='zzq', payload_contract='structured_json', is_active=True)
    WorkerType.objects.create(name='zzq_struct_off', display_name='zzq', payload_contract='structured_json', is_active=False)
    names = set(get_structured_worker_types())
    assert 'zzq_struct_on' in names
    assert 'zzq_struct_off' not in names
    assert 'llm' not in names
    assert {'audio', 'mt'} <= names


def test_definitions_carry_payload_contract():
    assert WORKER_TYPE_DEFINITIONS['audio']['payload_contract'] == 'structured_json'
    assert WORKER_TYPE_DEFINITIONS['mt']['payload_contract'] == 'structured_json'
    assert WORKER_TYPE_DEFINITIONS['llm'].get('payload_contract', 'llm_prompt') == 'llm_prompt'
    assert (WorkerTypeName.AUDIO, WorkerTypeName.MT) == ('audio', 'mt')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJTEST engine/tests/test_workertype_payload_contract.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_structured_worker_types'`.

- [ ] **Step 3: Add the model field**

In `engine/models/workitem.py`, inside `class WorkerType`, after `class Provider(...)`:

```python
    class PayloadContract(models.TextChoices):
        """How the worker reads WorkItem.prompt (spec 2026-10-06 §3.1).

        llm_prompt      — LLM text instructions; tier-routed; validation retry
                          rewrites the prompt (build_retry_prompt) and may decompose.
        structured_json — JSON payload for a dedicated worker; claimed ONLY by
                          pollers listing the artifact_type in /next/?artifact_types=;
                          validation retry keeps the payload verbatim, never decomposes.
        """
        LLM_PROMPT = 'llm_prompt', 'LLM prompt (generic text generation, tier-routed)'
        STRUCTURED_JSON = 'structured_json', 'Structured JSON payload (dedicated worker, artifact_types opt-in)'
```

and after the `tier` field:

```python
    payload_contract = models.CharField(
        max_length=20,
        choices=PayloadContract.choices,
        default=PayloadContract.LLM_PROMPT,
        help_text="llm_prompt = tier-routed LLM work; structured_json = dedicated worker, "
                  "claimed only via /next/?artifact_types= (spec 2026-10-06 §3.1).",
    )
```

- [ ] **Step 4: Write the migration**

```python
# engine/migrations/0078_workertype_payload_contract.py
from django.db import migrations, models

SPEECH_WORKER_TYPES = {
    'audio': {
        'display_name': 'Audio speech services (ASR/TTS)',
        'provider': 'workitem',
        'tier': None,
        'description': 'rt-asr / rt-tts dedicated workers — artifact_types asr_transcribe, '
                       'tts_synthesize; JSON payload; claimed only via /next/?artifact_types=',
        'is_active': True,
        'payload_contract': 'structured_json',
    },
    'mt': {
        'display_name': 'Machine translation (NLLB, rt-mt)',
        'provider': 'workitem',
        'tier': None,
        'description': 'rt-mt dedicated worker — artifact_type mt_translate; JSON payload; '
                       'claimed only via /next/?artifact_types=',
        'is_active': True,
        'payload_contract': 'structured_json',
    },
}


def seed_speech_worker_types(apps, schema_editor):
    WorkerType = apps.get_model('engine', 'WorkerType')
    for name, defaults in SPEECH_WORKER_TYPES.items():
        WorkerType.objects.update_or_create(name=name, defaults=defaults)


def unseed_speech_worker_types(apps, schema_editor):
    WorkerType = apps.get_model('engine', 'WorkerType')
    WorkerType.objects.filter(name='mt').delete()
    WorkerType.objects.filter(name='audio').update(
        display_name='Audio Generation (reserved)',
        description='Reserved for future audio-modality WorkItems — not yet implemented',
        is_active=False,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('engine', '0077_workitem_metadata_session_blueprint_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='workertype',
            name='payload_contract',
            field=models.CharField(
                choices=[
                    ('llm_prompt', 'LLM prompt (generic text generation, tier-routed)'),
                    ('structured_json', 'Structured JSON payload (dedicated worker, artifact_types opt-in)'),
                ],
                default='llm_prompt',
                help_text='llm_prompt = tier-routed LLM work; structured_json = dedicated worker, '
                          'claimed only via /next/?artifact_types= (spec 2026-10-06 §3.1).',
                max_length=20,
            ),
        ),
        migrations.RunPython(seed_speech_worker_types, unseed_speech_worker_types),
    ]
```

Перевір, що `0077_…` — справді останній: `ls django_api/engine/migrations | sort | tail -3`; якщо з'явився новіший — постав його в `dependencies` і перейменуй файл на наступний номер.

- [ ] **Step 5: Constants, seeding, dump, YAML, provider**

`engine/constants.py` — замінити запис `'audio'` і додати `'mt'` у `WORKER_TYPE_DEFINITIONS` (значення — ідентичні `SPEECH_WORKER_TYPES` міграції, включно з `'payload_contract': 'structured_json'`). Інші записи не чіпати (відсутній ключ = `llm_prompt`). У `class WorkerTypeName` додати:

```python
    # Dedicated speech workers (spec 2026-10-06 django-engine-integration §3.1)
    AUDIO: str = 'audio'
    MT: str = 'mt'
```

`bootstrap_blueprints.py::_seed_worker_types` — у `defaults` додати:

```python
                'payload_contract': defn.get('payload_contract', 'llm_prompt'),
```

`dump_workertypes.py` — у збиранні `definitions[wt.name]` додати `'payload_contract': wt.payload_contract,`; у `_format_python_dict` після рядка `'is_active'` додати:

```python
        if defn.get('payload_contract', 'llm_prompt') != 'llm_prompt':
            lines.append(f"        'payload_contract': {defn['payload_contract']!r},")
```

`engine/blueprints/worker_types.yaml` — замінити блок `audio` і додати `mt` (алфавітно після `llm`):

```yaml
- name: audio
  display_name: Audio speech services (ASR/TTS)
  provider: workitem
  tier: null
  payload_contract: structured_json
  description: rt-asr / rt-tts dedicated workers — artifact_types asr_transcribe, tts_synthesize; JSON payload; claimed only via /next/?artifact_types=
  is_active: true
```

```yaml
- name: mt
  display_name: Machine translation (NLLB, rt-mt)
  provider: workitem
  tier: null
  payload_contract: structured_json
  description: rt-mt dedicated worker — artifact_type mt_translate; JSON payload; claimed only via /next/?artifact_types=
  is_active: true
```

`engine/services/providers.py` — додати після `get_lm_studio_worker_types`:

```python
def get_structured_worker_types() -> Collection[str]:
    """Active worker_type names with payload_contract='structured_json' —
    dedicated (non-LLM) workers that must never be handed to a generic
    tier poller (spec 2026-10-06 django-engine-integration §3.1)."""
    from engine.models import WorkerType

    return list(
        WorkerType.objects
        .filter(payload_contract=WorkerType.PayloadContract.STRUCTURED_JSON, is_active=True)
        .values_list('name', flat=True)
    )
```

- [ ] **Step 6: Run tests + migration check**

Run: `DJTEST engine/tests/test_workertype_payload_contract.py -v` → PASS (4 tests).
Run: `DJMANAGE makemigrations engine --check --dry-run` → `No changes detected`.
Run: `DJTEST engine/tests/test_migration_0075_consolidate_worker_types.py engine/tests/test_management_commands.py -v` → PASS (регресія сідінгу).

- [ ] **Step 7: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/engine/models/workitem.py django_api/engine/migrations/0078_workertype_payload_contract.py django_api/engine/constants.py django_api/engine/management/commands/bootstrap_blueprints.py django_api/engine/management/commands/dump_workertypes.py django_api/engine/blueprints/worker_types.yaml django_api/engine/services/providers.py django_api/engine/tests/test_workertype_payload_contract.py
git -C ~/repos/swarm-nix commit -m "engine: WorkerType.payload_contract; activate audio, add mt structured worker types"
```

### Task A2: `next()` — claim-scope за `?artifact_types=`

**Files:**
- Modify: `engine/views_work_item.py` (імпорти ~рядок 15-39; нові функції після `order_by_tier_preference`; `next()` рядки 172-231)
- Test: `engine/tests/test_next_artifact_types.py`

**Interfaces:**
- Consumes: `get_structured_worker_types()` (A1).
- Produces: `parse_artifact_types(raw: str | None) -> list[str] | None` (ValueError на некоректне); `apply_claim_scope(queryset, artifact_types: list[str] | None, structured_worker_types) -> QuerySet`; HTTP: `GET /api/engine/work-items/next/?artifact_types=a,b` (400 `invalid_artifact_types`).

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_next_artifact_types.py
"""next() claim scope by ?artifact_types= (spec 2026-10-06 django-engine-integration §3.1).

Filter functions are tested on a pk__in-scoped queryset (shared test DB, TD-18 —
same convention as test_next_tier_matching.py); the HTTP tests use a
collision-proof artifact_type / out-of-domain tier so only our rows can match.
"""
import uuid

import pytest
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from engine.models import WorkerType, WorkItem
from engine.views_work_item import apply_claim_scope, parse_artifact_types
from llm_services.models import LLMService


class TestParseArtifactTypes:
    def test_absent_is_none(self):
        assert parse_artifact_types(None) is None

    def test_csv_is_split_trimmed_deduplicated(self):
        assert parse_artifact_types(' asr_transcribe, mt_translate,asr_transcribe ') == ['asr_transcribe', 'mt_translate']

    @pytest.mark.parametrize('raw', ['', ' , ', 'Bad-Type', 'a' * 51, ','.join(f't{i}' for i in range(17))])
    def test_invalid_raises(self, raw):
        with pytest.raises(ValueError):
            parse_artifact_types(raw)


@pytest.mark.django_db
class TestApplyClaimScope:
    @pytest.fixture(autouse=True)
    def _rows(self, db):
        WorkerType.objects.get_or_create(name='zzq_struct', defaults={'display_name': 'zzq', 'payload_contract': 'structured_json'})
        self.llm = WorkItem.objects.create(worker_type='llm', artifact_type='zzq_llm_art', prompt='p', status='todo')
        self.speech = WorkItem.objects.create(worker_type='zzq_struct', artifact_type='zzq_speech_art', prompt='{}', status='todo')
        self.other_speech = WorkItem.objects.create(worker_type='zzq_struct', artifact_type='zzq_other_art', prompt='{}', status='todo')
        self.base = WorkItem.objects.filter(pk__in=[self.llm.pk, self.speech.pk, self.other_speech.pk])

    def test_opt_in_poller_gets_only_listed_types(self):
        got = set(apply_claim_scope(self.base, ['zzq_speech_art'], ['zzq_struct']).values_list('pk', flat=True))
        assert got == {self.speech.pk}

    def test_legacy_poll_never_sees_structured(self):
        got = set(apply_claim_scope(self.base, None, ['zzq_struct']).values_list('pk', flat=True))
        assert got == {self.llm.pk}


@pytest.mark.django_db
class TestNextHttp:
    def _client(self):
        name = f'zzq-worker-{uuid.uuid4().hex[:8]}'
        user = User.objects.create_user(name)
        token = Token.objects.create(user=user)
        LLMService.objects.create(name=name, base_url='http://127.0.0.1:8091', token=token.key)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.key}')
        return client

    def test_invalid_param_is_400(self):
        resp = self._client().get('/api/engine/work-items/next/?artifact_types=Bad-Type')
        assert resp.status_code == 400
        assert resp.data['code'] == 'invalid_artifact_types'

    def test_opt_in_poll_claims_own_structured_item(self):
        WorkerType.objects.get_or_create(name='zzq_struct', defaults={'display_name': 'zzq', 'payload_contract': 'structured_json'})
        art = f'zzq_art_{uuid.uuid4().hex[:8]}'
        wi = WorkItem.objects.create(worker_type='zzq_struct', artifact_type=art, prompt='{"v": 1}', status='todo', expected_format='json')
        resp = self._client().get(f'/api/engine/work-items/next/?artifact_types={art}')
        assert resp.status_code == 200
        assert resp.data['uuid'] == str(wi.uuid)
        assert resp.data['prompt'] == '{"v": 1}'  # not re-rendered
        wi.refresh_from_db()
        assert wi.status == 'in_progress'

    def test_tier_poll_does_not_claim_structured_item(self):
        WorkerType.objects.get_or_create(name='zzq_struct', defaults={'display_name': 'zzq', 'payload_contract': 'structured_json'})
        wi = WorkItem.objects.create(worker_type='zzq_struct', artifact_type='zzq_tier_art', prompt='{}', status='todo', tier=97)
        resp = self._client().get('/api/engine/work-items/next/?tier=97')
        assert resp.status_code == 204
        wi.refresh_from_db()
        assert wi.status == 'todo'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJTEST engine/tests/test_next_artifact_types.py -v`
Expected: FAIL — `ImportError: cannot import name 'apply_claim_scope'`.

- [ ] **Step 3: Implement the functions**

In `engine/views_work_item.py` add `import re` to imports, change the providers import to
`from engine.services.providers import get_lm_studio_worker_types, get_structured_worker_types`, and add after `order_by_tier_preference()`:

```python
ARTIFACT_TYPES_MAX = 16
_ARTIFACT_TYPE_RE = re.compile(r'^[a-z0-9_]{1,50}$')


def parse_artifact_types(raw: str | None) -> list[str] | None:
    """next()'s `?artifact_types=a,b` — the dedicated-worker opt-in (spec
    2026-10-06 django-engine-integration §3.1). None when the param is absent
    (every legacy llama poller); ValueError on an empty/malformed list so a
    misconfigured worker gets an explicit 400 instead of silently polling
    nothing."""
    if raw is None:
        return None
    items: list[str] = []
    for part in raw.split(','):
        item = part.strip()
        if not item:
            continue
        if not _ARTIFACT_TYPE_RE.match(item):
            raise ValueError(f'invalid artifact_type {item!r} (expected [a-z0-9_]{{1,50}})')
        if item not in items:
            items.append(item)
    if not items:
        raise ValueError('artifact_types is empty')
    if len(items) > ARTIFACT_TYPES_MAX:
        raise ValueError(f'too many artifact_types ({len(items)} > {ARTIFACT_TYPES_MAX})')
    return items


def apply_claim_scope(queryset, artifact_types, structured_worker_types):
    """Opt-in pollers get exactly the artifact_types they listed; pollers
    that list nothing (generic tier-routed LLM workers) never see a
    structured_json worker_type — its prompt is a JSON payload, not LLM
    instructions (spec §3.1)."""
    if artifact_types is not None:
        return queryset.filter(artifact_type__in=artifact_types)
    return queryset.exclude(worker_type__in=list(structured_worker_types))
```

- [ ] **Step 4: Wire into `next()`**

Right after the tier parsing block (after the `tier_filter = int(tier_param)` try/except, ~line 182) add:

```python
        try:
            artifact_types = parse_artifact_types(request.query_params.get('artifact_types'))
        except ValueError as exc:
            return response.Response(
                {"error": str(exc), "code": "invalid_artifact_types", "details": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )
```

Replace the block `if tier_filter is not None: … else: candidates_qs = task.exclude(...)` (lines 226-231) with:

```python
        # spec 2026-10-06 §3.1: dedicated (structured_json) workers opt in by
        # artifact_type; generic pollers never see their WorkItems.
        task = apply_claim_scope(task, artifact_types, get_structured_worker_types())
        if tier_filter is not None:
            # TD-157 (2026-10-01, owner decision) — see order_by_tier_
            # preference()'s own docstring for the full starvation story.
            candidates_qs = order_by_tier_preference(apply_tier_filter(task, tier_filter), tier_filter)
        elif artifact_types is not None:
            candidates_qs = task.order_by('id')
        else:
            candidates_qs = task.exclude(worker_type__in=get_lm_studio_worker_types()).order_by('id')
```

Append to the `next()` docstring a paragraph: "`?artifact_types=a,b` (spec 2026-10-06 §3.1) — dedicated workers (WorkerType.payload_contract='structured_json') opt in by artifact_type; combined with `tier` by AND. Without it, structured WorkItems are never returned."

- [ ] **Step 5: Run tests**

Run: `DJTEST engine/tests/test_next_artifact_types.py engine/tests/test_next_tier_matching.py engine/tests/test_next_tier_preference_ordering.py engine/tests/test_next_paused_signal.py engine/tests/test_next_excludes_internal_steps.py engine/tests/test_next_tried_by_services.py -v`
Expected: PASS (нові + регресія `next()`).

- [ ] **Step 6: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/engine/views_work_item.py django_api/engine/tests/test_next_artifact_types.py
git -C ~/repos/swarm-nix commit -m "engine: next() artifact_types opt-in; hide structured WorkItems from generic pollers"
```

### Task A3: deferred parent "waiting_on" — success розгортає результат, помилка фейлить parent

**Files:**
- Modify: `engine/services/workitem_factory.py:187-212` (`resolve_deferred_parent_waiting_on`)
- Modify: `engine/views_work_item.py` (`_handle_worker_error` термінальна гілка ~рядок 499; `_handle_validation_error` термінальна гілка ~рядок 566)
- Test: `engine/tests/test_deferred_waiting_on.py`

**Interfaces:**
- Consumes: `engine.services.deferred_resolution.finish_deferred_parent(wi, *, success, parsed_result, error='')`.
- Produces: `resolve_deferred_parent_waiting_on(work_item)` — для `done` дитини parent.parsed_result = `{**child.parsed_result, 'result_uuid', 'raw_result'}` → `on_complete`; для `error` дитини parent `error` з `validation_error = child.validation_error` → `on_error`; інші статуси — no-op.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_deferred_waiting_on.py
"""resolve_deferred_parent_waiting_on — success merges child result, error fails parent
(spec 2026-10-06 django-engine-integration §3.4)."""
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from engine.models import WorkItem
from engine.services.workitem_factory import create_deferred_parent_workitem, resolve_deferred_parent_waiting_on

ON_COMPLETE = 'engine.services.workflow_executor.WorkflowExecutor.on_complete'
ON_ERROR = 'engine.services.workflow_executor.WorkflowExecutor.on_error'


def _child(status, **kw):
    return WorkItem.objects.create(worker_type='llm', artifact_type='zzq_child', prompt='x', status=status, **kw)


def _parent_waiting_on(child):
    return create_deferred_parent_workitem(
        worker_type='internal', artifact_type='zzq_parent', prompt='(zzq parent)',
        metadata={'is_zzq_step': True, 'waiting_on_workitem_uuid': str(child.uuid)},
    )


@pytest.mark.django_db
class TestResolveWaitingOn:
    def test_done_child_merges_parsed_result_and_completes_parent(self):
        child = _child('done', parsed_result={'text': 'hi', 'language': 'uk'}, raw_result='{"text":"hi"}')
        parent = _parent_waiting_on(child)
        with patch(ON_COMPLETE) as on_complete:
            resolve_deferred_parent_waiting_on(child)
        parent.refresh_from_db()
        assert parent.status == 'done'
        assert parent.parsed_result == {
            'text': 'hi', 'language': 'uk',
            'result_uuid': str(child.uuid), 'raw_result': '{"text":"hi"}',
        }
        on_complete.assert_called_once()

    def test_error_child_fails_parent(self):
        child = _child('error', validation_error='ValueError: boom', raw_result='')
        parent = _parent_waiting_on(child)
        with patch(ON_ERROR) as on_error, patch(ON_COMPLETE) as on_complete:
            resolve_deferred_parent_waiting_on(child)
        parent.refresh_from_db()
        assert parent.status == 'error'
        assert parent.validation_error == 'ValueError: boom'
        on_error.assert_called_once()
        on_complete.assert_not_called()

    def test_in_progress_child_is_noop(self):
        child = _child('in_progress')
        parent = _parent_waiting_on(child)
        resolve_deferred_parent_waiting_on(child)
        parent.refresh_from_db()
        assert parent.status == 'in_progress'

    def test_second_resolution_is_noop(self):
        child = _child('done', parsed_result={}, raw_result='')
        _parent_waiting_on(child)
        with patch(ON_COMPLETE) as on_complete:
            resolve_deferred_parent_waiting_on(child)
            resolve_deferred_parent_waiting_on(child)
        assert on_complete.call_count == 1


@pytest.mark.django_db
def test_terminal_worker_error_via_http_fails_waiting_parent():
    user = User.objects.create_user(f'zzq-w-{uuid.uuid4().hex[:8]}')
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.key}')
    child = _child('in_progress', max_retries=1)
    parent = _parent_waiting_on(child)
    with patch(ON_ERROR):
        resp = client.post(
            f'/api/engine/work-items/{child.uuid}/complete/',
            {'raw_result': '', 'status': 'error', 'error_message': 'JobError: blob 404'}, format='json',
        )
    assert resp.status_code == 200
    parent.refresh_from_db()
    assert parent.status == 'error'
    assert 'blob 404' in parent.validation_error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJTEST engine/tests/test_deferred_waiting_on.py -v`
Expected: FAIL — `test_done_child_merges_parsed_result…` (parsed_result lacks `text`), `test_error_child_fails_parent` (parent stays `in_progress`), HTTP test (parent stays `in_progress`).

- [ ] **Step 3: Implement**

Replace the body of `resolve_deferred_parent_waiting_on` in `engine/services/workitem_factory.py`:

```python
def resolve_deferred_parent_waiting_on(work_item) -> None:
    """Resolve the ADR-040 deferred parent waiting on `work_item` (its
    metadata.waiting_on_workitem_uuid) once `work_item` is terminal.

    done  → parent done; parsed_result = the child's own parsed_result keys
            plus `result_uuid`/`raw_result` (kept for existing readers, e.g.
            clusterops machine_bootstrap._get_sibling_result) — so downstream
            steps and `field_mapping` ('<capability>.<key>') read the child's
            result directly (spec 2026-10-06 django-engine-integration §3.4).
    error → parent error with the child's validation_error — previously the
            parent stayed in_progress forever and its workflow hung.
    Writes only through finish_deferred_parent() (workitem-deferred rule 4;
    atomic, idempotent). No-op for non-terminal children or when nothing waits.
    """
    if work_item.status not in ('done', 'error'):
        return
    pending = WorkItem.objects.filter(
        status='in_progress', metadata__waiting_on_workitem_uuid=str(work_item.uuid),
    ).first()
    if pending is None:
        return

    from engine.services.deferred_resolution import finish_deferred_parent

    base = {'result_uuid': str(work_item.uuid), 'raw_result': work_item.raw_result}
    if work_item.status == 'done':
        child_result = work_item.parsed_result if isinstance(work_item.parsed_result, dict) else {}
        finish_deferred_parent(pending, success=True, parsed_result={**child_result, **base})
    else:
        finish_deferred_parent(
            pending, success=False, parsed_result=base,
            error=work_item.validation_error or 'dependency WorkItem failed',
        )
```

In `engine/views_work_item.py::_handle_worker_error`, inside `if task.status == "error":` right after `WorkflowExecutor.on_error(task)` add:

```python
        from engine.services.workitem_factory import resolve_deferred_parent_waiting_on
        resolve_deferred_parent_waiting_on(task)
```

In `_handle_validation_error`, in the terminal branch right after `WorkflowExecutor.on_error(task)` add the same two lines.

- [ ] **Step 4: Run tests**

Run: `DJTEST engine/tests/test_deferred_waiting_on.py engine/tests/test_deferred_resolution.py engine/tests/test_machine_bootstrap_nixos_existing_blueprint.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/engine/services/workitem_factory.py django_api/engine/views_work_item.py django_api/engine/tests/test_deferred_waiting_on.py
git -C ~/repos/swarm-nix commit -m "engine: waiting_on deferred parents merge child result and fail on child error"
```

### Task A4: structured-контракт у `_handle_validation_error`

**Files:**
- Modify: `engine/views_work_item.py::_handle_validation_error` (~рядки 529-606)
- Test: `engine/tests/test_structured_validation_retry.py`

**Interfaces:**
- Consumes: `get_structured_worker_types()` (A1).
- Produces: structured WorkItem після невдалої валідації → `status='invalid'`, `prompt` незмінний, `retry_history` поповнено; на вичерпанні — `error` без `maybe_decompose`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_structured_validation_retry.py
"""structured_json WorkItems: validation retry keeps the JSON payload, never decomposes
(spec 2026-10-06 django-engine-integration §3.3)."""
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from engine.models import WorkerType, WorkItem

PAYLOAD = '{"v": 1, "texts": ["a"]}'


def _client():
    user = User.objects.create_user(f'zzq-w-{uuid.uuid4().hex[:8]}')
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.key}')
    return client


def _item(worker_type, max_retries=2):
    return WorkItem.objects.create(
        worker_type=worker_type, artifact_type='zzq_struct_art', prompt=PAYLOAD,
        expected_format='json', status='in_progress', max_retries=max_retries,
    )


@pytest.mark.django_db
class TestStructuredValidationRetry:
    @pytest.fixture(autouse=True)
    def _struct_type(self, db):
        WorkerType.objects.get_or_create(name='zzq_struct', defaults={'display_name': 'zzq', 'payload_contract': 'structured_json'})

    def test_invalid_result_keeps_payload(self):
        wi = _item('zzq_struct')
        resp = _client().post(f'/api/engine/work-items/{wi.uuid}/complete/', {'raw_result': 'not json at all'}, format='json')
        assert resp.status_code == 422
        wi.refresh_from_db()
        assert wi.status == 'invalid'
        assert wi.prompt == PAYLOAD
        assert wi.retry_history[0]['prompt'] == PAYLOAD

    def test_exhausted_retries_error_without_decompose(self):
        wi = _item('zzq_struct', max_retries=1)
        with patch('engine.services.decomposer.maybe_decompose') as decompose, \
                patch('engine.services.workflow_executor.WorkflowExecutor.on_error'):
            resp = _client().post(f'/api/engine/work-items/{wi.uuid}/complete/', {'raw_result': 'nope'}, format='json')
        assert resp.status_code == 422
        decompose.assert_not_called()
        wi.refresh_from_db()
        assert wi.status == 'error'
        assert wi.prompt == PAYLOAD

    def test_llm_prompt_items_still_get_retry_prompt(self):
        wi = _item('llm')
        _client().post(f'/api/engine/work-items/{wi.uuid}/complete/', {'raw_result': 'not json at all'}, format='json')
        wi.refresh_from_db()
        assert wi.status == 'invalid'
        assert wi.prompt != PAYLOAD
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJTEST engine/tests/test_structured_validation_retry.py -v`
Expected: FAIL — `test_invalid_result_keeps_payload` (`prompt` переписано), `test_exhausted…` (`maybe_decompose` викликано).

- [ ] **Step 3: Implement**

In `_handle_validation_error`, after computing `error_msg` add:

```python
    # spec 2026-10-06 §3.3: a structured_json payload is not LLM instructions —
    # never wrap it in a retry prompt, never ask an LLM to "reconsolidate" it.
    structured = task.worker_type in set(get_structured_worker_types())
```

Replace the exhaustion header

```python
    if task.retry_count >= task.max_retries:
        from engine.services.decomposer import maybe_decompose
        if maybe_decompose(task):
```

with

```python
    if task.retry_count >= task.max_retries:
        from engine.services.decomposer import maybe_decompose
        if not structured and maybe_decompose(task):
```

and replace

```python
    task.prompt = build_retry_prompt(
        original_prompt=_get_original_prompt_text(task),
        raw_result=raw_result,
        error=error_msg,
    )
```

with

```python
    if not structured:
        task.prompt = build_retry_prompt(
            original_prompt=_get_original_prompt_text(task),
            raw_result=raw_result,
            error=error_msg,
        )
```

(Патчимо `engine.services.decomposer.maybe_decompose` — функція імпортується локально в тілі, тож патч модульного атрибута діє.)

- [ ] **Step 4: Run tests**

Run: `DJTEST engine/tests/test_structured_validation_retry.py engine/tests/test_maybe_decompose_workitem.py engine/tests/test_deferred_waiting_on.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/engine/views_work_item.py django_api/engine/tests/test_structured_validation_retry.py
git -C ~/repos/swarm-nix commit -m "engine: structured_json WorkItems keep payload on validation retry, never decompose"
```

### Task A5: per-row claim-таймаут (`WorkItem.timeout_seconds`)

**Files:**
- Modify: `engine/services/queue_health.py` (нова функція)
- Modify: `engine/management/commands/engine_poll.py:396-…` (`_reset_stale`)
- Modify: `engine/management/commands/reset_stale_workitems.py:40-100`
- Test: `engine/tests/test_reset_stale_per_item_timeout.py`

**Interfaces:**
- Produces: `engine.services.queue_health.overdue_claims(queryset, now) -> QuerySet` — `in_progress` рядки, де `claimed_at + timeout_seconds < now`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_reset_stale_per_item_timeout.py
"""Stale-claim reset honours each WorkItem's own timeout_seconds
(spec 2026-10-06 django-engine-integration §3.5)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from engine.management.commands.engine_poll import Command as EnginePoll
from engine.models import WorkItem
from engine.services.queue_health import overdue_claims


def _claimed(seconds_ago, timeout_seconds):
    return WorkItem.objects.create(
        worker_type='llm', artifact_type='zzq_stale_art', prompt='x', status='in_progress',
        claimed_at=timezone.now() - timedelta(seconds=seconds_ago), timeout_seconds=timeout_seconds,
    )


@pytest.mark.django_db
class TestOverdueClaims:
    def test_only_items_past_their_own_timeout(self):
        long_job = _claimed(400, 3600)
        short_job = _claimed(400, 300)
        fresh = _claimed(10, 300)
        qs = WorkItem.objects.filter(pk__in=[long_job.pk, short_job.pk, fresh.pk])
        assert set(overdue_claims(qs, timezone.now()).values_list('pk', flat=True)) == {short_job.pk}

    def test_engine_poll_reset_keeps_long_job_claimed(self):
        long_job = _claimed(400, 3600)
        short_job = _claimed(400, 300)
        EnginePoll()._reset_stale(dry_run=False)
        long_job.refresh_from_db()
        short_job.refresh_from_db()
        assert long_job.status == 'in_progress'
        assert short_job.status == 'todo'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJTEST engine/tests/test_reset_stale_per_item_timeout.py -v`
Expected: FAIL — `ImportError: cannot import name 'overdue_claims'`.

- [ ] **Step 3: Implement `overdue_claims`**

Add to `engine/services/queue_health.py` (imports at module top: `from datetime import timedelta`, `from django.db.models import DateTimeField, DurationField, ExpressionWrapper, F, Value` — додай лише відсутні):

```python
def overdue_claims(queryset, now):
    """in_progress WorkItems whose claimed_at + their OWN timeout_seconds has
    passed. Replaces the global 300 s threshold (spec 2026-10-06
    django-engine-integration §3.5): a 30-minute ASR job must not be reset
    after 5 minutes. timeout_seconds defaults to 300, so every pre-existing
    WorkItem behaves exactly as before."""
    allowed = ExpressionWrapper(F('timeout_seconds') * Value(timedelta(seconds=1)), output_field=DurationField())
    deadline = ExpressionWrapper(F('claimed_at') + allowed, output_field=DateTimeField())
    return (
        queryset.filter(status='in_progress', claimed_at__isnull=False)
        .annotate(_claim_deadline=deadline)
        .filter(_claim_deadline__lt=now)
    )
```

- [ ] **Step 4: Use it in both reset paths**

`engine_poll.py::_reset_stale` — replace

```python
        now = timezone.now()
        threshold = now - timedelta(seconds=timeout)
        stale = exclude_deferred_parents(WorkItem.objects.filter(
            status='in_progress',
            claimed_at__lt=threshold,
            stalled_since__isnull=True,
        ))
```

with

```python
        from engine.services.queue_health import overdue_claims

        now = timezone.now()
        stale = overdue_claims(
            exclude_deferred_parents(WorkItem.objects.filter(stalled_since__isnull=True)), now,
        )
```

and in the same method replace `delta={'claimed_at': str(wi.claimed_at), 'timeout_seconds': timeout}` with `delta={'claimed_at': str(wi.claimed_at), 'timeout_seconds': wi.timeout_seconds}`. Remove the `timeout: int = 300` parameter from `_reset_stale`'s signature (no-backwards-compat policy: the only caller, `self._reset_stale(dry_run)`, never passes it — verify with `rg -n "_reset_stale\(" django_api/`) and update its docstring to say the threshold is each row's `timeout_seconds`.

`reset_stale_workitems.py::handle` — replace the threshold/queryset block with:

```python
        from engine.services.queue_health import overdue_claims
        from engine.services.workitem_factory import exclude_deferred_parents

        timeout = options['timeout']
        dry_run = options['dry_run']
        base = exclude_deferred_parents(WorkItem.objects.all())
        if timeout is None:
            # spec 2026-10-06 §3.5: each WorkItem's own timeout_seconds.
            stale_items = overdue_claims(base, timezone.now())
        else:
            # explicit operator override — one global threshold.
            stale_items = base.filter(
                status='in_progress', claimed_at__lt=timezone.now() - timedelta(seconds=timeout),
            )
```

remove the now-unused `RetryPolicy.resolve()` default lookup and update `--timeout` help to `'Global override in seconds (default: each WorkItem's own timeout_seconds)'`.

- [ ] **Step 5: Run tests**

Run: `DJTEST engine/tests/test_reset_stale_per_item_timeout.py engine/tests/test_management_commands.py engine/tests/test_engine_poll_retention.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/engine/services/queue_health.py django_api/engine/management/commands/engine_poll.py django_api/engine/management/commands/reset_stale_workitems.py django_api/engine/tests/test_reset_stale_per_item_timeout.py
git -C ~/repos/swarm-nix commit -m "engine: stale-claim reset uses per-WorkItem timeout_seconds"
```

### Task A6: `LLMService.kind` + `port` при реєстрації

**Files:**
- Modify: `llm_services/constants.py`, `llm_services/models.py`, `llm_services/api_token_views.py`, `llm_services/admin.py`
- Create: `llm_services/migrations/0007_llmservice_kind.py`
- Test: `llm_services/tests/test_api_token_views.py` (доповнити)

**Interfaces:**
- Produces: `LLMService.kind ∈ {'llm','asr','mt','tts'}` (default `'llm'`); `llm_services.constants.SERVICE_KIND_CHOICES`, `SERVICE_KINDS`; `POST /api/llm/register/` приймає опційні `kind`, `port`; відповідь містить `kind`; 400 `invalid_kind`/`invalid_port`; 409 `kind_conflict`.

- [ ] **Step 1: Write the failing tests** (append to `llm_services/tests/test_api_token_views.py`)

```python
@pytest.mark.django_db
class TestRegisterServiceKindAndPort:
    def _post(self, **body):
        return Client().post(
            reverse("llm_services:llm-register"),
            data={"bootstrap_token": "test-bootstrap-token", **body},
            content_type="application/json",
            REMOTE_ADDR="100.64.0.7",
        )

    def test_speech_service_registers_with_kind_and_port(self):
        from llm_services.models import LLMService
        resp = self._post(service_name="zzq-host-rt-asr", kind="asr", port=8091)
        assert resp.status_code == 200
        assert resp.json()["kind"] == "asr"
        svc = LLMService.objects.get(name="zzq-host-rt-asr")
        assert (svc.kind, svc.base_url) == ("asr", "http://100.64.0.7:8091")

    def test_reregistration_with_port_updates_base_url(self):
        from llm_services.models import LLMService
        self._post(service_name="zzq-host-rt-mt", kind="mt", port=8092)
        Client().post(
            reverse("llm_services:llm-register"),
            data={"bootstrap_token": "test-bootstrap-token", "service_name": "zzq-host-rt-mt", "kind": "mt", "port": 9092},
            content_type="application/json", REMOTE_ADDR="100.64.0.9",
        )
        assert LLMService.objects.get(name="zzq-host-rt-mt").base_url == "http://100.64.0.9:9092"

    def test_legacy_llama_registration_unchanged(self):
        from llm_services.models import LLMService
        resp = self._post(service_name="zzq-llama-box")
        assert resp.status_code == 200
        svc = LLMService.objects.get(name="zzq-llama-box")
        assert (svc.kind, svc.base_url) == ("llm", "http://100.64.0.7:8080")

    def test_invalid_kind_is_400_without_side_effects(self):
        from django.contrib.auth.models import User
        resp = self._post(service_name="zzq-bad-kind", kind="gpu")
        assert resp.status_code == 400
        assert resp.json()["code"] == "invalid_kind"
        assert not User.objects.filter(username="zzq-bad-kind").exists()

    @pytest.mark.parametrize("port", [0, 70000, "abc"])
    def test_invalid_port_is_400(self, port):
        resp = self._post(service_name="zzq-bad-port", kind="tts", port=port)
        assert resp.status_code == 400
        assert resp.json()["code"] == "invalid_port"

    def test_kind_conflict_is_409(self):
        self._post(service_name="zzq-conflict", kind="asr", port=8091)
        resp = self._post(service_name="zzq-conflict", kind="tts", port=8093)
        assert resp.status_code == 409
        assert resp.json()["code"] == "kind_conflict"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJTEST llm_services/tests/test_api_token_views.py -v`
Expected: FAIL — `KeyError: 'kind'` / `AttributeError: 'LLMService' object has no attribute 'kind'`.

- [ ] **Step 3: Constants, model, migration**

`llm_services/constants.py` — додати:

```python
# What kind of compute service an LLMService row is (spec 2026-10-06
# django-engine-integration §3.8). 'llm' = llama FastAPI (historic default).
SERVICE_KIND_CHOICES = [
    ("llm", "LLM (llama)"),
    ("asr", "ASR (rt-asr)"),
    ("mt", "MT (rt-mt)"),
    ("tts", "TTS (rt-tts)"),
]
SERVICE_KINDS = frozenset(k for k, _ in SERVICE_KIND_CHOICES)
```

`llm_services/models.py` — `from .constants import SERVICE_KIND_CHOICES, SERVICE_STATUS_CHOICES`; у `LLMService` після `base_url`:

```python
    kind = models.CharField(
        max_length=10, choices=SERVICE_KIND_CHOICES, default="llm", db_index=True,
        help_text="llm = llama FastAPI; asr/mt/tts = rt-speech services (spec 2026-10-06 §3.8). "
                  "Set at registration; a different kind for an existing name is rejected (409).",
    )
```

```python
# llm_services/migrations/0007_llmservice_kind.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('llm_services', '0006_llmservice_node'),
    ]

    operations = [
        migrations.AddField(
            model_name='llmservice',
            name='kind',
            field=models.CharField(
                choices=[('llm', 'LLM (llama)'), ('asr', 'ASR (rt-asr)'), ('mt', 'MT (rt-mt)'), ('tts', 'TTS (rt-tts)')],
                db_index=True, default='llm', max_length=10,
                help_text='llm = llama FastAPI; asr/mt/tts = rt-speech services (spec 2026-10-06 §3.8). '
                          'Set at registration; a different kind for an existing name is rejected (409).',
            ),
        ),
    ]
```

- [ ] **Step 4: Registration view**

In `llm_services/api_token_views.py` add `from llm_services.constants import SERVICE_KINDS` and helpers:

```python
def _remote_ip(request) -> str:
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "unknown")


def _parse_port(raw) -> int | None:
    """Optional `port` from the registration body; None when absent."""
    if raw is None:
        return None
    try:
        port = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"port must be an integer, got {raw!r}")
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    return port


def _error(message: str, code: str, http_status: int) -> Response:
    return Response({"error": message, "code": code, "details": {}}, status=http_status)
```

In `register_service`, right after the `if not service_name:` check (before creating the User), add:

```python
    kind = request.data.get("kind") or "llm"
    if kind not in SERVICE_KINDS:
        return _error(f"unknown kind {kind!r}", "invalid_kind", status.HTTP_400_BAD_REQUEST)
    try:
        port = _parse_port(request.data.get("port"))
    except ValueError as exc:
        return _error(str(exc), "invalid_port", status.HTTP_400_BAD_REQUEST)
    existing = LLMService.objects.filter(name=service_name).first()
    if existing is not None and existing.kind != kind:
        return _error(
            f"{service_name} is registered as kind={existing.kind!r}, not {kind!r}",
            "kind_conflict", status.HTTP_409_CONFLICT,
        )
```

Replace the `base_url = …` + `get_or_create` block with:

```python
    remote_ip = _remote_ip(request)
    service, svc_created = LLMService.objects.get_or_create(
        name=service_name,
        defaults={"base_url": f"http://{remote_ip}:{port or 8080}", "status": "offline", "kind": kind},
    )
    if port is not None and service.base_url != f"http://{remote_ip}:{port}":
        # A service that announces its port owns its address: it may have moved hosts.
        service.base_url = f"http://{remote_ip}:{port}"
        service.save(update_fields=["base_url", "updated_at"])
```

and add `"kind": service.kind,` to the response dict. Update the module docstring request example: `{"service_name": "cyborg-rt-asr", "bootstrap_token": "...", "kind"?: "asr", "port"?: 8091}`.

`llm_services/admin.py` — in the `LLMService` ModelAdmin add `"kind"` to `list_display` and `list_filter` (якщо `list_filter` немає — створити `list_filter = ("kind", "status", "is_active")`).

- [ ] **Step 5: Run tests**

Run: `DJTEST llm_services/tests/ -v` → PASS.
Run: `DJMANAGE makemigrations llm_services --check --dry-run` → `No changes detected`.

- [ ] **Step 6: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/llm_services/
git -C ~/repos/swarm-nix commit -m "llm_services: LLMService.kind; registration accepts kind and port"
```

### Task A7: health sync за kind + фікс `model.path` + runtime-config лише для llm

**Files:**
- Modify: `llm_services/services/health_service.py:18-95` (`sync_service`)
- Modify: `llm_services/selectors.py:88-110` (`push_runtime_config_to_nodes`)
- Modify: `llm_services/tests/test_health_service.py` (рядки 36, 55, 79 — форма llama `/status`)
- Test: `llm_services/tests/test_health_service_kinds.py`, `llm_services/tests/test_selectors.py` (доповнити)

**Interfaces:**
- Consumes: `LLMService.kind` (A6).
- Produces: `sync_service(service)` — dispatch `_STATUS_SYNCERS[service.kind]`; невідомий kind → `ValueError`.

- [ ] **Step 1: Fix existing llama-shape tests**

In `llm_services/tests/test_health_service.py` replace the three mocked statuses with the real llama shape (`system-conf/common/modules/llm/app/api/routes_status.py`):
- line 36 and line 55: `'model_path': '/path/to/model.gguf',` → `'model': {'loaded': True, 'path': '/path/to/model.gguf'},`
- line 79: `'model_path': '',` → `'model': {'loaded': False, 'path': None},`

(Assertions stay: `current_model == '/path/to/model.gguf'`, `model.gguf` `is_loaded`.) In `llm_services/tests/test_client.py:60` leave as is (it tests the raw client passthrough, not the sync).

- [ ] **Step 2: Write the failing tests**

```python
# llm_services/tests/test_health_service_kinds.py
"""sync_service dispatch by LLMService.kind (spec 2026-10-06 django-engine-integration §3.8)."""
from unittest.mock import MagicMock, patch

import pytest

from llm_services.models import LLMService
from llm_services.services.health_service import sync_service

CLIENT = 'llm_services.services.health_service.LLMServiceClient'


def _speech_status(**over):
    status = {
        'service': 'rt-asr', 'version': '0.1.0', 'device': 'cuda', 'backend': 'faster-whisper',
        'model': {'id': 'small', 'loaded': True, 'state': 'ready', 'load_seconds': 3.2, 'error': None},
        'ready': True, 'vram_mb': 812, 'pending': 0,
        'worker': {'enabled': True, 'running': True, 'error': None},
    }
    status.update(over)
    return status


@pytest.fixture
def speech_service(db):
    return LLMService.objects.create(name='zzq-host-rt-asr', base_url='http://127.0.0.1:8091', token='t', kind='asr')


def _sync(service, status):
    client = MagicMock()
    client.get_status.return_value = status
    with patch(CLIENT, return_value=client):
        sync_service(service)
    service.refresh_from_db()
    return client


def test_speech_online_without_models_call(speech_service):
    client = _sync(speech_service, _speech_status())
    assert (speech_service.status, speech_service.current_model) == ('online', 'small')
    client.list_models.assert_not_called()


def test_speech_loading_when_not_ready(speech_service):
    _sync(speech_service, _speech_status(ready=False, model={'id': 'small', 'loaded': False, 'state': 'loading', 'error': None}))
    assert speech_service.status == 'loading'


def test_speech_disabled_when_worker_disabled(speech_service):
    _sync(speech_service, _speech_status(worker={'enabled': False}))
    assert speech_service.status == 'disabled'


def test_speech_error_when_model_failed(speech_service):
    _sync(speech_service, _speech_status(ready=False, model={'id': 'small', 'loaded': False, 'state': 'failed', 'error': 'CUDA OOM'}))
    assert speech_service.status == 'error'
    assert 'CUDA OOM' in speech_service.error_message


def test_speech_does_not_touch_reported_tier(speech_service):
    _sync(speech_service, _speech_status(tier=4))
    assert speech_service.reported_tier is None


def test_llama_current_model_read_from_nested_path(db):
    svc = LLMService.objects.create(name='zzq-llama', base_url='http://127.0.0.1:8080', token='t')
    client = MagicMock()
    client.get_status.return_value = {'service': 'healthy', 'model': {'loaded': True, 'path': '/m/qwen.gguf'}, 'tier': 2, 'worker': {'enabled': True}}
    client.list_models.return_value = ['qwen.gguf']
    with patch(CLIENT, return_value=client):
        sync_service(svc)
    svc.refresh_from_db()
    assert svc.current_model == '/m/qwen.gguf'
    assert svc.models.get(filename='qwen.gguf').is_loaded


def test_unknown_kind_raises(db):
    svc = LLMService.objects.create(name='zzq-weird', base_url='http://x', token='t')
    LLMService.objects.filter(pk=svc.pk).update(kind='zzz')
    svc.refresh_from_db()
    with pytest.raises(ValueError):
        sync_service(svc)
```

Append to `llm_services/tests/test_selectors.py`:

```python
@pytest.mark.django_db
def test_push_runtime_config_skips_non_llm_services():
    from unittest.mock import patch
    from clusterops.models import Node
    from llm_services.models import LLMService
    from llm_services.selectors import push_runtime_config_to_nodes

    node = Node.objects.create(name='zzq-node-push')
    LLMService.objects.create(name='zzq-push-llama', base_url='http://a', token='t', node=node)
    LLMService.objects.create(name='zzq-push-asr', base_url='http://b', token='t', node=node, kind='asr')
    with patch('llm_services.selectors.LLMServiceClient') as client_cls:
        results = push_runtime_config_to_nodes([node.pk], version=1, values={})
    assert [r['llm_service'] for r in results] == ['zzq-push-llama']
    assert client_cls.call_count == 1
```

(Якщо `test_selectors.py` — `unittest.TestCase`-стиль без `import pytest`, додай `import pytest` нагорі.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `DJTEST llm_services/tests/test_health_service.py llm_services/tests/test_health_service_kinds.py llm_services/tests/test_selectors.py -v`
Expected: FAIL — llama tests (`current_model == ''`), speech tests (`list_models` called / status online), push test (2 results).

- [ ] **Step 4: Implement the dispatch**

Rewrite `sync_service` in `llm_services/services/health_service.py`:

```python
def _sync_llama_status(service: LLMService, client: LLMServiceClient, status_data: dict) -> None:
    """llama FastAPI (`kind='llm'`). Model path is nested: /status → model.path
    (system-conf .../api/routes_status.py) — the old top-level `model_path`
    read never existed there, so current_model was always ''."""
    worker_enabled = (status_data.get("worker", {}) or {}).get("enabled", True)
    if worker_enabled:
        service.status = "online"
        service.error_message = ""
    else:
        service.status = "disabled"
        service.error_message = "Worker disabled (workerEnabled=false) — not polling queue"
    model_path = (status_data.get("model") or {}).get("path") or ""
    service.current_model = model_path
    service.last_seen = now()

    # TASK-ENG-19 (TD-24): flag-only tier drift check (unchanged).
    reported_tier = status_data.get("tier")
    if reported_tier is not None:
        service.reported_tier = reported_tier
        if reported_tier != service.tier:
            logger.warning(
                "Service %s: tier drift — DB tier=%s but worker reports tier=%s "
                "(TIER env var). WorkItems may be misrouted; update LLMService.tier "
                "to match if the worker's value is correct.",
                service.name, service.tier, reported_tier,
            )

    model_files_data = client.list_models()
    model_files = [m.get("name", m) if isinstance(m, dict) else m for m in model_files_data]
    loaded_name = model_path.split("/")[-1] if model_path else ""
    deleted_count = service.models.exclude(filename__in=model_files).delete()[0]
    if deleted_count:
        logger.debug("Deleted %s stale models for %s", deleted_count, service.name)
    for filename in model_files:
        LLMModel.objects.update_or_create(
            service=service, filename=filename, defaults={"is_loaded": filename == loaded_name},
        )


def _sync_speech_status(service: LLMService, client: LLMServiceClient, status_data: dict) -> None:
    """rt-asr/rt-mt/rt-tts (`kind` asr/mt/tts): /status only — no /models,
    no tier (spec 2026-10-06 django-engine-integration §3.8)."""
    model = status_data.get("model") or {}
    worker = status_data.get("worker") or {}
    service.current_model = model.get("id") or ""
    service.last_seen = now()
    if model.get("state") == "failed":
        service.status = "error"
        service.error_message = f"Model load failed: {model.get('error') or 'unknown error'}"
    elif worker.get("enabled") is False:
        service.status = "disabled"
        service.error_message = "Django worker disabled (RT_WORKER_ENABLED=false) — not polling queue"
    elif not status_data.get("ready"):
        service.status = "loading"
        service.error_message = ""
    else:
        service.status = "online"
        service.error_message = worker.get("error") or ""


_STATUS_SYNCERS = {
    "llm": _sync_llama_status,
    "asr": _sync_speech_status,
    "mt": _sync_speech_status,
    "tts": _sync_speech_status,
}


def sync_service(service: LLMService) -> None:
    """Synchronize one service's state from its /status (dispatch by kind)."""
    syncer = _STATUS_SYNCERS.get(service.kind)
    if syncer is None:
        raise ValueError(f"Unknown LLMService.kind {service.kind!r} for {service.name}")
    logger.info("Syncing service: %s (ID: %s, kind=%s)", service.name, service.id, service.kind)
    client = LLMServiceClient(service)
    try:
        status_data = client.get_status()
        syncer(service, client, status_data)
    except requests.RequestException as e:
        logger.error("Service %s connection error: %s", service.name, e)
        service.status = "error"
        service.error_message = f"Connection error: {e}"
    except Exception as e:
        logger.exception("Service %s sync failed: %s", service.name, e)
        service.status = "error"
        service.error_message = str(e)

    service.save(update_fields=[
        "status", "current_model", "last_seen", "error_message", "reported_tier",
    ])
```

`llm_services/selectors.py::push_runtime_config_to_nodes` — `LLMService.objects.filter(node_id__in=node_ids)` → `LLMService.objects.filter(node_id__in=node_ids, kind="llm")` + docstring line: "llama-only: rt-speech services have no ServiceRuntimeConfig (spec 2026-10-06 §3.8)."

- [ ] **Step 5: Run tests**

Run: `DJTEST llm_services/tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/llm_services/
git -C ~/repos/swarm-nix commit -m "llm_services: kind-aware health sync, fix nested model.path, llama-only runtime config push"
```

### Task A8: app `speech` — `AudioBlob`, сховище, settings, blob-сервіс

**Files:**
- Create: `speech/__init__.py` (порожній), `speech/apps.py`, `speech/constants.py`, `speech/storage.py`, `speech/models.py`, `speech/admin.py`, `speech/services/__init__.py` (порожній), `speech/services/blobs.py`, `speech/migrations/__init__.py` (порожній), `speech/migrations/0001_initial.py` (генерується), `speech/tests/__init__.py` (порожній), `speech/tests/conftest.py`
- Modify: `knowledge_base_api/settings.py` (`INSTALLED_APPS` ~рядок 94; новий блок `SPEECH_*` після `LLM_BOOTSTRAP_TOKEN` ~рядок 404)
- Test: `speech/tests/test_blobs.py`

**Interfaces:**
- Produces: `speech.models.AudioBlob` (поля spec §3.6; `Role.INPUT/OUTPUT`; `is_expired(now=None) -> bool`); `speech.services.blobs.BlobError(code, message)` (`.code` ∈ `empty|too_large|unsupported_content_type|not_found|expired|invalid_role`); `store_blob(source: bytes | File, *, content_type: str, role: str, work_item_uuid=None, uploaded_by_service_id: int | None = None) -> AudioBlob`; `get_live_blob(blob_id) -> AudioBlob`; `wav_duration(fileobj, content_type) -> float | None`; `gc_expired_blobs(now=None) -> int`; constants у `speech.constants` (див. Step 3); тест-фікстури `make_wav`, `speech_storage_root`.

- [ ] **Step 1: Test fixtures + failing tests**

```python
# speech/tests/conftest.py
import io
import wave

import pytest


def make_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """Silent PCM16 mono WAV."""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b'\x00\x00' * int(seconds * rate))
    return buf.getvalue()


@pytest.fixture
def speech_storage_root(tmp_path, settings):
    root = tmp_path / 'speech-store'
    settings.SPEECH_STORAGE_ROOT = root
    return root


@pytest.fixture
def wav():
    return make_wav
```

```python
# speech/tests/test_blobs.py
import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone

from speech.models import AudioBlob
from speech.services.blobs import BlobError, gc_expired_blobs, get_live_blob, store_blob


@pytest.mark.django_db
class TestStoreBlob:
    def test_input_wav_records_hash_size_duration_and_ttl(self, speech_storage_root, wav):
        data = wav(seconds=2.0)
        blob = store_blob(data, content_type='audio/wav', role='input')
        assert blob.size_bytes == len(data)
        assert blob.sha256 == hashlib.sha256(data).hexdigest()
        assert blob.duration_seconds == pytest.approx(2.0)
        assert blob.file.path.startswith(str(speech_storage_root))
        with blob.file.open('rb') as fh:
            assert fh.read() == data
        assert abs((blob.expires_at - blob.created_at) - timedelta(days=7)) < timedelta(seconds=5)

    def test_non_wav_input_has_no_duration(self, speech_storage_root):
        blob = store_blob(b'fLaC-not-really', content_type='audio/flac', role='input')
        assert blob.duration_seconds is None

    @pytest.mark.parametrize('content_type', ['video/mp4', 'audio/webm', 'text/plain'])
    def test_rejects_unsupported_input_type(self, speech_storage_root, content_type):
        with pytest.raises(BlobError) as exc:
            store_blob(b'x', content_type=content_type, role='input')
        assert exc.value.code == 'unsupported_content_type'

    def test_output_only_wav(self, speech_storage_root):
        with pytest.raises(BlobError) as exc:
            store_blob(b'x', content_type='audio/flac', role='output')
        assert exc.value.code == 'unsupported_content_type'

    def test_rejects_too_large(self, speech_storage_root, settings):
        settings.SPEECH_MAX_INPUT_BYTES = 10
        with pytest.raises(BlobError) as exc:
            store_blob(b'x' * 11, content_type='audio/wav', role='input')
        assert exc.value.code == 'too_large'

    def test_rejects_empty_and_bad_role(self, speech_storage_root):
        with pytest.raises(BlobError) as exc:
            store_blob(b'', content_type='audio/wav', role='input')
        assert exc.value.code == 'empty'
        with pytest.raises(BlobError) as exc:
            store_blob(b'x', content_type='audio/wav', role='scratch')
        assert exc.value.code == 'invalid_role'


@pytest.mark.django_db
class TestLiveBlobAndGc:
    def test_get_live_blob(self, speech_storage_root, wav):
        blob = store_blob(wav(), content_type='audio/wav', role='input')
        assert get_live_blob(str(blob.uuid)) == blob
        with pytest.raises(BlobError) as exc:
            get_live_blob('not-a-uuid')
        assert exc.value.code == 'not_found'
        AudioBlob.objects.filter(pk=blob.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with pytest.raises(BlobError) as exc:
            get_live_blob(str(blob.uuid))
        assert exc.value.code == 'expired'

    def test_gc_deletes_expired_rows_and_files(self, speech_storage_root, wav):
        old = store_blob(wav(), content_type='audio/wav', role='input')
        fresh = store_blob(wav(), content_type='audio/wav', role='input')
        old_path = old.file.path
        AudioBlob.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(days=1))
        assert gc_expired_blobs() >= 1
        assert not AudioBlob.objects.filter(pk=old.pk).exists()
        assert AudioBlob.objects.filter(pk=fresh.pk).exists()
        import os
        assert not os.path.exists(old_path)
```

- [ ] **Step 2: Settings + app registration**

`knowledge_base_api/settings.py` — `INSTALLED_APPS` додати після `'identity',`:

```python
    'speech',        # Speech services (rt-asr/rt-mt/rt-tts) via WorkItem queue — AudioBlob, speech_* capabilities
```

після `LLM_BOOTSTRAP_TOKEN = …`:

```python
# Speech services (spec 2026-10-06 django-engine-integration §3.6) — audio
# blobs passed to rt-asr/rt-tts BY REFERENCE; never served as static/media.
SPEECH_STORAGE_ROOT = Path(os.environ.get('SPEECH_STORAGE_ROOT', str(BASE_DIR / 'var' / 'speech')))
SPEECH_MAX_INPUT_BYTES = int(os.environ.get('SPEECH_MAX_INPUT_BYTES', 52428800))
SPEECH_MAX_OUTPUT_BYTES = int(os.environ.get('SPEECH_MAX_OUTPUT_BYTES', 209715200))
SPEECH_BLOB_TTL_DAYS = int(os.environ.get('SPEECH_BLOB_TTL_DAYS', 7))
SPEECH_MT_MAX_CHARS = int(os.environ.get('SPEECH_MT_MAX_CHARS', 20000))
SPEECH_MT_MAX_ITEM_CHARS = int(os.environ.get('SPEECH_MT_MAX_ITEM_CHARS', 1000))
SPEECH_TTS_MAX_CHARS = int(os.environ.get('SPEECH_TTS_MAX_CHARS', 4000))
SPEECH_ASR_DEFAULT_TIMEOUT = int(os.environ.get('SPEECH_ASR_DEFAULT_TIMEOUT', 1800))
```

(`Path` уже імпортовано на початку settings — `BASE_DIR = Path(...)`.) Додати `django_api/var/` до `.gitignore` swarm-nix, якщо його там немає (`rg -n "^django_api/var|^var/" ~/repos/swarm-nix/.gitignore`).

```python
# speech/apps.py
from django.apps import AppConfig


class SpeechConfig(AppConfig):
    """Speech services bounded context: AudioBlob storage + speech_* engine
    capabilities (spec 2026-10-06 django-engine-integration §3.6)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'speech'
    verbose_name = 'Speech services (ASR/MT/TTS)'
```

- [ ] **Step 3: Constants, storage, model**

```python
# speech/constants.py
"""Wire-contract names shared with rt-asr/rt-mt/rt-tts (spec 2026-10-06 §3.2/§3.6)."""

PAYLOAD_VERSION = 1

ARTIFACT_ASR = 'asr_transcribe'
ARTIFACT_MT = 'mt_translate'
ARTIFACT_TTS = 'tts_synthesize'

WORKER_TYPE_AUDIO = 'audio'
WORKER_TYPE_MT = 'mt'

CAP_TRANSCRIBE = 'speech_transcribe'
CAP_TRANSLATE = 'speech_translate'
CAP_SYNTHESIZE = 'speech_synthesize'
CAP_GC = 'speech_gc_blobs'

ROLE_INPUT = 'input'
ROLE_OUTPUT = 'output'

# What rt-asr can decode (soundfile in src/services/common/audio.decode_audio_file).
ALLOWED_INPUT_CONTENT_TYPES = frozenset({'audio/wav', 'audio/x-wav', 'audio/flac', 'audio/ogg', 'audio/mpeg'})
ALLOWED_OUTPUT_CONTENT_TYPES = frozenset({'audio/wav'})
EXTENSIONS = {'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/flac': 'flac', 'audio/ogg': 'ogg', 'audio/mpeg': 'mp3'}

SPEECH_JOB_MAX_RETRIES = 2
MT_TIMEOUT_SECONDS = 300
```

```python
# speech/storage.py
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class SpeechStorage(FileSystemStorage):
    """FileSystemStorage rooted at settings.SPEECH_STORAGE_ROOT, resolved on
    every access (not cached at import) so deployments and tests can move it.
    Deliberately NOT under MEDIA_ROOT/static: blobs are served only through
    speech.views.blob_content with worker/staff auth."""

    @property
    def base_location(self):
        return str(settings.SPEECH_STORAGE_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)
```

```python
# speech/models.py
import uuid

from django.db import models
from django.utils import timezone

from speech.constants import EXTENSIONS, ROLE_INPUT, ROLE_OUTPUT
from speech.storage import SpeechStorage


def _upload_to(instance, filename):
    ext = EXTENSIONS.get(instance.content_type, 'bin')
    return f'speech/{timezone.now():%Y/%m/%d}/{instance.uuid}.{ext}'


class AudioBlob(models.Model):
    """An audio file passed to/from rt-speech workers BY REFERENCE (never
    base64 in WorkItem.prompt) — spec 2026-10-06 django-engine-integration
    §3.6. Cross-app links are UUID/pk, not FK (app-structure rule 3)."""

    class Role(models.TextChoices):
        INPUT = ROLE_INPUT, 'Input'
        OUTPUT = ROLE_OUTPUT, 'Output'

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    file = models.FileField(storage=SpeechStorage(), upload_to=_upload_to, max_length=255)
    role = models.CharField(max_length=10, choices=Role.choices)
    content_type = models.CharField(max_length=100)
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    duration_seconds = models.FloatField(null=True, blank=True)
    work_item_uuid = models.UUIDField(null=True, blank=True, db_index=True,
                                      help_text='Producing WorkItem (role=output).')
    uploaded_by_service_id = models.IntegerField(null=True, blank=True,
                                                 help_text='llm_services.LLMService pk of the uploading worker.')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['role', 'expires_at'])]

    def __str__(self):
        return f'AudioBlob {self.uuid} ({self.role}, {self.content_type}, {self.size_bytes} B)'

    def is_expired(self, now=None) -> bool:
        return self.expires_at <= (now or timezone.now())
```

```python
# speech/admin.py
from django.contrib import admin

from speech.models import AudioBlob


@admin.register(AudioBlob)
class AudioBlobAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'role', 'content_type', 'size_bytes', 'duration_seconds', 'work_item_uuid', 'expires_at')
    list_filter = ('role', 'content_type')
    readonly_fields = ('uuid', 'sha256', 'size_bytes', 'created_at')
```

- [ ] **Step 4: Blob service**

```python
# speech/services/blobs.py
"""AudioBlob storage service — the single writer of AudioBlob rows/files."""
import hashlib
import logging
import uuid as uuid_lib
import wave
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile, File
from django.utils import timezone

from speech.constants import (
    ALLOWED_INPUT_CONTENT_TYPES, ALLOWED_OUTPUT_CONTENT_TYPES, EXTENSIONS, ROLE_INPUT, ROLE_OUTPUT,
)
from speech.models import AudioBlob

logger = logging.getLogger(__name__)


class BlobError(Exception):
    """Explicit, coded blob failure (no silent fallback)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def wav_duration(fileobj, content_type: str) -> float | None:
    """Duration for WAV via stdlib `wave`; None for other formats or unreadable headers."""
    if content_type not in ('audio/wav', 'audio/x-wav'):
        return None
    try:
        with wave.open(fileobj, 'rb') as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


def store_blob(source, *, content_type: str, role: str, work_item_uuid=None,
               uploaded_by_service_id: int | None = None) -> AudioBlob:
    if role == ROLE_INPUT:
        allowed, limit = ALLOWED_INPUT_CONTENT_TYPES, settings.SPEECH_MAX_INPUT_BYTES
    elif role == ROLE_OUTPUT:
        allowed, limit = ALLOWED_OUTPUT_CONTENT_TYPES, settings.SPEECH_MAX_OUTPUT_BYTES
    else:
        raise BlobError('invalid_role', f'unknown blob role {role!r}')
    if content_type not in allowed:
        raise BlobError('unsupported_content_type', f'{content_type!r} not allowed for {role} blobs')

    f = source if isinstance(source, File) else ContentFile(source)
    size = f.size
    if not size:
        raise BlobError('empty', 'empty audio file')
    if size > limit:
        raise BlobError('too_large', f'{size} bytes exceeds the {role} limit of {limit} bytes')

    digest = hashlib.sha256()
    f.seek(0)
    for chunk in f.chunks():
        digest.update(chunk)
    f.seek(0)
    duration = wav_duration(f, content_type)
    f.seek(0)

    blob = AudioBlob(
        role=role, content_type=content_type, size_bytes=size, sha256=digest.hexdigest(),
        duration_seconds=duration, work_item_uuid=work_item_uuid,
        uploaded_by_service_id=uploaded_by_service_id,
        expires_at=timezone.now() + timedelta(days=settings.SPEECH_BLOB_TTL_DAYS),
    )
    blob.file.save(f'{blob.uuid}.{EXTENSIONS.get(content_type, "bin")}', f, save=False)
    blob.save()
    logger.info('store_blob: %s', blob)
    return blob


def get_live_blob(blob_id) -> AudioBlob:
    try:
        blob_uuid = uuid_lib.UUID(str(blob_id))
    except ValueError:
        raise BlobError('not_found', f'audio blob {blob_id!r} is not a valid id')
    blob = AudioBlob.objects.filter(uuid=blob_uuid).first()
    if blob is None:
        raise BlobError('not_found', f'audio blob {blob_id} does not exist')
    if blob.is_expired():
        raise BlobError('expired', f'audio blob {blob_id} expired at {blob.expires_at.isoformat()}')
    return blob


def gc_expired_blobs(now=None) -> int:
    """Delete expired blobs (file + row). Returns the number deleted."""
    now = now or timezone.now()
    deleted = 0
    for blob in AudioBlob.objects.filter(expires_at__lt=now).iterator():
        blob.file.delete(save=False)
        blob.delete()
        deleted += 1
    logger.info('gc_expired_blobs: deleted %s blobs', deleted)
    return deleted
```

- [ ] **Step 5: Migration**

Run: `DJMANAGE makemigrations speech` → створює `speech/migrations/0001_initial.py` з `CreateModel('AudioBlob', …)` і `storage=speech.storage.SpeechStorage()`. Переглянь файл: поля збігаються з моделлю, жодних інших app у `dependencies`.

- [ ] **Step 6: Run tests**

Run: `DJTEST speech/tests/test_blobs.py -v` → PASS.
Run: `nix develop ./flake --command bash -c "cd django_api && ruff check speech/"` → без помилок.

- [ ] **Step 7: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/speech/ django_api/knowledge_base_api/settings.py .gitignore
git -C ~/repos/swarm-nix commit -m "speech: new app with AudioBlob storage service"
```

### Task A9: blob views (download/upload) + ACL до WorkItem

**Files:**
- Create: `speech/handlers/__init__.py` (порожній), `speech/handlers/engine_acl.py`, `speech/views.py`, `speech/urls.py`
- Modify: `speech/services/blobs.py` (path helpers), `knowledge_base_api/urls.py` (після рядка `api/llm/`)
- Modify: `speech/tests/conftest.py` (фікстури воркера)
- Test: `speech/tests/test_blob_views.py`

**Interfaces:**
- Consumes: `store_blob`, `AudioBlob` (A8); `llm_services.authentication.LLMServiceTokenAuthentication`, `resolve_calling_service`.
- Produces: `GET /api/speech/blobs/<uuid>/content/?work_item=<uuid>` (name `speech:blob-content`), `POST /api/speech/blobs/?work_item=<uuid>` (name `speech:blob-upload`, multipart `file` → 201 `{"blob_id","size_bytes","sha256","content_type"}`); `blob_content_path(blob, work_item_uuid) -> str`, `blob_upload_path(work_item_uuid) -> str`; `engine_acl.work_item_grants_blob(work_item_uuid, blob) -> bool`, `engine_acl.work_item_accepts_output(work_item_uuid) -> bool`; фікстура `worker_client` (APIClient з Bearer-токеном `LLMService(kind='tts')`, атрибут `.service`).

- [ ] **Step 1: Fixtures + failing tests**

Append to `speech/tests/conftest.py`:

```python
import uuid as uuid_lib

from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient


@pytest.fixture
def worker_client(db):
    from llm_services.models import LLMService

    name = f'zzq-host-rt-{uuid_lib.uuid4().hex[:8]}'
    user = User.objects.create_user(name)
    token = Token.objects.create(user=user)
    service = LLMService.objects.create(name=name, base_url='http://127.0.0.1:8093', token=token.key, kind='tts')
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.key}')
    client.service = service
    return client
```

```python
# speech/tests/test_blob_views.py
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from engine.models import WorkItem
from speech.models import AudioBlob
from speech.services.blobs import blob_content_path, blob_upload_path, store_blob


def _wi(status='in_progress', artifact_type='asr_transcribe', blob_ids=()):
    return WorkItem.objects.create(
        worker_type='audio', artifact_type=artifact_type, prompt='{"v": 1}', expected_format='json',
        status=status, metadata={'is_speech_job': True, 'speech_blob_ids': [str(b) for b in blob_ids]},
    )


@pytest.mark.django_db
class TestDownload:
    def test_worker_downloads_granted_blob(self, speech_storage_root, wav, worker_client):
        data = wav()
        blob = store_blob(data, content_type='audio/wav', role='input')
        wi = _wi(blob_ids=[blob.uuid])
        resp = worker_client.get(blob_content_path(blob, wi.uuid))
        assert resp.status_code == 200
        assert b''.join(resp.streaming_content) == data
        assert resp['X-Content-SHA256'] == blob.sha256
        assert resp['Content-Type'] == 'audio/wav'

    @pytest.mark.parametrize('case', ['not_referenced', 'not_in_progress', 'no_work_item'])
    def test_worker_denied_outside_grant(self, speech_storage_root, wav, worker_client, case):
        blob = store_blob(wav(), content_type='audio/wav', role='input')
        if case == 'not_referenced':
            url = blob_content_path(blob, _wi(blob_ids=[]).uuid)
        elif case == 'not_in_progress':
            url = blob_content_path(blob, _wi(status='todo', blob_ids=[blob.uuid]).uuid)
        else:
            url = reverse('speech:blob-content', kwargs={'blob_uuid': blob.uuid})
        assert worker_client.get(url).status_code == 403

    def test_staff_session_downloads_without_work_item(self, speech_storage_root, wav):
        blob = store_blob(wav(), content_type='audio/wav', role='input')
        client = APIClient()
        client.force_login(User.objects.create_user('zzq-staff-dl', is_staff=True))
        assert client.get(reverse('speech:blob-content', kwargs={'blob_uuid': blob.uuid})).status_code == 200

    def test_non_staff_session_denied(self, speech_storage_root, wav):
        blob = store_blob(wav(), content_type='audio/wav', role='input')
        client = APIClient()
        client.force_login(User.objects.create_user('zzq-plain-dl'))
        assert client.get(reverse('speech:blob-content', kwargs={'blob_uuid': blob.uuid})).status_code == 403

    def test_unknown_and_expired(self, speech_storage_root, wav, worker_client):
        import uuid
        assert worker_client.get(reverse('speech:blob-content', kwargs={'blob_uuid': uuid.uuid4()})).status_code == 404
        blob = store_blob(wav(), content_type='audio/wav', role='input')
        wi = _wi(blob_ids=[blob.uuid])
        AudioBlob.objects.filter(pk=blob.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        assert worker_client.get(blob_content_path(blob, wi.uuid)).status_code == 410


@pytest.mark.django_db
class TestUpload:
    def _upload(self, client, wi_uuid, data, content_type='audio/wav'):
        return client.post(blob_upload_path(wi_uuid), {'file': SimpleUploadedFile('out.wav', data, content_type=content_type)}, format='multipart')

    def test_worker_uploads_tts_output(self, speech_storage_root, wav, worker_client):
        wi = _wi(artifact_type='tts_synthesize')
        data = wav(seconds=0.5, rate=24000)
        resp = self._upload(worker_client, wi.uuid, data)
        assert resp.status_code == 201
        blob = AudioBlob.objects.get(uuid=resp.json()['blob_id'])
        assert (blob.role, blob.work_item_uuid, blob.uploaded_by_service_id) == ('output', wi.uuid, worker_client.service.pk)
        assert resp.json()['size_bytes'] == len(data)

    def test_rejects_non_tts_or_not_in_progress(self, speech_storage_root, wav, worker_client):
        assert self._upload(worker_client, _wi(artifact_type='asr_transcribe').uuid, wav()).status_code == 403
        assert self._upload(worker_client, _wi(status='done', artifact_type='tts_synthesize').uuid, wav()).status_code == 403

    def test_rejects_too_large_and_wrong_type(self, speech_storage_root, wav, worker_client, settings):
        wi = _wi(artifact_type='tts_synthesize')
        assert self._upload(worker_client, wi.uuid, wav(), content_type='audio/flac').status_code == 400
        settings.SPEECH_MAX_OUTPUT_BYTES = 10
        assert self._upload(worker_client, wi.uuid, wav()).status_code == 413

    def test_session_user_cannot_upload(self, speech_storage_root, wav):
        client = APIClient()
        client.force_login(User.objects.create_user('zzq-staff-up', is_staff=True))
        assert self._upload(client, _wi(artifact_type='tts_synthesize').uuid, wav()).status_code in (401, 403)


def test_path_helpers_are_relative_api_paths():
    import uuid
    from types import SimpleNamespace
    blob, wi = SimpleNamespace(uuid=uuid.uuid4()), uuid.uuid4()
    assert blob_content_path(blob, wi) == f'/api/speech/blobs/{blob.uuid}/content/?work_item={wi}'
    assert blob_upload_path(wi) == f'/api/speech/blobs/?work_item={wi}'
```

- [ ] **Step 2: Run to verify failure**

Run: `DJTEST speech/tests/test_blob_views.py -v`
Expected: FAIL — `ImportError: cannot import name 'blob_content_path'`.

- [ ] **Step 3: Path helpers** (append to `speech/services/blobs.py`; add `from urllib.parse import urlencode` and `from django.urls import reverse` to imports)

```python
def blob_content_path(blob, work_item_uuid) -> str:
    """Relative download path a worker resolves against the Django endpoint
    it polled (spec §3.2 — never an absolute URL)."""
    path = reverse('speech:blob-content', kwargs={'blob_uuid': blob.uuid})
    return f'{path}?{urlencode({"work_item": str(work_item_uuid)})}'


def blob_upload_path(work_item_uuid) -> str:
    return f'{reverse("speech:blob-upload")}?{urlencode({"work_item": str(work_item_uuid)})}'
```

- [ ] **Step 4: Engine ACL**

```python
# speech/handlers/engine_acl.py
"""The ONLY place `speech` reads engine.WorkItem for access control
(handlers/ is the allowed engine-import zone, app-structure rule 2)."""
import uuid as uuid_lib

from speech.constants import ARTIFACT_TTS


def _in_progress_work_item(work_item_uuid):
    from engine.models import WorkItem

    try:
        wi_uuid = uuid_lib.UUID(str(work_item_uuid))
    except ValueError:
        return None
    return WorkItem.objects.filter(uuid=wi_uuid, status='in_progress').first()


def work_item_grants_blob(work_item_uuid, blob) -> bool:
    """True iff an in_progress WorkItem lists this blob in metadata.speech_blob_ids."""
    wi = _in_progress_work_item(work_item_uuid)
    return wi is not None and str(blob.uuid) in (wi.metadata or {}).get('speech_blob_ids', [])


def work_item_accepts_output(work_item_uuid) -> bool:
    """True iff an in_progress tts_synthesize WorkItem may receive an output blob."""
    wi = _in_progress_work_item(work_item_uuid)
    return wi is not None and wi.artifact_type == ARTIFACT_TTS
```

- [ ] **Step 5: Views + urls**

```python
# speech/views.py
"""Blob transport for rt-speech workers (spec 2026-10-06 §3.6)."""
import logging

from django.http import FileResponse
from llm_services.authentication import LLMServiceTokenAuthentication, resolve_calling_service
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from speech.constants import ROLE_OUTPUT
from speech.handlers.engine_acl import work_item_accepts_output, work_item_grants_blob
from speech.models import AudioBlob
from speech.services.blobs import BlobError, store_blob

logger = logging.getLogger(__name__)


def _error(message: str, code: str, http_status: int) -> Response:
    return Response({'error': message, 'code': code, 'details': {}}, status=http_status)


@api_view(['GET'])
@authentication_classes([LLMServiceTokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def blob_content(request, blob_uuid):
    """Worker: only for an in_progress WorkItem that references the blob.
    Staff session: any blob (debugging / fetching TTS output)."""
    blob = AudioBlob.objects.filter(uuid=blob_uuid).first()
    if blob is None:
        return _error('audio blob not found', 'not_found', status.HTTP_404_NOT_FOUND)
    service = resolve_calling_service(request)
    if service is None:
        if not request.user.is_staff:
            return _error('staff or worker token required', 'forbidden', status.HTTP_403_FORBIDDEN)
    elif not work_item_grants_blob(request.query_params.get('work_item'), blob):
        return _error('blob not granted to this work item', 'forbidden', status.HTTP_403_FORBIDDEN)
    if blob.is_expired():
        return _error('audio blob expired', 'expired', status.HTTP_410_GONE)
    resp = FileResponse(blob.file.open('rb'), content_type=blob.content_type)
    resp['Content-Length'] = str(blob.size_bytes)
    resp['X-Content-SHA256'] = blob.sha256
    return resp


@api_view(['POST'])
@authentication_classes([LLMServiceTokenAuthentication])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def blob_upload(request):
    """Worker-only: TTS output for an in_progress tts_synthesize WorkItem."""
    service = resolve_calling_service(request)
    if service is None:
        return _error('worker token required', 'worker_only', status.HTTP_403_FORBIDDEN)
    wi_uuid = request.query_params.get('work_item')
    if not work_item_accepts_output(wi_uuid):
        return _error('work item does not accept output', 'work_item_not_accepting_output', status.HTTP_403_FORBIDDEN)
    upload = request.FILES.get('file')
    if upload is None:
        return _error("multipart field 'file' is required", 'missing_file', status.HTTP_400_BAD_REQUEST)
    try:
        blob = store_blob(upload, content_type=upload.content_type, role=ROLE_OUTPUT,
                          work_item_uuid=wi_uuid, uploaded_by_service_id=service.pk)
    except BlobError as exc:
        http_status = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if exc.code == 'too_large' else status.HTTP_400_BAD_REQUEST
        return _error(str(exc), exc.code, http_status)
    return Response(
        {'blob_id': str(blob.uuid), 'size_bytes': blob.size_bytes, 'sha256': blob.sha256, 'content_type': blob.content_type},
        status=status.HTTP_201_CREATED,
    )
```

```python
# speech/urls.py
from django.urls import path

from speech import views

app_name = 'speech'

urlpatterns = [
    path('blobs/', views.blob_upload, name='blob-upload'),
    path('blobs/<uuid:blob_uuid>/content/', views.blob_content, name='blob-content'),
]
```

`knowledge_base_api/urls.py` — після `path('api/llm/', include('llm_services.urls')),`:

```python
    # Speech services — AudioBlob transport for rt-asr/rt-tts workers (spec 2026-10-06)
    path('api/speech/', include('speech.urls', namespace='speech')),
```

- [ ] **Step 6: Run tests**

Run: `DJTEST speech/tests/ -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/speech/ django_api/knowledge_base_api/urls.py
git -C ~/repos/swarm-nix commit -m "speech: worker-scoped blob download and TTS output upload endpoints"
```

### Task A10: payload builders

**Files:**
- Create: `speech/services/payloads.py`
- Test: `speech/tests/test_payloads.py`

**Interfaces:**
- Consumes: `get_live_blob`, `blob_content_path`, `blob_upload_path`, `BlobError` (A8/A9); constants.
- Produces: `SpeechInputError(Exception)`; `SpeechInputs(run_context: dict, condition: dict, parent_result: dict)` з `.merged`; `JobRequest(payload: dict, metadata: dict, timeout_seconds: int)`; `split_sentences(text) -> list[str]`; `cap_item(text, limit) -> list[str]`; `asr_timeout(duration_s) -> int`; `build_asr_job(inputs, work_item_uuid) -> JobRequest`; `build_mt_job(...)`; `build_tts_job(...)`; `resolve_tts_text(inputs) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# speech/tests/test_payloads.py
import json

import pytest

from speech.services.blobs import store_blob
from speech.services.payloads import (
    SpeechInputError, SpeechInputs, asr_timeout, build_asr_job, build_mt_job, build_tts_job,
    cap_item, split_sentences,
)

WI = '11111111-2222-3333-4444-555555555555'


def _inputs(run=None, cond=None, parent=None):
    return SpeechInputs(run or {}, cond or {}, parent or {})


def test_split_sentences_and_cap_item():
    assert split_sentences('Привіт. Як справи? Добре!') == ['Привіт.', 'Як справи?', 'Добре!']
    assert split_sentences('  ') == []
    assert cap_item('aaa bbb ccc', 7) == ['aaa bbb', 'ccc']
    assert cap_item('x' * 10, 4) == ['xxxx', 'xxxx', 'xx']
    assert cap_item('', 5) == []


def test_asr_timeout():
    assert asr_timeout(None) == 1800
    assert asr_timeout(10.0) == 300
    assert asr_timeout(600.0) == 1020
    assert asr_timeout(10000.0) == 3600


@pytest.mark.django_db
class TestAsr:
    def test_builds_relative_audio_ref(self, speech_storage_root, wav):
        blob = store_blob(wav(seconds=60.0), content_type='audio/wav', role='input')
        job = build_asr_job(_inputs(run={'audio_blob_id': str(blob.uuid), 'language': 'uk'}), WI)
        assert job.payload == {
            'v': 1,
            'audio': {
                'path': f'/api/speech/blobs/{blob.uuid}/content/?work_item={WI}',
                'content_type': 'audio/wav', 'size_bytes': blob.size_bytes,
                'sha256': blob.sha256, 'duration_s': pytest.approx(60.0),
            },
            'language': 'uk', 'initial_prompt': None, 'temperature': 0.0,
        }
        assert job.metadata == {'speech_blob_ids': [str(blob.uuid)]}
        assert job.timeout_seconds == 300
        json.dumps(job.payload)

    def test_missing_or_bad_blob(self, speech_storage_root, wav):
        with pytest.raises(SpeechInputError, match='audio_blob_id'):
            build_asr_job(_inputs(), WI)
        out = store_blob(wav(), content_type='audio/wav', role='output')
        with pytest.raises(SpeechInputError, match='input'):
            build_asr_job(_inputs(cond={'audio_blob_id': str(out.uuid)}), WI)
        with pytest.raises(SpeechInputError, match='not_found|does not exist|valid id'):
            build_asr_job(_inputs(cond={'audio_blob_id': 'nope'}), WI)


class TestMt:
    def test_prefers_parent_segments_and_detected_language(self):
        parent = {'text': 'Привіт. Як справи?', 'language': 'uk',
                  'segments': [{'start': 0, 'end': 1, 'text': ' Привіт. '}, {'start': 1, 'end': 2, 'text': 'Як справи?'}, {'text': '  '}]}
        job = build_mt_job(_inputs(run={'target_lang': 'en', 'language': 'pl'}, parent=parent), WI)
        assert job.payload == {'v': 1, 'texts': ['Привіт.', 'Як справи?'], 'source_lang': 'uk', 'target_lang': 'en', 'num_beams': None}
        assert job.timeout_seconds == 300

    def test_explicit_text_is_sentence_split_and_capped(self, settings):
        settings.SPEECH_MT_MAX_ITEM_CHARS = 10
        job = build_mt_job(_inputs(cond={'text': 'один два три. чотири', 'source_lang': 'uk', 'target_lang': 'en'}), WI)
        assert job.payload['texts'] == ['один два', 'три.', 'чотири']

    def test_errors(self, settings):
        with pytest.raises(SpeechInputError, match='target_lang'):
            build_mt_job(_inputs(cond={'text': 'a', 'source_lang': 'uk'}), WI)
        with pytest.raises(SpeechInputError, match='source_lang'):
            build_mt_job(_inputs(cond={'text': 'a', 'target_lang': 'en'}), WI)
        with pytest.raises(SpeechInputError, match='nothing to translate'):
            build_mt_job(_inputs(cond={'text': '  ', 'source_lang': 'uk', 'target_lang': 'en'}), WI)
        settings.SPEECH_MT_MAX_CHARS = 5
        with pytest.raises(SpeechInputError, match='SPEECH_MT_MAX_CHARS'):
            build_mt_job(_inputs(cond={'text': 'abcdef', 'source_lang': 'uk', 'target_lang': 'en'}), WI)


class TestTts:
    def test_joins_parent_translations_and_sets_upload(self, settings):
        job = build_tts_job(_inputs(run={'target_lang': 'en'}, parent={'translations': ['Hello.', 'How are you?'], 'target_lang': 'en'}), WI)
        assert job.payload == {
            'v': 1, 'text': 'Hello. How are you?', 'lang': 'en', 'voice': None, 'speed': 1.0,
            'format': 'wav', 'sample_rate': 24000,
            'output': {'upload_path': f'/api/speech/blobs/?work_item={WI}', 'max_bytes': settings.SPEECH_MAX_OUTPUT_BYTES},
        }
        assert job.timeout_seconds == 300

    def test_text_source_order(self):
        assert build_tts_job(_inputs(cond={'text': 'cond', 'lang': 'en'}, parent={'translations': ['p']}), WI).payload['text'] == 'cond'
        assert build_tts_job(_inputs(parent={'text': 'asr text', 'language': 'uk'}), WI).payload['text'] == 'asr text'
        assert build_tts_job(_inputs(run={'text': 'run text', 'lang': 'en'}), WI).payload['text'] == 'run text'

    def test_errors(self, settings):
        with pytest.raises(SpeechInputError, match='no text'):
            build_tts_job(_inputs(run={'lang': 'en'}), WI)
        with pytest.raises(SpeechInputError, match='lang'):
            build_tts_job(_inputs(cond={'text': 'x'}), WI)
        settings.SPEECH_TTS_MAX_CHARS = 3
        with pytest.raises(SpeechInputError, match='SPEECH_TTS_MAX_CHARS'):
            build_tts_job(_inputs(cond={'text': 'abcd', 'lang': 'en'}), WI)
```

- [ ] **Step 2: Run to verify failure**

Run: `DJTEST speech/tests/test_payloads.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speech.services.payloads'`.

- [ ] **Step 3: Implement**

```python
# speech/services/payloads.py
"""Build rt-speech WorkItem payloads (wire contract, spec 2026-10-06 §3.2/§3.6).

Pure functions over three explicit input sources — the run's context, the
step's condition, the previous step's result — so every "where did this
value come from" rule is visible and tested (no silent defaults: missing or
oversized input raises SpeechInputError)."""
import math
import re
from dataclasses import dataclass

from django.conf import settings

from speech.constants import ALLOWED_INPUT_CONTENT_TYPES, MT_TIMEOUT_SECONDS, PAYLOAD_VERSION, ROLE_INPUT
from speech.services.blobs import BlobError, blob_content_path, blob_upload_path, get_live_blob


class SpeechInputError(Exception):
    """A speech step cannot build its payload — explicit, terminal for the step."""


@dataclass(frozen=True)
class SpeechInputs:
    run_context: dict
    condition: dict
    parent_result: dict

    @property
    def merged(self) -> dict:
        return {**self.run_context, **self.condition, **self.parent_result}


@dataclass(frozen=True)
class JobRequest:
    payload: dict
    metadata: dict
    timeout_seconds: int


_SENTENCE_RE = re.compile(r'(?<=[.!?…])\s+')


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_RE.split(text or '') if part.strip()]


def cap_item(text: str, limit: int) -> list[str]:
    """Split `text` into chunks ≤ limit on whitespace; hard-cut an overlong token."""
    text = (text or '').strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ''
    for word in text.split():
        while len(word) > limit:
            if current:
                chunks.append(current)
                current = ''
            chunks.append(word[:limit])
            word = word[limit:]
        if not word:
            continue
        candidate = f'{current} {word}' if current else word
        if len(candidate) > limit:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def asr_timeout(duration_s: float | None) -> int:
    if duration_s is None:
        return settings.SPEECH_ASR_DEFAULT_TIMEOUT
    return _clamp(math.ceil(duration_s * 1.5) + 120, 300, 3600)


def build_asr_job(inputs: SpeechInputs, work_item_uuid) -> JobRequest:
    merged = inputs.merged
    blob_id = merged.get('audio_blob_id')
    if not blob_id:
        raise SpeechInputError("speech_transcribe: 'audio_blob_id' is required (AnalysisRun.context or step.condition)")
    try:
        blob = get_live_blob(blob_id)
    except BlobError as exc:
        raise SpeechInputError(f'speech_transcribe: {exc.code}: {exc}') from exc
    if blob.role != ROLE_INPUT:
        raise SpeechInputError(f'speech_transcribe: blob {blob.uuid} is {blob.role}, expected an input blob')
    if blob.content_type not in ALLOWED_INPUT_CONTENT_TYPES:
        raise SpeechInputError(f'speech_transcribe: unsupported content type {blob.content_type!r}')
    payload = {
        'v': PAYLOAD_VERSION,
        'audio': {
            'path': blob_content_path(blob, work_item_uuid),
            'content_type': blob.content_type,
            'size_bytes': blob.size_bytes,
            'sha256': blob.sha256,
            'duration_s': blob.duration_seconds,
        },
        'language': merged.get('language') or None,
        'initial_prompt': merged.get('initial_prompt') or None,
        'temperature': float(merged.get('temperature', 0.0)),
    }
    return JobRequest(payload, {'speech_blob_ids': [str(blob.uuid)]}, asr_timeout(blob.duration_seconds))


def _mt_texts(inputs: SpeechInputs) -> list[str]:
    merged = inputs.merged
    texts = merged.get('texts')
    if texts is None:
        segments = inputs.parent_result.get('segments') or []
        texts = [s['text'].strip() for s in segments
                 if isinstance(s, dict) and isinstance(s.get('text'), str) and s['text'].strip()]
        if not texts:
            texts = split_sentences(merged.get('text') or '')
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise SpeechInputError("speech_translate: 'texts' must be a list of strings")
    return [chunk for text in texts for chunk in cap_item(text, settings.SPEECH_MT_MAX_ITEM_CHARS)]


def build_mt_job(inputs: SpeechInputs, work_item_uuid) -> JobRequest:
    merged = inputs.merged
    target = merged.get('target_lang')
    if not target:
        raise SpeechInputError("speech_translate: 'target_lang' is required")
    source = merged.get('source_lang') or merged.get('language')
    if not source:
        raise SpeechInputError("speech_translate: 'source_lang' unknown (no source_lang and no detected language)")
    items = _mt_texts(inputs)
    if not items:
        raise SpeechInputError('speech_translate: nothing to translate')
    total = sum(len(item) for item in items)
    if total > settings.SPEECH_MT_MAX_CHARS:
        raise SpeechInputError(f'speech_translate: {total} chars exceeds SPEECH_MT_MAX_CHARS={settings.SPEECH_MT_MAX_CHARS}')
    payload = {'v': PAYLOAD_VERSION, 'texts': items, 'source_lang': source, 'target_lang': target,
               'num_beams': merged.get('num_beams')}
    return JobRequest(payload, {'speech_blob_ids': []}, MT_TIMEOUT_SECONDS)


def resolve_tts_text(inputs: SpeechInputs) -> str:
    """condition.text → parent translations → parent text → run_context.text (spec §3.6)."""
    if isinstance(inputs.condition.get('text'), str) and inputs.condition['text'].strip():
        return inputs.condition['text'].strip()
    translations = inputs.parent_result.get('translations')
    if isinstance(translations, list):
        joined = ' '.join(t.strip() for t in translations if isinstance(t, str) and t.strip())
        if joined:
            return joined
    for source in (inputs.parent_result, inputs.run_context):
        if isinstance(source.get('text'), str) and source['text'].strip():
            return source['text'].strip()
    raise SpeechInputError('speech_synthesize: no text (condition.text, translations, text) to synthesize')


def build_tts_job(inputs: SpeechInputs, work_item_uuid) -> JobRequest:
    text = resolve_tts_text(inputs)
    if len(text) > settings.SPEECH_TTS_MAX_CHARS:
        raise SpeechInputError(f'speech_synthesize: {len(text)} chars exceeds SPEECH_TTS_MAX_CHARS={settings.SPEECH_TTS_MAX_CHARS}')
    merged = inputs.merged
    lang = merged.get('target_lang') or merged.get('lang') or merged.get('language')
    if not lang:
        raise SpeechInputError("speech_synthesize: 'lang' unknown (no target_lang/lang/language)")
    payload = {
        'v': PAYLOAD_VERSION,
        'text': text,
        'lang': lang,
        'voice': merged.get('voice') or None,
        'speed': float(merged.get('speed', 1.0)),
        'format': 'wav',
        'sample_rate': int(merged.get('sample_rate', 24000)),
        'output': {'upload_path': blob_upload_path(work_item_uuid), 'max_bytes': settings.SPEECH_MAX_OUTPUT_BYTES},
    }
    return JobRequest(payload, {'speech_blob_ids': []}, _clamp(len(text) // 10 + 120, 300, 1800))
```

- [ ] **Step 4: Run tests**

Run: `DJTEST speech/tests/test_payloads.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/speech/services/payloads.py django_api/speech/tests/test_payloads.py
git -C ~/repos/swarm-nix commit -m "speech: wire-contract payload builders for asr/mt/tts jobs"
```

### Task A11: step handler, validators, `SpeechPlugin`

**Files:**
- Create: `speech/handlers/speech_steps.py`, `speech/services/output_validators.py`, `speech/services/capability_validators.py`, `speech/services/capability_input_validators.py`, `speech/plugin.py`
- Modify: `speech/apps.py` (`ready()`)
- Test: `speech/tests/test_speech_step_handler.py`, `speech/tests/test_validators.py`

**Interfaces:**
- Consumes: payload builders (A10); `engine.services.workitem_factory.create_workitem`, `create_deferred_parent_workitem`; `engine.services.capability_input.resolve_manual_field_mapping`; `engine.capability_docs.capability_usage`, `usage_of`; `engine.plugin_api.EnginePlugin`, `PluginRegistry`.
- Produces: `handle_speech_step(step, session_id, workflow_id, parent_task) -> dict` (`{'_deferred': True, 'parent_work_item_id': int}`); `handle_speech_gc_blobs(...) -> {'deleted': int}`; `collect_inputs(step, session_id, parent_task) -> SpeechInputs`; `SPEECH_JOBS: dict[str, JobSpec]`; validators `validate_speech_step(result, step, parent_task)`, `validate_input_speech(data) -> list[str]`, `validate_asr_result/validate_mt_result/validate_tts_result(result, step, task)`; `SpeechPlugin` (name `'speech'`).

- [ ] **Step 1: Write the failing tests**

```python
# speech/tests/test_speech_step_handler.py
import json
import uuid

import pytest

from engine.models import AnalysisRun, Workflow, WorkflowStep, WorkItem
from speech.handlers.speech_steps import handle_speech_step
from speech.services.blobs import store_blob
from speech.services.payloads import SpeechInputError


def _step(target, condition=None):
    wf = Workflow.objects.create(name=f'zzq_speech_wf_{uuid.uuid4().hex[:8]}', workflow_kind='execution')
    step = WorkflowStep.objects.create(
        workflow=wf, target=target, order=1, step_type='transform',
        condition={'format': 'json', 'priority': 4, 'worker_type': 'internal', 'execution_mode': 'internal', **(condition or {})},
    )
    return wf, step


@pytest.mark.django_db
class TestHandleSpeechStep:
    def test_transcribe_creates_child_and_waiting_parent(self, speech_storage_root, wav):
        blob = store_blob(wav(seconds=10.0), content_type='audio/wav', role='input')
        wf, step = _step('speech_transcribe')
        run = AnalysisRun.objects.create(workflow=wf, blueprint_name=wf.name, context={'audio_blob_id': str(blob.uuid), 'language': 'uk'})

        out = handle_speech_step(step, run.pk, str(wf.uuid), None)

        assert out['_deferred'] is True
        parent = WorkItem.objects.get(pk=out['parent_work_item_id'])
        child = WorkItem.objects.get(uuid=parent.metadata['waiting_on_workitem_uuid'])
        assert (parent.status, parent.worker_type, parent.artifact_type) == ('in_progress', 'internal', 'speech_transcribe')
        assert parent.metadata['internal'] is True
        assert parent.metadata['session_id'] == run.pk
        assert parent.metadata['blueprint_name'] == wf.name
        assert (child.status, child.worker_type, child.artifact_type, child.expected_format) == ('todo', 'audio', 'asr_transcribe', 'json')
        assert (child.max_retries, child.timeout_seconds, child.priority) == (2, 300, 4)
        assert not {'session_id', 'blueprint_name', 'workflow_id'} & set(child.metadata)
        assert child.metadata['speech_blob_ids'] == [str(blob.uuid)]
        payload = json.loads(child.prompt)
        assert payload['audio']['path'] == f'/api/speech/blobs/{blob.uuid}/content/?work_item={child.uuid}'
        assert payload['language'] == 'uk'

    def test_translate_reads_previous_step_result(self, speech_storage_root):
        wf, step = _step('speech_translate', {'target_lang': 'en'})
        run = AnalysisRun.objects.create(workflow=wf, blueprint_name=wf.name, context={})
        prev = WorkItem.objects.create(
            worker_type='internal', artifact_type='speech_transcribe', prompt='(p)', status='done',
            parsed_result={'text': 'Привіт. Як справи?', 'language': 'uk', 'segments': []},
            metadata={'session_id': run.pk, 'blueprint_name': wf.name},
        )
        out = handle_speech_step(step, run.pk, str(wf.uuid), prev)
        parent = WorkItem.objects.get(pk=out['parent_work_item_id'])
        child = WorkItem.objects.get(uuid=parent.metadata['waiting_on_workitem_uuid'])
        assert (child.worker_type, child.artifact_type) == ('mt', 'mt_translate')
        assert json.loads(child.prompt)['texts'] == ['Привіт.', 'Як справи?']

    def test_input_error_raises_and_creates_nothing(self, speech_storage_root):
        wf, step = _step('speech_transcribe')
        run = AnalysisRun.objects.create(workflow=wf, blueprint_name=wf.name, context={})
        before = WorkItem.objects.filter(metadata__is_speech_job=True).count()
        with pytest.raises(SpeechInputError, match='audio_blob_id'):
            handle_speech_step(step, run.pk, str(wf.uuid), None)
        assert WorkItem.objects.filter(metadata__is_speech_job=True).count() == before
        assert not WorkItem.objects.filter(metadata__session_id=run.pk).exists()
```

```python
# speech/tests/test_validators.py
import json
import uuid

import pytest

from engine.models import WorkItem
from engine.plugin_api import PluginRegistry
from speech.handlers.speech_steps import handle_speech_step
from speech.services.blobs import store_blob
from speech.services.capability_input_validators import validate_input_speech
from speech.services.capability_validators import validate_speech_step
from speech.services.output_validators import validate_asr_result, validate_mt_result, validate_tts_result


def test_step_postcondition():
    assert validate_speech_step({'_deferred': True, 'parent_work_item_id': 1}, None, None)['valid'] is True
    verdict = validate_speech_step({'error': 'speech_transcribe: audio_blob_id is required', 'step': 'speech_transcribe'}, None, None)
    assert verdict == {'valid': False, 'reason': 'speech_transcribe: audio_blob_id is required'}


def test_input_types():
    assert validate_input_speech({'audio_blob_id': 'x', 'speed': 1, 'texts': ['a'], 'language': None}) == []
    problems = validate_input_speech({'audio_blob_id': 5, 'speed': 'fast', 'texts': 'a'})
    assert len(problems) == 3


def test_asr_result_shape():
    ok = {'v': 1, 'text': 't', 'language': None, 'duration_s': 1.0, 'segments': [], 'model': 'm'}
    assert validate_asr_result(ok, None, None)['valid'] is True
    assert validate_asr_result({**ok, 'v': 2}, None, None)['valid'] is False
    assert validate_asr_result({**ok, 'segments': 'x'}, None, None)['valid'] is False


def test_mt_result_count_must_match_payload():
    task = WorkItem(prompt=json.dumps({'v': 1, 'texts': ['a', 'b']}))
    ok = {'v': 1, 'translations': ['A', 'B'], 'source_lang': 'uk', 'target_lang': 'en', 'model': 'm'}
    assert validate_mt_result(ok, None, task)['valid'] is True
    assert validate_mt_result({**ok, 'translations': ['A']}, None, task)['valid'] is False


@pytest.mark.django_db
def test_tts_result_blob_must_belong_to_work_item(speech_storage_root, wav):
    task = WorkItem.objects.create(worker_type='audio', artifact_type='tts_synthesize', prompt='{}', status='in_progress')
    data = wav(seconds=0.2, rate=24000)
    own = store_blob(data, content_type='audio/wav', role='output', work_item_uuid=task.uuid)
    foreign = store_blob(data, content_type='audio/wav', role='output', work_item_uuid=uuid.uuid4())
    result = {'v': 1, 'audio': {'blob_id': str(own.uuid), 'content_type': 'audio/wav', 'size_bytes': own.size_bytes,
                                 'sha256': own.sha256, 'duration_s': 0.2, 'sample_rate': 24000}, 'voice': 'af_heart', 'model': 'm'}
    assert validate_tts_result(result, None, task)['valid'] is True
    bad = {**result, 'audio': {**result['audio'], 'blob_id': str(foreign.uuid)}}
    assert validate_tts_result(bad, None, task)['valid'] is False
    wrong_size = {**result, 'audio': {**result['audio'], 'size_bytes': 1}}
    assert validate_tts_result(wrong_size, None, task)['valid'] is False


def test_plugin_registration():
    for cap in ('speech_transcribe', 'speech_translate', 'speech_synthesize'):
        assert PluginRegistry.get_step_handler(cap) is handle_speech_step
        assert PluginRegistry.get_capability_validator(cap) is validate_speech_step
        assert PluginRegistry.get_input_validator(cap) is validate_input_speech
    assert PluginRegistry.get_step_handler('speech_gc_blobs') is not None
    assert PluginRegistry.get_output_validator('asr_transcribe') is validate_asr_result
    assert PluginRegistry.get_output_validator('mt_translate') is validate_mt_result
    assert PluginRegistry.get_output_validator('tts_synthesize') is validate_tts_result
```

- [ ] **Step 2: Run to verify failure**

Run: `DJTEST speech/tests/test_speech_step_handler.py speech/tests/test_validators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speech.handlers.speech_steps'`.

- [ ] **Step 3: Step handler**

```python
# speech/handlers/speech_steps.py
"""speech_* engine capabilities (spec 2026-10-06 django-engine-integration §3.6).

One handler for three capabilities (PPP: a variant = a registry entry). Each
call creates, in ONE transaction, the external WorkItem a dedicated rt-speech
worker claims via /next/?artifact_types=, plus an ADR-040 deferred parent that
waits on it (metadata.waiting_on_workitem_uuid → resolved by
engine.services.workitem_factory.resolve_deferred_parent_waiting_on)."""
import json
import logging
from dataclasses import dataclass
from typing import Callable

from django.db import transaction

from engine.capability_docs import capability_usage
from engine.constants import WorkerTypeName
from speech.constants import (
    ARTIFACT_ASR, ARTIFACT_MT, ARTIFACT_TTS, CAP_SYNTHESIZE, CAP_TRANSCRIBE, CAP_TRANSLATE,
    SPEECH_JOB_MAX_RETRIES, WORKER_TYPE_AUDIO, WORKER_TYPE_MT,
)
from speech.services.payloads import JobRequest, SpeechInputs, build_asr_job, build_mt_job, build_tts_job

logger = logging.getLogger(__name__)

# step.condition keys owned by the engine executor, not speech input.
_ENGINE_CONDITION_KEYS = frozenset({'format', 'priority', 'worker_type', 'execution_mode', 'output_spec', 'tier'})


@dataclass(frozen=True)
class JobSpec:
    artifact_type: str
    worker_type: str
    build: Callable[[SpeechInputs, object], JobRequest]


SPEECH_JOBS: dict[str, JobSpec] = {
    CAP_TRANSCRIBE: JobSpec(ARTIFACT_ASR, WORKER_TYPE_AUDIO, build_asr_job),
    CAP_TRANSLATE: JobSpec(ARTIFACT_MT, WORKER_TYPE_MT, build_mt_job),
    CAP_SYNTHESIZE: JobSpec(ARTIFACT_TTS, WORKER_TYPE_AUDIO, build_tts_job),
}


def collect_inputs(step, session_id, parent_task) -> SpeechInputs:
    from engine.models import AnalysisRun
    from engine.services.capability_input import resolve_manual_field_mapping

    run = AnalysisRun.objects.filter(pk=session_id).first() if session_id else None
    run_context = dict(run.context) if run is not None and isinstance(run.context, dict) else {}
    condition = {k: v for k, v in (step.condition or {}).items() if k not in _ENGINE_CONDITION_KEYS}
    mapping = condition.pop('field_mapping', None)
    if isinstance(mapping, dict):
        condition.update(resolve_manual_field_mapping(mapping, parent_task))
    parent_result = parent_task.parsed_result if parent_task is not None and isinstance(parent_task.parsed_result, dict) else {}
    return SpeechInputs(run_context, condition, dict(parent_result))


@capability_usage(
    instructions=(
        'Dispatch one rt-speech job (speech_transcribe → rt-asr, speech_translate → rt-mt, '
        'speech_synthesize → rt-tts) through the WorkItem queue and wait for it (deferred). '
        'Inputs come from AnalysisRun.context, step.condition (incl. field_mapping) and the '
        'previous step result: speech_transcribe needs audio_blob_id (+language?); '
        'speech_translate needs target_lang (source from source_lang or detected language; '
        'texts from previous segments/text); speech_synthesize needs text or previous '
        'translations + target_lang/lang (+voice?, speed?). Output = the worker result '
        '(text/segments, translations, audio.blob_id).'
    ),
    example={
        'condition': {'target_lang': 'en'},
        'output': {'_deferred': True, 'parent_work_item_id': 123},
    },
)
def handle_speech_step(step, session_id, workflow_id, parent_task) -> dict:
    from engine.services.workitem_factory import create_deferred_parent_workitem, create_workitem

    spec = SPEECH_JOBS[step.target]
    inputs = collect_inputs(step, session_id, parent_task)
    priority = int((step.condition or {}).get('priority', 5))
    with transaction.atomic():
        child = create_workitem(
            worker_type=spec.worker_type, artifact_type=spec.artifact_type, prompt='',
            expected_format='json', priority=priority, max_retries=SPEECH_JOB_MAX_RETRIES,
            metadata={'is_speech_job': True, 'speech_capability': step.target},
        )
        job = spec.build(inputs, child.uuid)  # SpeechInputError rolls the child back
        child.prompt = json.dumps(job.payload, ensure_ascii=False)
        child.timeout_seconds = job.timeout_seconds
        child.metadata = {**child.metadata, **job.metadata}
        child.save(update_fields=['prompt', 'timeout_seconds', 'metadata'])
        parent = create_deferred_parent_workitem(
            worker_type=WorkerTypeName.INTERNAL, artifact_type=step.target,
            prompt=f'({step.target} — waiting for {spec.artifact_type} worker)',
            expected_format='json', priority=priority, order=step.order,
            session_id=session_id, workflow_id=workflow_id, blueprint_name=step.workflow.name,
            step_type=step.step_type,
            metadata={'is_speech_step': True, 'waiting_on_workitem_uuid': str(child.uuid)},
        )
    logger.info('handle_speech_step: %s session=%s child=%s parent=%s', step.target, session_id, child.uuid, parent.pk)
    return {'_deferred': True, 'parent_work_item_id': parent.pk}


@capability_usage(
    instructions='Delete expired speech AudioBlobs (file + row). No input. Output {deleted: int}.',
    example={'condition': {}, 'output': {'deleted': 3}},
)
def handle_speech_gc_blobs(step, session_id, workflow_id, parent_task) -> dict:
    from speech.services.blobs import gc_expired_blobs

    return {'deleted': gc_expired_blobs()}
```

- [ ] **Step 4: Validators**

```python
# speech/services/capability_validators.py
def validate_speech_step(result, step, parent_task) -> dict:
    """Postcondition of speech_* internal steps: a job was dispatched (_deferred).
    A handler exception reaches here as {'error': ...} → step error, not a fake 'done'."""
    if isinstance(result, dict) and result.get('_deferred'):
        return {'valid': True}
    reason = result.get('error') if isinstance(result, dict) else None
    return {'valid': False, 'reason': reason or 'speech step did not dispatch a WorkItem'}
```

```python
# speech/services/capability_input_validators.py
_TYPES = {
    'audio_blob_id': (str,), 'language': (str,), 'source_lang': (str,), 'target_lang': (str,),
    'lang': (str,), 'voice': (str,), 'text': (str,), 'initial_prompt': (str,),
    'texts': (list,), 'speed': (int, float), 'temperature': (int, float), 'num_beams': (int,),
}


def validate_input_speech(data: dict) -> list:
    """Type checks for present speech_* inputs. Presence is checked by the payload
    builders, because the first step's input lives in AnalysisRun.context, which
    build_input_data() does not see."""
    problems = []
    for key, types in _TYPES.items():
        value = data.get(key)
        if value is not None and not isinstance(value, types):
            problems.append(f"'{key}' must be {'/'.join(t.__name__ for t in types)}, got {type(value).__name__}")
    return problems
```

```python
# speech/services/output_validators.py
"""Result-shape checks at /complete/ time, keyed by artifact_type (spec §3.6)."""
import json

from django.core.exceptions import ValidationError

from speech.constants import ROLE_OUTPUT


def _fail(reason: str) -> dict:
    return {'valid': False, 'reason': reason}


def validate_asr_result(result, step, task) -> dict:
    if result.get('v') != 1:
        return _fail(f"asr_transcribe: unsupported result version {result.get('v')!r}")
    if not isinstance(result.get('text'), str):
        return _fail("asr_transcribe: 'text' must be a string")
    if result.get('language') is not None and not isinstance(result['language'], str):
        return _fail("asr_transcribe: 'language' must be a string or null")
    if not isinstance(result.get('segments'), list):
        return _fail("asr_transcribe: 'segments' must be a list")
    return {'valid': True}


def validate_mt_result(result, step, task) -> dict:
    if result.get('v') != 1:
        return _fail(f"mt_translate: unsupported result version {result.get('v')!r}")
    translations = result.get('translations')
    if not isinstance(translations, list) or not all(isinstance(t, str) for t in translations):
        return _fail("mt_translate: 'translations' must be a list of strings")
    expected = len(json.loads(task.prompt).get('texts', []))
    if len(translations) != expected:
        return _fail(f'mt_translate: {len(translations)} translations for {expected} texts')
    return {'valid': True}


def validate_tts_result(result, step, task) -> dict:
    from speech.models import AudioBlob

    if result.get('v') != 1:
        return _fail(f"tts_synthesize: unsupported result version {result.get('v')!r}")
    audio = result.get('audio')
    if not isinstance(audio, dict) or not audio.get('blob_id'):
        return _fail("tts_synthesize: 'audio.blob_id' is required")
    try:
        blob = AudioBlob.objects.filter(uuid=audio['blob_id'], role=ROLE_OUTPUT, work_item_uuid=task.uuid).first()
    except (ValueError, ValidationError):
        return _fail('tts_synthesize: audio.blob_id is not a valid id')
    if blob is None:
        return _fail('tts_synthesize: audio blob was not uploaded for this work item')
    if audio.get('size_bytes') != blob.size_bytes:
        return _fail('tts_synthesize: audio.size_bytes does not match the uploaded blob')
    return {'valid': True}
```


- [ ] **Step 5: Plugin + ready()**

```python
# speech/plugin.py
"""SpeechPlugin — engine integration for rt-asr/rt-mt/rt-tts (spec 2026-10-06 §3.6).
Only {key: function} maps; bodies live in handlers/ and services/."""
from engine.plugin_api import EnginePlugin

from speech.constants import ARTIFACT_ASR, ARTIFACT_MT, ARTIFACT_TTS, CAP_GC, CAP_SYNTHESIZE, CAP_TRANSCRIBE, CAP_TRANSLATE

_JOB_CAPS = (CAP_TRANSCRIBE, CAP_TRANSLATE, CAP_SYNTHESIZE)


class SpeechPlugin(EnginePlugin):
    @property
    def name(self) -> str:
        return 'speech'

    def get_step_handlers(self):
        from speech.handlers.speech_steps import handle_speech_gc_blobs, handle_speech_step

        return {**{cap: handle_speech_step for cap in _JOB_CAPS}, CAP_GC: handle_speech_gc_blobs}

    def get_capability_validators(self):
        from speech.services.capability_validators import validate_speech_step

        return {cap: validate_speech_step for cap in _JOB_CAPS}

    def get_input_validators(self):
        from speech.services.capability_input_validators import validate_input_speech

        return {cap: validate_input_speech for cap in _JOB_CAPS}

    def get_output_validators(self):
        from speech.services.output_validators import validate_asr_result, validate_mt_result, validate_tts_result

        return {ARTIFACT_ASR: validate_asr_result, ARTIFACT_MT: validate_mt_result, ARTIFACT_TTS: validate_tts_result}

    def get_capabilities(self):
        from engine.capability_docs import usage_of
        from speech.handlers.speech_steps import handle_speech_gc_blobs, handle_speech_step

        speech_schema = {
            'audio_blob_id': 'str', 'language': 'str', 'source_lang': 'str', 'target_lang': 'str',
            'lang': 'str', 'voice': 'str', 'text': 'str', 'texts': 'list', 'speed': 'float',
        }
        rows = [
            (CAP_TRANSCRIBE, 'Transcribe an AudioBlob with rt-asr (WorkItem queue, deferred).',
             'audio_blob_id (+language, initial_prompt)', '{text, language, duration_s, segments[], model}'),
            (CAP_TRANSLATE, 'Translate text/segments with rt-mt (NLLB) via the WorkItem queue.',
             'target_lang (+source_lang | detected language; texts | previous segments/text)',
             '{translations[], source_lang, target_lang, model}'),
            (CAP_SYNTHESIZE, 'Synthesize speech with rt-tts; result is an output AudioBlob.',
             'text | previous translations; target_lang/lang (+voice, speed)',
             '{audio: {blob_id, size_bytes, sha256, duration_s, sample_rate}, voice, model}'),
        ]
        caps = [
            {
                'name': name, 'plugin_name': self.name, 'description': description,
                'input_description': input_description, 'input_schema': speech_schema,
                'output_description': output_description, 'execution_type': 'internal',
                **usage_of(handle_speech_step),
            }
            for name, description, input_description, output_description in rows
        ]
        caps.append({
            'name': CAP_GC, 'plugin_name': self.name,
            'description': 'Delete expired speech AudioBlobs (file + row).',
            'input_description': 'none', 'input_schema': {}, 'output_description': '{deleted: int}',
            'execution_type': 'internal', **usage_of(handle_speech_gc_blobs),
        })
        return caps
```

`speech/apps.py` — додати в `SpeechConfig`:

```python
    def ready(self):
        from engine.plugin_api import PluginRegistry

        from speech.plugin import SpeechPlugin
        PluginRegistry.register(SpeechPlugin())
```

(`register()` без запитів до БД; якщо `PluginRegistry.register` у цьому репо синхронізує capabilities у БД — обгорни у `try/except Exception: logger.warning(..., exc_info=True)` за `plugin-writing-guide.md` §9; перевір: `rg -n "def register" django_api/engine/plugin_api.py`.)

- [ ] **Step 6: Run tests**

Run: `DJTEST speech/tests/ engine/tests/test_capability.py -v` → PASS.
Run: `nix develop ./flake --command bash -c "cd django_api && ruff check speech/"` → clean.

- [ ] **Step 7: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/speech/
git -C ~/repos/swarm-nix commit -m "speech: speech_* step handler, validators and SpeechPlugin"
```

### Task A12: blueprints, GC-schedule, `speech_submit`

**Files:**
- Create: `engine/blueprints/workflows/speech.yaml`
- Modify: `engine/blueprints/workflows/meta_schedules.yaml` (додати запис у кінець списку)
- Create: `speech/management/__init__.py`, `speech/management/commands/__init__.py` (порожні), `speech/management/commands/speech_submit.py`
- Test: `speech/tests/test_blueprints.py`, `speech/tests/test_speech_submit.py`

**Interfaces:**
- Produces: Workflow `speech_transcribe_audio` (1 крок), `speech_translate_audio` (3 кроки), `__speech_blob_gc__`; MetaSchedule `speech_blob_gc_daily`; `manage.py speech_submit <path> [--blueprint speech_transcribe_audio] [--language] [--target-lang] [--voice] [--content-type]` → друкує `AnalysisRun <pk>` і `AudioBlob <uuid>`.

- [ ] **Step 1: Write the failing tests**

```python
# speech/tests/test_blueprints.py
from engine.blueprints.loader import load_blueprints


def _workflows():
    return {wf['name']: wf for wf in load_blueprints()['workflows']}


def test_speech_workflows_defined():
    wfs = _workflows()
    assert [s['target'] for s in wfs['speech_transcribe_audio']['steps']] == ['speech_transcribe']
    steps = wfs['speech_translate_audio']['steps']
    assert [s['target'] for s in steps] == ['speech_transcribe', 'speech_translate', 'speech_synthesize']
    assert steps[1]['depends_on'] == ['speech_transcribe'] and steps[2]['depends_on'] == ['speech_translate']
    for step in steps:
        assert step['condition']['execution_mode'] == 'internal'
    assert wfs['__speech_blob_gc__']['steps'][0]['target'] == 'speech_gc_blobs'


def test_gc_schedule_defined():
    schedules = {s['name']: s for s in load_blueprints()['meta_schedules']}
    assert schedules['speech_blob_gc_daily']['blueprint'] == '__speech_blob_gc__'
    assert schedules['speech_blob_gc_daily']['interval_hours'] == 24
```

```python
# speech/tests/test_speech_submit.py
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from speech.models import AudioBlob


@pytest.mark.django_db
def test_submit_stores_blob_and_spawns_run(tmp_path, speech_storage_root, wav, capsys):
    path = tmp_path / 'in.wav'
    path.write_bytes(wav())
    with patch('engine.services.workflow_executor.WorkflowExecutor.spawn_analysis_run') as spawn:
        spawn.return_value.pk = 4242
        call_command('speech_submit', str(path), '--blueprint', 'speech_translate_audio', '--language', 'uk', '--target-lang', 'en')
    blob = AudioBlob.objects.order_by('-created_at').first()
    args, kwargs = spawn.call_args
    assert args[0] == 'speech_translate_audio'
    assert kwargs['context'] == {'audio_blob_id': str(blob.uuid), 'language': 'uk', 'target_lang': 'en'}
    assert 'AnalysisRun 4242' in capsys.readouterr().out


@pytest.mark.django_db
def test_submit_fails_loudly_when_blueprint_missing(tmp_path, speech_storage_root, wav):
    path = tmp_path / 'in.wav'
    path.write_bytes(wav())
    with patch('engine.services.workflow_executor.WorkflowExecutor.spawn_analysis_run', return_value=None):
        with pytest.raises(CommandError, match='not found or inactive'):
            call_command('speech_submit', str(path))
```

- [ ] **Step 2: Run to verify failure**

Run: `DJTEST speech/tests/test_blueprints.py speech/tests/test_speech_submit.py -v`
Expected: FAIL — `KeyError: 'speech_transcribe_audio'`, `CommandError: Unknown command: 'speech_submit'`.

- [ ] **Step 3: Blueprint YAML**

```yaml
# engine/blueprints/workflows/speech.yaml
# ── speech workflows (spec 2026-10-06 django-engine-integration §3.6) ─────
# rt-asr / rt-mt / rt-tts consumed through the WorkItem queue. Each step is an
# internal handler (speech/handlers/speech_steps.py) that creates the external
# WorkItem (worker_type audio|mt, structured_json payload) + a deferred parent
# waiting on it. Run input: AnalysisRun.context {audio_blob_id, language?,
# target_lang?, voice?} — start with `manage.py speech_submit`.
# Names are NOT dunder: Workflow.save() forces __x__ names to workflow_kind=meta.
# ─────────────────────────────────────────────────────────────────────────

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
    condition:
      format: json
      priority: 5
      output_spec: 'JSON: {text, language, duration_s, segments: [{start, end, text}], model}'
      worker_type: internal
      execution_mode: internal

- name: speech_translate_audio
  description: 'ASR → MT → TTS: audio in one language → spoken translation (output AudioBlob)'
  version: '1.0'
  workflow_type: generation
  workflow_kind: execution
  is_active: true
  steps:
  - target: speech_transcribe
    step_type: transform
    order: 1
    depends_on: []
    condition:
      format: json
      priority: 5
      output_spec: 'JSON: {text, language, duration_s, segments, model}'
      worker_type: internal
      execution_mode: internal
  - target: speech_translate
    step_type: transform
    order: 2
    depends_on: [speech_transcribe]
    condition:
      format: json
      priority: 5
      output_spec: 'JSON: {translations, source_lang, target_lang, model}'
      worker_type: internal
      execution_mode: internal
  - target: speech_synthesize
    step_type: transform
    order: 3
    depends_on: [speech_translate]
    condition:
      format: json
      priority: 5
      output_spec: 'JSON: {audio: {blob_id, size_bytes, sha256, duration_s, sample_rate}, voice, model}'
      worker_type: internal
      execution_mode: internal

- name: __speech_blob_gc__
  description: 'Delete expired speech AudioBlobs (file + row) — maintenance'
  version: '1.0'
  workflow_type: analysis
  workflow_kind: meta
  is_active: true
  steps:
  - target: speech_gc_blobs
    step_type: transform
    order: 1
    depends_on: []
    condition:
      format: json
      priority: 3
      output_spec: 'JSON: {deleted: int}'
      worker_type: internal
      execution_mode: internal
```

Append to `meta_schedules.yaml` (same indentation as other entries):

```yaml
  - name: speech_blob_gc_daily
    blueprint: __speech_blob_gc__
    scope_type: self_scanning
    scope_filter: {}
    interval_hours: 24
    is_active: true
```

(Звір `scope_type: self_scanning` з записом `doc_audit_daily` у тому ж файлі — використовуй те саме значення, що там.)

- [ ] **Step 4: Management command**

```python
# speech/management/commands/speech_submit.py
"""Start a speech workflow on a local audio file (imperative, user-initiated —
the documented use of spawn_analysis_run; cf. communication/services/action_dispatcher.py)."""
import mimetypes
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from speech.constants import ROLE_INPUT
from speech.services.blobs import BlobError, store_blob

_CONTENT_TYPE_FIXUPS = {'audio/x-wav': 'audio/wav', 'audio/vnd.wave': 'audio/wav', 'audio/x-flac': 'audio/flac'}


class Command(BaseCommand):
    help = 'Store an audio file as an AudioBlob and start a speech workflow on it'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--blueprint', default='speech_transcribe_audio')
        parser.add_argument('--language', default=None, help='ASR language hint (ISO 639-1)')
        parser.add_argument('--target-lang', default=None, help='MT/TTS target language (ISO 639-1)')
        parser.add_argument('--voice', default=None)
        parser.add_argument('--content-type', default=None)

    def handle(self, *args, **options):
        from engine.services.workflow_executor import WorkflowExecutor

        path = Path(options['path'])
        if not path.is_file():
            raise CommandError(f'{path} is not a file')
        guessed = options['content_type'] or mimetypes.guess_type(path.name)[0] or ''
        content_type = _CONTENT_TYPE_FIXUPS.get(guessed, guessed)
        try:
            with path.open('rb') as fh:
                blob = store_blob(File(fh, name=path.name), content_type=content_type, role=ROLE_INPUT)
        except BlobError as exc:
            raise CommandError(f'{exc.code}: {exc}')

        context = {'audio_blob_id': str(blob.uuid)}
        for key, option in (('language', 'language'), ('target_lang', 'target_lang'), ('voice', 'voice')):
            if options[option]:
                context[key] = options[option]
        run = WorkflowExecutor.spawn_analysis_run(options['blueprint'], context=context, object_type='system')
        if run is None:
            raise CommandError(f"workflow {options['blueprint']!r} not found or inactive (run bootstrap_blueprints --workflows)")
        self.stdout.write(f'AnalysisRun {run.pk} started for AudioBlob {blob.uuid}')
```

- [ ] **Step 5: Run tests**

Run: `DJTEST speech/tests/ -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/engine/blueprints/workflows/speech.yaml django_api/engine/blueprints/workflows/meta_schedules.yaml django_api/speech/
git -C ~/repos/swarm-nix commit -m "speech: workflows, blob GC schedule and speech_submit command"
```

### Task A13: Django e2e — ASR→MT→TTS через реальні endpoint'и

**Files:**
- Test: `speech/tests/test_speech_pipeline_e2e.py`

**Interfaces:**
- Consumes: усе з A1–A12 (`/complete/`, blob views, handler, blueprints через `bootstrap_blueprints --workflows`).

- [ ] **Step 1: Write the test**

```python
# speech/tests/test_speech_pipeline_e2e.py
"""Full speech_translate_audio run with the test client in the worker's role.
WorkItems are claimed directly (shared test queue, TD-18) — /next/ selection is
covered by engine/tests/test_next_artifact_types.py."""
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone

from engine.models import WorkItem
from engine.services.workflow_executor import WorkflowExecutor
from speech.services.blobs import store_blob

from .conftest import make_wav


def _child_of(run_pk, capability):
    parent = WorkItem.objects.get(metadata__session_id=run_pk, artifact_type=capability)
    return parent, WorkItem.objects.get(uuid=parent.metadata['waiting_on_workitem_uuid'])


def _claim(wi):
    WorkItem.objects.filter(pk=wi.pk).update(status='in_progress', claimed_at=timezone.now())


def _complete(client, wi, result):
    resp = client.post(f'/api/engine/work-items/{wi.uuid}/complete/', {'raw_result': json.dumps(result, ensure_ascii=False)}, format='json')
    assert resp.status_code == 200, resp.content


@pytest.mark.django_db
def test_speech_translate_audio_end_to_end(speech_storage_root, worker_client):
    call_command('bootstrap_blueprints', '--workflows')
    source = make_wav(seconds=1.0)
    blob = store_blob(source, content_type='audio/wav', role='input')
    run = WorkflowExecutor.spawn_analysis_run(
        'speech_translate_audio', context={'audio_blob_id': str(blob.uuid), 'target_lang': 'en'}, object_type='system',
    )
    assert run is not None

    # ASR
    asr_parent, asr = _child_of(run.pk, 'speech_transcribe')
    assert (asr.worker_type, asr.artifact_type, asr.status) == ('audio', 'asr_transcribe', 'todo')
    _claim(asr)
    payload = json.loads(asr.prompt)
    resp = worker_client.get(payload['audio']['path'])
    assert resp.status_code == 200 and b''.join(resp.streaming_content) == source
    _complete(worker_client, asr, {
        'v': 1, 'text': 'Привіт. Як справи?', 'language': 'uk', 'duration_s': 1.0,
        'segments': [{'start': 0.0, 'end': 0.5, 'text': 'Привіт.'}, {'start': 0.5, 'end': 1.0, 'text': 'Як справи?'}],
        'model': 'fake-asr',
    })
    asr_parent.refresh_from_db()
    assert asr_parent.status == 'done' and asr_parent.parsed_result['text'] == 'Привіт. Як справи?'

    # MT
    mt_parent, mt = _child_of(run.pk, 'speech_translate')
    assert json.loads(mt.prompt) == {'v': 1, 'texts': ['Привіт.', 'Як справи?'], 'source_lang': 'uk', 'target_lang': 'en', 'num_beams': None}
    _claim(mt)
    _complete(worker_client, mt, {'v': 1, 'translations': ['Hello.', 'How are you?'], 'source_lang': 'uk', 'target_lang': 'en', 'model': 'fake-mt'})

    # TTS
    tts_parent, tts = _child_of(run.pk, 'speech_synthesize')
    tts_payload = json.loads(tts.prompt)
    assert (tts_payload['text'], tts_payload['lang']) == ('Hello. How are you?', 'en')
    _claim(tts)
    out = make_wav(seconds=0.5, rate=24000)
    upload = worker_client.post(tts_payload['output']['upload_path'], {'file': SimpleUploadedFile('o.wav', out, content_type='audio/wav')}, format='multipart')
    assert upload.status_code == 201
    up = upload.json()
    _complete(worker_client, tts, {
        'v': 1, 'audio': {'blob_id': up['blob_id'], 'content_type': 'audio/wav', 'size_bytes': up['size_bytes'],
                           'sha256': up['sha256'], 'duration_s': 0.5, 'sample_rate': 24000},
        'voice': 'af_heart', 'model': 'fake-tts',
    })
    tts_parent.refresh_from_db()
    assert tts_parent.status == 'done'
    assert tts_parent.parsed_result['audio']['blob_id'] == up['blob_id']


@pytest.mark.django_db
def test_missing_audio_blob_fails_step_visibly(speech_storage_root):
    call_command('bootstrap_blueprints', '--workflows')
    run = WorkflowExecutor.spawn_analysis_run('speech_transcribe_audio', context={}, object_type='system')
    failed = WorkItem.objects.get(metadata__session_id=run.pk, artifact_type='speech_transcribe')
    assert failed.status == 'error'
    assert 'audio_blob_id' in failed.validation_error
    assert not WorkItem.objects.filter(metadata__speech_capability='speech_transcribe', metadata__session_id=run.pk).exists()
```

- [ ] **Step 2: Run**

Run: `DJTEST speech/tests/test_speech_pipeline_e2e.py -v`
Expected: PASS. Якщо падає — це інтеграційна розбіжність A3/A11 з реальним `WorkflowExecutor` (напр. `on_complete` не диспатчить наступний internal-крок синхронно); діагностувати через `superpowers:systematic-debugging`, виправляти в задачі-власнику коду, не в тесті.

- [ ] **Step 3: Full regression**

Run: `DJTEST engine/tests/ llm_services/tests/ speech/tests/ knowledge/tests/test_rerank*.py clusterops/tests/ -q`
Expected: PASS (крім тестів, що падали й до змін — порівняти з `git stash`-прогоном, якщо є сумнів).

- [ ] **Step 4: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/speech/tests/test_speech_pipeline_e2e.py
git -C ~/repos/swarm-nix commit -m "speech: end-to-end test of the ASR→MT→TTS workflow"
```

### Task A14: документація (Documentation Update Rule)

**Files:**
- Create: `context/docs/adr/ADR-057-speech-services-workitem-queue.md` (номер — наступний вільний: `ls django_api/context/docs/adr | sort -V | tail -3`)
- Create: `context/docs/reference/speech.md`, `speech/context/README.md`
- Modify: `context/docs/reference/tier.md`, `context/docs/reference/README.md` (індекс), `context/tasks.md`, `context/ui-tasks.md`, `context/technical-debt.md`, `AGENTS.md` (apps-таблиця, URL namespaces, Management Commands), `llm_services/context/README.md`, `autodoc/docmeta/vocabulary.toml`

- [ ] **Step 1: Vocabulary**

`autodoc/docmeta/vocabulary.toml` — у `[apps]` додати `speech = "Speech services via WorkItem queue: AudioBlob, speech_* capabilities (rt-asr/rt-mt/rt-tts)"`; у кінець `[[entities]]` додати:

```toml
[[entities]]
name = "AudioBlob"
app = "speech"
aliases = ["audio blob", "аудіо"]
description = "speech.AudioBlob — audio passed by reference to/from rt-speech workers"
```

Оновити опис `llm_services` у `[apps]`: `"Registry of compute worker services: llama + rt-speech (LLMService.kind, LLMModel)"`.

- [ ] **Step 2: ADR**

`ADR-057-speech-services-workitem-queue.md` за `ADR-TEMPLATE.md`, frontmatter:

```yaml
---
title: ADR-057 — Speech services via WorkItem queue (dedicated workers)
type: adr
doc_class: canonical
status: accepted
updated: <дата реалізації>
apps: [engine, llm_services, speech]
entities: [WorkItem, WorkerType, LLMService, AudioBlob, Capability]
---
```

Розділи (стисло, з посиланнями на код): Контекст (spec §1 — tier-only `next()`, немає storage, kind-less реєстр); Рішення (1) `WorkerType.payload_contract` + `?artifact_types=`; (2) structured retry без переписування prompt; (3) waiting_on parent: merge + fail; (4) per-row `timeout_seconds`; (5) `AudioBlob` by reference, worker-scoped download/upload; (6) `LLMService.kind` + `port`, kind-aware health, фікс `model.path`; (7) capabilities ≠ artifact_types; Альтернативи (spec §2); Наслідки (rerank/cli_executor ще на tier-1 ексклюзиві — TD; ім'я `LLMService` відстає від ролі — TD); Пов'язані: ADR-026, ADR-029, ADR-030, ADR-040, ADR-042, ADR-055, spec/plan у `real-time-transletor/docs/superpowers/`.

- [ ] **Step 3: Reference + app docs**

`context/docs/reference/speech.md` (frontmatter `type: reference`, `doc_class: canonical`, `apps: [speech, engine]`, `entities: [AudioBlob, WorkItem]`): таблиця "де виставляється → для чого" — усі `SPEECH_*` (A8 Step 2) і service-env (`RT_WORKER_ENABLED`, `RT_DJANGO_ENDPOINTS`, `RT_DJANGO_BOOTSTRAP_TOKEN_FILE`, `RT_SERVICE_NAME`, `RT_WORKER_POLL_INTERVAL`, `RT_WORKER_MAX_DOWNLOAD_BYTES`); FAQ: "speech WorkItem висить у todo" → перевір, чи сервіс з `RT_WORKER_ENABLED=true` поллить з `artifact_types=…` і `/status.worker.endpoints[].last_error`. Додати рядок в індекс `context/docs/reference/README.md`.

`tier.md` — у таблицю джерел додати рядок: "`engine/views_work_item.py::next()` `?artifact_types=` (`apply_claim_scope`) | Dedicated workers (`WorkerType.payload_contract='structured_json'`: `audio`, `mt`) — поза tier-маршрутизацією; generic-поллери їх ніколи не бачать | ADR-057"; у "Зміни в документі" — рядок з датою.

`speech/context/README.md` — поточний стан app (моделі, endpoints, capabilities, workflows, команди, тести). `llm_services/context/README.md` — `kind`, `port` при реєстрації, kind-aware health, фікс `model.path`.

`AGENTS.md` (django_api): рядок `speech` у таблиці Apps; `/api/speech/ → speech (namespace: speech)` у URL namespaces; `python manage.py speech_submit <file> [--blueprint …]` у Management Commands.

- [ ] **Step 4: Tasks / UI / debt**

`context/tasks.md` — задача `TASK-ENG-<наступний>` "Speech services via WorkItem queue" зі статусом і посиланням на ADR-057; backlog-пункти з spec §11 (Q2 міграція rerank/cli_executor на structured_json; Q3 kind-перевірка artifact_types; Q4 чанкінг довгих аудіо; Q5 realtime vs batch пріоритет).
`context/ui-tasks.md` — kind-бейдж у `/workers/`; UI запуску `speech_*` workflow з завантаженням файлу; плеєр для output AudioBlob.
`context/technical-debt.md` — TD: "`LLMService` реєструє не лише LLM (`kind`) — кандидат на перейменування `ComputeService`" і TD: "rerank/cli_executor маршрутизуються tier-1-ексклюзивом замість `payload_contract=structured_json` (ADR-057)".

- [ ] **Step 5: Lint + budget**

Run: `DJMANAGE doc_lint --errors-only context/docs/adr/ADR-057-speech-services-workitem-queue.md context/docs/reference/speech.md context/docs/reference/tier.md speech/context/README.md` → без помилок.
Run: `nix develop ./flake --command python3 scripts/check_instruction_budget.py` → OK.

- [ ] **Step 6: Commit**

```bash
git -C ~/repos/swarm-nix add django_api/context/ django_api/speech/context/ django_api/llm_services/context/ django_api/AGENTS.md django_api/autodoc/docmeta/vocabulary.toml
git -C ~/repos/swarm-nix commit -m "docs: ADR-057 speech services via WorkItem queue; reference, tasks, debt"
```

---

# Part B — real-time-transletor (service-side worker)

Шляхи — відносно `~/repos/real-time-transletor`. Скорочення: `RTTEST <args>` ≡ `nix develop --command pytest <args>`.

### Task B0: Preflight — підпроєкт 1 на місці, карта імен

**Files:** немає змін коду (лише `nix/devshell.nix`, якщо бракує залежностей).

Part B спирається на код підпроєкту 1. Імена в його спеці зафіксовані на рівні модулів і env; кілька ідентифікаторів треба звірити.

- [ ] **Step 1: Перевір наявність і сигнатури**

```bash
cd ~/repos/real-time-transletor
grep -n "class ModelState\|def submit_threadsafe\|self.worker\b\|self.state\b\|self.backend\b" src/services/common/runtime.py
grep -n "class Priority" src/services/common/worker.py
grep -n "lifespan_hooks" src/services/common/app.py src/services/asr/app.py src/services/mt/app.py src/services/tts/app.py
grep -n "def build_health_router\|/status" src/services/common/health.py
grep -n "^def " src/services/common/audio.py src/services/mt/languages.py src/services/tts/encoding.py
grep -n "class DecodeOptions\|class Transcription\|class FakeAsrBackend" src/services/asr/backend.py
grep -n "class FakeMtBackend\|class FakeTtsBackend\|def voices\|class UnsupportedVoiceLanguage" src/services/mt/backend.py src/services/tts/backend.py
grep -n "beam_size\|vad\b\|num_beams\|max_chars\|max_batch\|voice\b\|speed\|sample_rate" src/services/asr/settings.py src/services/mt/settings.py src/services/tts/settings.py
grep -n "backend_factory\|def asr_client\|def mt_client\|def tts_client" tests/services/conftest.py
nix develop --command python -c "import httpx, pydantic, fastapi; print(httpx.__version__, pydantic.VERSION)"
```

Expected: усі файли існують; `httpx`/`pydantic`/`fastapi` імпортуються (SP1 §10.1 додає їх у devshell). Якщо `httpx` відсутній — додай `httpx` у `python313.withPackages` у `nix/devshell.nix` і закоміть (`chore(devshell): add httpx`). У bundle (`nix/packages/deps.nix`) `httpx` уже є (SP2, overview §7 C5).

- [ ] **Step 2: Заповни карту імен** (використовується в B6–B10; якщо фактичне ім'я інше — підстав його в коді цих задач, не змінюючи логіки)

| Використання в плані | Очікуване ім'я (SP1 spec) | Фактичне |
|---|---|---|
| стан моделі | `src.services.common.runtime.ModelState.READY` | |
| черга | `runtime.worker.pending`, `runtime.submit_threadsafe(fn, *args, priority=…)` | |
| пріоритет | `src.services.common.worker.Priority.REQUEST` | |
| декодування | `src.services.common.audio.decode_audio_file(data: bytes) -> np.ndarray` | |
| ресемплінг | `src.services.common.audio.resample(audio, src_rate, dst_rate) -> np.ndarray` | |
| WAV | `src.services.tts.encoding.wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes` | |
| ISO→NLLB | `src.services.mt.languages.to_nllb(code: str) -> str` (ValueError/SettingsError на невідомий) | |
| ASR settings | `settings.asr_beam_size`, `settings.asr_vad` | |
| MT settings | `settings.mt_num_beams`, `settings.mt_max_chars`, `settings.mt_max_batch` | |
| TTS settings | `settings.tts_voice`, `settings.tts_max_chars` | |
| app factory | `create_app(settings, backend_factory=…, lifespan_hooks=…)` | |

### Task B1: `errors.py` + `config.py`

**Files:**
- Create: `src/services/common/django_worker/__init__.py`, `src/services/common/django_worker/errors.py`, `src/services/common/django_worker/config.py`
- Test: `tests/services/django_worker/test_config.py`

**Interfaces:**
- Produces: `WorkerError`, `TransportError`, `AuthError`, `JobError`; `WorkerConfig` (frozen dataclass: `service, enabled, endpoints: tuple[str,...], bootstrap_token_file: Path | None, service_name, port, poll_interval=10.0, max_download_bytes=104857600, connect_timeout=5.0, api_timeout=30.0, blob_timeout=120.0`; properties `artifact_types`, `kind`; `from_env(service, environ=None)`; `validate()`; `read_bootstrap_token() -> str`); `WorkerConfigError(ValueError)`; `SERVICE_ARTIFACT_TYPES`, `DEFAULT_PORTS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/django_worker/test_config.py
import socket

import pytest

from src.services.common.django_worker.config import WorkerConfig, WorkerConfigError


def test_disabled_by_default():
    cfg = WorkerConfig.from_env("asr", {})
    assert cfg.enabled is False
    assert cfg.artifact_types == ("asr_transcribe",)
    assert cfg.port == 8091
    assert cfg.service_name == f"{socket.gethostname()}-rt-asr"
    assert (cfg.poll_interval, cfg.max_download_bytes) == (10.0, 104857600)


def test_enabled_full(tmp_path):
    token = tmp_path / "boot"
    token.write_text("secret\n")
    cfg = WorkerConfig.from_env("tts", {
        "RT_WORKER_ENABLED": "true",
        "RT_DJANGO_ENDPOINTS": "http://cyborg:8000/, http://sktop:8000",
        "RT_DJANGO_BOOTSTRAP_TOKEN_FILE": str(token),
        "RT_SERVICE_NAME": "box-rt-tts",
        "RT_PORT": "9093",
        "RT_WORKER_POLL_INTERVAL": "2.5",
    })
    assert cfg.endpoints == ("http://cyborg:8000", "http://sktop:8000")
    assert (cfg.kind, cfg.service_name, cfg.port, cfg.poll_interval) == ("tts", "box-rt-tts", 9093, 2.5)
    assert cfg.artifact_types == ("tts_synthesize",)
    assert cfg.read_bootstrap_token() == "secret"


@pytest.mark.parametrize("env,match", [
    ({"RT_WORKER_ENABLED": "true"}, "RT_DJANGO_ENDPOINTS"),
    ({"RT_WORKER_ENABLED": "true", "RT_DJANGO_ENDPOINTS": "http://x"}, "RT_DJANGO_BOOTSTRAP_TOKEN_FILE"),
    ({"RT_WORKER_ENABLED": "true", "RT_DJANGO_ENDPOINTS": "http://x", "RT_DJANGO_BOOTSTRAP_TOKEN_FILE": "/nonexistent/boot"}, "not a file"),
    ({"RT_WORKER_ENABLED": "true", "RT_DJANGO_ENDPOINTS": "ftp://x", "RT_DJANGO_BOOTSTRAP_TOKEN_FILE": __file__}, "http"),
    ({"RT_WORKER_ENABLED": "maybe"}, "RT_WORKER_ENABLED"),
    ({"RT_WORKER_POLL_INTERVAL": "0"}, "RT_WORKER_POLL_INTERVAL"),
])
def test_invalid_config_is_explicit(env, match):
    with pytest.raises(WorkerConfigError, match=match):
        WorkerConfig.from_env("mt", env)


def test_empty_token_file_rejected(tmp_path):
    token = tmp_path / "boot"
    token.write_text("  \n")
    cfg = WorkerConfig.from_env("mt", {"RT_WORKER_ENABLED": "true", "RT_DJANGO_ENDPOINTS": "http://x", "RT_DJANGO_BOOTSTRAP_TOKEN_FILE": str(token)})
    with pytest.raises(WorkerConfigError, match="empty"):
        cfg.read_bootstrap_token()


def test_unknown_service():
    with pytest.raises(WorkerConfigError):
        WorkerConfig.from_env("ocr", {})
```

- [ ] **Step 2: Run to verify failure**

Run: `RTTEST tests/services/django_worker/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.common.django_worker'`.

- [ ] **Step 3: Implement**

```python
# src/services/common/django_worker/errors.py
"""Failure classes of the Django WorkItem worker (spec 2026-10-06 django-engine-integration §5)."""


class WorkerError(Exception):
    """Base class."""


class TransportError(WorkerError):
    """Django endpoint unreachable or answered with an unexpected status — retry later."""


class AuthError(WorkerError):
    """Registration rejected (bad bootstrap token, kind conflict) or token refused twice."""


class JobError(WorkerError):
    """Expected job failure — reported to Django as status=error with this message."""
```

```python
# src/services/common/django_worker/config.py
"""Worker configuration from env (spec 2026-10-06 django-engine-integration §5.2).
The bootstrap token is only ever read from a FILE, never from env/Nix."""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

SERVICE_ARTIFACT_TYPES: dict[str, tuple[str, ...]] = {
    "asr": ("asr_transcribe",),
    "mt": ("mt_translate",),
    "tts": ("tts_synthesize",),
}
DEFAULT_PORTS = {"asr": 8091, "mt": 8092, "tts": 8093}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class WorkerConfigError(ValueError):
    """Invalid or incomplete worker configuration."""


def _bool(env: Mapping[str, str], key: str, default: str) -> bool:
    raw = env.get(key, default).strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise WorkerConfigError(f"{key}={raw!r} is not a boolean")


def _number(env: Mapping[str, str], key: str, default, cast):
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise WorkerConfigError(f"{key}={raw!r} is not a valid {cast.__name__}")
    if value <= 0:
        raise WorkerConfigError(f"{key} must be > 0, got {raw!r}")
    return value


@dataclass(frozen=True)
class WorkerConfig:
    service: str
    enabled: bool
    endpoints: tuple[str, ...]
    bootstrap_token_file: Path | None
    service_name: str
    port: int
    poll_interval: float = 10.0
    max_download_bytes: int = 104_857_600
    connect_timeout: float = 5.0
    api_timeout: float = 30.0
    blob_timeout: float = 120.0

    @property
    def artifact_types(self) -> tuple[str, ...]:
        return SERVICE_ARTIFACT_TYPES[self.service]

    @property
    def kind(self) -> str:
        return self.service

    @classmethod
    def from_env(cls, service: str, environ: Mapping[str, str] | None = None) -> "WorkerConfig":
        if service not in SERVICE_ARTIFACT_TYPES:
            raise WorkerConfigError(f"unknown service {service!r}")
        env = os.environ if environ is None else environ
        token_file = env.get("RT_DJANGO_BOOTSTRAP_TOKEN_FILE", "").strip()
        cfg = cls(
            service=service,
            enabled=_bool(env, "RT_WORKER_ENABLED", "false"),
            endpoints=tuple(e.strip().rstrip("/") for e in env.get("RT_DJANGO_ENDPOINTS", "").split(",") if e.strip()),
            bootstrap_token_file=Path(token_file) if token_file else None,
            service_name=env.get("RT_SERVICE_NAME", "").strip() or f"{socket.gethostname()}-rt-{service}",
            port=_number(env, "RT_PORT", DEFAULT_PORTS[service], int),
            poll_interval=_number(env, "RT_WORKER_POLL_INTERVAL", 10.0, float),
            max_download_bytes=_number(env, "RT_WORKER_MAX_DOWNLOAD_BYTES", 104_857_600, int),
        )
        if cfg.enabled:
            cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.endpoints:
            raise WorkerConfigError("RT_WORKER_ENABLED=true but RT_DJANGO_ENDPOINTS is empty")
        for endpoint in self.endpoints:
            if not endpoint.startswith(("http://", "https://")):
                raise WorkerConfigError(f"RT_DJANGO_ENDPOINTS entry {endpoint!r} must start with http:// or https://")
        if self.bootstrap_token_file is None:
            raise WorkerConfigError("RT_WORKER_ENABLED=true but RT_DJANGO_BOOTSTRAP_TOKEN_FILE is not set")
        if not self.bootstrap_token_file.is_file():
            raise WorkerConfigError(f"RT_DJANGO_BOOTSTRAP_TOKEN_FILE {self.bootstrap_token_file} is not a file")

    def read_bootstrap_token(self) -> str:
        """Re-read on every (re)registration so a rotated secret needs no restart."""
        if self.bootstrap_token_file is None:
            raise WorkerConfigError("RT_DJANGO_BOOTSTRAP_TOKEN_FILE is not set")
        try:
            token = self.bootstrap_token_file.read_text().strip()
        except OSError as exc:
            raise WorkerConfigError(f"cannot read {self.bootstrap_token_file}: {exc}") from exc
        if not token:
            raise WorkerConfigError(f"bootstrap token file {self.bootstrap_token_file} is empty")
        return token
```

```python
# src/services/common/django_worker/__init__.py
"""Django WorkItem worker for rt-speech services (spec 2026-10-06 django-engine-integration §5).
Disabled unless RT_WORKER_ENABLED=true."""
from src.services.common.django_worker.config import WorkerConfig, WorkerConfigError
from src.services.common.django_worker.errors import AuthError, JobError, TransportError, WorkerError

__all__ = ["AuthError", "JobError", "TransportError", "WorkerConfig", "WorkerConfigError", "WorkerError"]
```

(Якщо SP1 імпортує пакети як `services.…` без `src.` — використовуй той самий стиль імпорту, що в `src/services/asr/app.py`.)

- [ ] **Step 4: Run tests**

Run: `RTTEST tests/services/django_worker/test_config.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/common/django_worker/ tests/services/django_worker/test_config.py
git commit -m "feat(django-worker): worker config from env and error classes"
```

### Task B2: тестовий `FakeDjango` + `TokenManager`

**Files:**
- Create: `tests/services/django_worker/conftest.py`, `src/services/common/django_worker/auth.py`
- Test: `tests/services/django_worker/test_auth.py`

**Interfaces:**
- Consumes: `WorkerConfig`, `TransportError`, `AuthError`, `WorkerConfigError` (B1).
- Produces: `TokenManager(base_url: str, config: WorkerConfig, http: httpx.Client, clock=time.monotonic)` з `get_token() -> str`, `invalidate()`, `registered: bool`; фікстури `fake_django` (`FakeDjango`), `worker_config` (factory `(service="asr", **env) -> WorkerConfig`), `http_client` (`httpx.Client` поверх `fake_django`), `fake_runtime` (factory `(backend) -> FakeRuntime`).

- [ ] **Step 1: Test harness**

```python
# tests/services/django_worker/conftest.py
"""In-process fake of the Django WorkItem API (register/next/complete/blobs) over
httpx.MockTransport + a synchronous fake of SP1's ServiceRuntime."""
import concurrent.futures
import email.policy
import hashlib
import itertools
import json
import re
import uuid
from email.parser import BytesParser

import httpx
import pytest

from src.services.common.django_worker.config import WorkerConfig

BASE = "http://django.test"


class FakeDjango:
    def __init__(self, bootstrap="boot-secret"):
        self.bootstrap = bootstrap
        self.registrations: list[dict] = []
        self.valid_tokens: set[str] = set()
        self.tasks: list[dict] = []
        self.claimed: dict[str, dict] = {}
        self.completed: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.uploads: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.paused = False
        self.fail_complete = 0
        self.next_status: int | None = None
        self._ids = itertools.count(1)

    # --- test helpers ---
    def enqueue(self, artifact_type: str, payload: dict) -> str:
        wi = str(uuid.uuid4())
        self.tasks.append({"uuid": wi, "artifact_type": artifact_type, "prompt": json.dumps(payload),
                           "max_tokens": 2000, "temperature": 0.1, "repeat_penalty": None, "top_p": None})
        return wi

    def add_blob(self, data: bytes, work_item: str, content_type="audio/wav") -> dict:
        blob_id = str(uuid.uuid4())
        path = f"/api/speech/blobs/{blob_id}/content/?work_item={work_item}"
        self.blobs[path] = data
        return {"path": path, "content_type": content_type, "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "duration_s": None}

    def revoke_tokens(self):
        self.valid_tokens.clear()

    # --- transport ---
    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        path = request.url.path
        if path == "/api/llm/register/":
            body = json.loads(request.content)
            self.registrations.append(body)
            if body.get("bootstrap_token") != self.bootstrap:
                return httpx.Response(403, json={"error": "Invalid bootstrap token"})
            token = f"tok-{next(self._ids)}"
            self.valid_tokens.add(token)
            return httpx.Response(200, json={"access_token": token, "expires_in": 3600, "service_id": 1,
                                             "service_name": body["service_name"], "kind": body.get("kind", "llm")})
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.valid_tokens:
            return httpx.Response(401, json={"detail": "Invalid token"})
        if path == "/api/engine/work-items/next/":
            if self.next_status is not None:
                return httpx.Response(self.next_status, text="boom")
            if self.paused:
                return httpx.Response(200, json={"paused": True, "retry_after": 60, "message": "disabled"})
            wanted = request.url.params.get("artifact_types", "").split(",")
            for task in self.tasks:
                if task["artifact_type"] in wanted:
                    self.tasks.remove(task)
                    self.claimed[task["uuid"]] = task
                    return httpx.Response(200, json=task)
            return httpx.Response(204)
        match = re.fullmatch(r"/api/engine/work-items/([^/]+)/complete/", path)
        if match:
            if self.fail_complete:
                self.fail_complete -= 1
                return httpx.Response(503, text="unavailable")
            if match.group(1) not in self.claimed:
                return httpx.Response(400, json={"error": "Task is not in progress"})
            self.completed[match.group(1)] = json.loads(request.content)
            del self.claimed[match.group(1)]
            return httpx.Response(200, json={"status": "done"})
        raw_path = request.url.raw_path.decode()
        if request.method == "GET" and raw_path in self.blobs:
            return httpx.Response(200, content=self.blobs[raw_path], headers={"Content-Type": "audio/wav"})
        if request.method == "POST" and path == "/api/speech/blobs/":
            data, content_type = _multipart_file(request)
            blob_id = str(uuid.uuid4())
            self.uploads.append({"blob_id": blob_id, "work_item": request.url.params.get("work_item"),
                                 "data": data, "content_type": content_type})
            return httpx.Response(201, json={"blob_id": blob_id, "size_bytes": len(data),
                                             "sha256": hashlib.sha256(data).hexdigest(), "content_type": content_type})
        return httpx.Response(404, json={"error": "not found"})


def _multipart_file(request: httpx.Request) -> tuple[bytes, str]:
    header = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = BytesParser(policy=email.policy.HTTP).parsebytes(header + request.content)
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") == "file":
            return part.get_payload(decode=True), part.get_content_type()
    raise AssertionError("no 'file' part in multipart upload")


class _Queue:
    pending = 0


class FakeRuntime:
    """Synchronous stand-in for SP1 ServiceRuntime (state, backend, worker.pending,
    submit_threadsafe, status_extras)."""

    def __init__(self, backend):
        from src.services.common.runtime import ModelState

        self.backend = backend
        self.state = ModelState.READY
        self.worker = _Queue()
        self.status_extras: dict = {}
        self.priorities: list = []

    def submit_threadsafe(self, fn, *args, priority):
        self.priorities.append(priority)
        future: concurrent.futures.Future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args))
        except Exception as exc:  # mirror InferenceWorker: the error travels in the Future
            future.set_exception(exc)
        return future


@pytest.fixture
def fake_django():
    return FakeDjango()


@pytest.fixture
def http_client(fake_django):
    with httpx.Client(transport=httpx.MockTransport(fake_django.handler)) as client:
        yield client


@pytest.fixture
def worker_config(tmp_path):
    token = tmp_path / "bootstrap-token"
    token.write_text("boot-secret\n")

    def make(service="asr", **env):
        base = {"RT_WORKER_ENABLED": "true", "RT_DJANGO_ENDPOINTS": BASE,
                "RT_DJANGO_BOOTSTRAP_TOKEN_FILE": str(token), "RT_SERVICE_NAME": f"box-rt-{service}",
                "RT_WORKER_POLL_INTERVAL": "0.01"}
        base.update(env)
        return WorkerConfig.from_env(service, base)

    make.token_file = token
    return make


@pytest.fixture
def fake_runtime():
    return FakeRuntime
```

- [ ] **Step 2: Write the failing test**

```python
# tests/services/django_worker/test_auth.py
import pytest

from src.services.common.django_worker.auth import TokenManager
from src.services.common.django_worker.errors import AuthError, TransportError

BASE = "http://django.test"


def test_registers_with_kind_and_port_and_caches(fake_django, http_client, worker_config):
    tm = TokenManager(BASE, worker_config("mt", RT_PORT="8092"), http_client)
    assert tm.registered is False
    first = tm.get_token()
    assert tm.get_token() == first
    assert tm.registered is True
    assert fake_django.registrations == [{"service_name": "box-rt-mt", "bootstrap_token": "boot-secret", "kind": "mt", "port": 8092}]


def test_invalidate_forces_reregistration(fake_django, http_client, worker_config):
    tm = TokenManager(BASE, worker_config(), http_client)
    first = tm.get_token()
    tm.invalidate()
    assert tm.get_token() != first
    assert len(fake_django.registrations) == 2


def test_token_expiry_triggers_refresh(fake_django, http_client, worker_config):
    now = [0.0]
    tm = TokenManager(BASE, worker_config(), http_client, clock=lambda: now[0])
    tm.get_token()
    now[0] = 3600 - 30  # inside the 60 s refresh margin
    tm.get_token()
    assert len(fake_django.registrations) == 2


def test_bootstrap_file_is_reread(fake_django, http_client, worker_config):
    cfg = worker_config()
    worker_config.token_file.write_text("rotated")
    fake_django.bootstrap = "rotated"
    assert TokenManager(BASE, cfg, http_client).get_token().startswith("tok-")


def test_rejected_bootstrap_is_auth_error(fake_django, http_client, worker_config):
    fake_django.bootstrap = "other"
    with pytest.raises(AuthError, match="403"):
        TokenManager(BASE, worker_config(), http_client).get_token()


def test_network_error_is_transport_error(worker_config):
    import httpx

    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(boom)) as client:
        with pytest.raises(TransportError):
            TokenManager(BASE, worker_config(), client).get_token()
```

- [ ] **Step 3: Run to verify failure**

Run: `RTTEST tests/services/django_worker/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: …auth`.

- [ ] **Step 4: Implement**

```python
# src/services/common/django_worker/auth.py
"""Per-endpoint token handshake: bootstrap token (from file) → per-service access token
(POST /api/llm/register/, spec 2026-10-06 django-engine-integration §5.4)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import httpx

from src.services.common.django_worker.config import WorkerConfig, WorkerConfigError
from src.services.common.django_worker.errors import AuthError, TransportError

logger = logging.getLogger(__name__)
REFRESH_MARGIN_S = 60.0


class TokenManager:
    def __init__(self, base_url: str, config: WorkerConfig, http: httpx.Client,
                 clock: Callable[[], float] = time.monotonic):
        self.base_url = base_url.rstrip("/")
        self._config = config
        self._http = http
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    @property
    def registered(self) -> bool:
        return self._token is not None

    def get_token(self) -> str:
        with self._lock:
            if self._token is not None and self._clock() < self._expires_at - REFRESH_MARGIN_S:
                return self._token
            return self._register()

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def _register(self) -> str:
        try:
            bootstrap = self._config.read_bootstrap_token()
        except WorkerConfigError as exc:
            raise AuthError(str(exc)) from exc
        body = {"service_name": self._config.service_name, "bootstrap_token": bootstrap,
                "kind": self._config.kind, "port": self._config.port}
        try:
            resp = self._http.post(f"{self.base_url}/api/llm/register/", json=body,
                                   timeout=httpx.Timeout(self._config.api_timeout, connect=self._config.connect_timeout))
        except httpx.HTTPError as exc:
            raise TransportError(f"register at {self.base_url}: {exc}") from exc
        if resp.status_code in (400, 403, 409):
            raise AuthError(f"register at {self.base_url} rejected: HTTP {resp.status_code} {resp.text[:200]}")
        if resp.status_code != 200:
            raise TransportError(f"register at {self.base_url}: HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        self._token = data["access_token"]
        self._expires_at = self._clock() + float(data.get("expires_in", 86400))
        logger.info("registered %s (kind=%s) at %s", self._config.service_name, self._config.kind, self.base_url)
        return self._token
```

- [ ] **Step 5: Run tests**

Run: `RTTEST tests/services/django_worker/test_auth.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/common/django_worker/auth.py tests/services/django_worker/conftest.py tests/services/django_worker/test_auth.py
git commit -m "feat(django-worker): token manager with bootstrap-file registration and fake Django harness"
```

### Task B3: `jobs.py` (схеми, контекст, `run_job`) + `client.py`

**Files:**
- Create: `src/services/common/django_worker/jobs.py`, `src/services/common/django_worker/client.py`
- Test: `tests/services/django_worker/test_jobs.py`, `tests/services/django_worker/test_client.py`

**Interfaces:**
- Consumes: B1, B2.
- Produces:
  - `ensure_speech_path(path: str) -> str` (JobError, якщо є scheme/host, шлях не `/api/speech/…` або містить `..`);
  - `BlobRef` (pydantic: `path, content_type, size_bytes>0, sha256 (64 hex), duration_s: float | None`), `UploadSpec` (`upload_path, max_bytes>0`);
  - `JobContext(client, work_item_uuid: str, max_download_bytes: int)` з `fetch_blob(ref: BlobRef) -> bytes`, `upload_blob(spec: UploadSpec, data: bytes, content_type: str) -> dict`;
  - `JobHandler = Callable[[dict, JobContext], dict]`; `run_job(task: dict, handlers: Mapping[str, JobHandler], ctx: JobContext, clock=time.perf_counter) -> dict`;
  - `DjangoEndpointClient(base_url, config, http, tokens=None, clock=time.monotonic)` з `next_task(artifact_types) -> dict | None`, `complete(uuid, result, *, attempts=3, sleep=time.sleep) -> bool`, `fetch_blob(ref, max_bytes) -> bytes`, `upload_blob(spec, data, content_type, filename) -> dict`, `blocked() -> bool`, `note_failure(msg)`, `note_success()`, атрибути `base_url, tokens, paused_until, backoff_until, last_error`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/django_worker/test_jobs.py
import json

import pytest
from pydantic import ValidationError

from src.services.common.django_worker.errors import JobError
from src.services.common.django_worker.jobs import BlobRef, UploadSpec, ensure_speech_path, run_job

SHA = "a" * 64


@pytest.mark.parametrize("path", [
    "http://evil.example/api/speech/blobs/x/content/",
    "//evil.example/api/speech/blobs/x/content/",
    "/api/engine/work-items/next/",
    "/api/speech/../llm/register/",
    "api/speech/blobs/x/content/",
])
def test_blobref_rejects_foreign_paths(path):
    with pytest.raises(JobError):
        ensure_speech_path(path)
    with pytest.raises(ValidationError):
        BlobRef(path=path, content_type="audio/wav", size_bytes=1, sha256=SHA)


def test_valid_refs():
    ref = BlobRef(path="/api/speech/blobs/b/content/?work_item=w", content_type="audio/wav", size_bytes=10, sha256=SHA)
    assert ref.duration_s is None
    assert UploadSpec(upload_path="/api/speech/blobs/?work_item=w", max_bytes=5).max_bytes == 5


def _task(prompt, artifact_type="asr_transcribe"):
    return {"uuid": "w1", "artifact_type": artifact_type, "prompt": prompt}


def test_run_job_success_wraps_result():
    out = run_job(_task(json.dumps({"v": 1, "x": 2})), {"asr_transcribe": lambda p, ctx: {"text": f"got {p['x']}"}}, ctx=None)
    assert out["status"] == "done" and out["error_message"] == ""
    assert json.loads(out["raw_result"]) == {"v": 1, "text": "got 2"}
    assert out["generation_seconds"] >= 0


@pytest.mark.parametrize("task,match", [
    (_task("{}", artifact_type="ocr"), "no handler for artifact_type 'ocr'"),
    (_task("not json"), "JSONDecodeError"),
    (_task("[1]"), "not a JSON object"),
    (_task(json.dumps({"v": 2})), "unsupported payload version 2"),
])
def test_run_job_reports_errors(task, match):
    out = run_job(task, {"asr_transcribe": lambda p, ctx: {}}, ctx=None)
    assert out["status"] == "error" and out["raw_result"] == ""
    assert match in out["error_message"]


def test_run_job_handler_exception_becomes_error():
    def boom(payload, ctx):
        raise JobError("blob sha256 mismatch")
    out = run_job(_task(json.dumps({"v": 1})), {"asr_transcribe": boom}, ctx=None)
    assert out["status"] == "error"
    assert out["error_message"] == "JobError: blob sha256 mismatch"
```

```python
# tests/services/django_worker/test_client.py
import hashlib

import httpx
import pytest

from src.services.common.django_worker.client import DjangoEndpointClient
from src.services.common.django_worker.errors import AuthError, JobError, TransportError
from src.services.common.django_worker.jobs import BlobRef, UploadSpec

BASE = "http://django.test"


def _client(http_client, worker_config, service="asr", clock=None):
    kwargs = {"clock": clock} if clock else {}
    return DjangoEndpointClient(BASE, worker_config(service), http_client, **kwargs)


def test_next_task_passes_artifact_types(fake_django, http_client, worker_config):
    wi = fake_django.enqueue("asr_transcribe", {"v": 1})
    client = _client(http_client, worker_config)
    assert client.next_task(("asr_transcribe",))["uuid"] == wi
    assert client.next_task(("asr_transcribe",)) is None
    assert fake_django.requests[-1].url.params["artifact_types"] == "asr_transcribe"


def test_401_reregisters_once(fake_django, http_client, worker_config):
    client = _client(http_client, worker_config)
    client.next_task(("asr_transcribe",))
    fake_django.revoke_tokens()
    assert client.next_task(("asr_transcribe",)) is None
    assert len(fake_django.registrations) == 2


def test_paused_sets_paused_until(fake_django, http_client, worker_config):
    now = [100.0]
    client = _client(http_client, worker_config, clock=lambda: now[0])
    fake_django.paused = True
    assert client.next_task(("asr_transcribe",)) is None
    assert client.paused_until == 160.0 and client.blocked()
    now[0] = 161.0
    assert not client.blocked()


def test_unexpected_status_is_transport_error(fake_django, http_client, worker_config):
    fake_django.next_status = 500
    with pytest.raises(TransportError, match="500"):
        _client(http_client, worker_config).next_task(("asr_transcribe",))


def test_backoff_doubles_and_resets():
    from types import SimpleNamespace
    now = [0.0]
    cfg = SimpleNamespace(api_timeout=30.0, connect_timeout=5.0, blob_timeout=120.0)
    client = DjangoEndpointClient(BASE, cfg, http=None, tokens=object(), clock=lambda: now[0])
    client.note_failure("down")
    assert client.backoff_until == 5.0 and client.last_error == "down"
    client.note_failure("down")
    assert client.backoff_until == 10.0
    for _ in range(10):
        client.note_failure("down")
    assert client.backoff_until == 300.0
    client.note_success()
    assert (client.backoff_until, client.last_error) == (0.0, None)


def test_complete_retries_then_succeeds(fake_django, http_client, worker_config):
    wi = fake_django.enqueue("asr_transcribe", {"v": 1})
    client = _client(http_client, worker_config)
    client.next_task(("asr_transcribe",))
    fake_django.fail_complete = 2
    sleeps = []
    assert client.complete(wi, {"raw_result": "{}", "status": "done"}, sleep=sleeps.append) is True
    assert sleeps == [1, 2]
    assert fake_django.completed[wi]["status"] == "done"


def test_complete_not_in_progress_is_not_retried(fake_django, http_client, worker_config):
    sleeps = []
    assert _client(http_client, worker_config).complete("unknown", {"raw_result": ""}, sleep=sleeps.append) is False
    assert sleeps == []


def test_fetch_blob_checks_size_and_hash(fake_django, http_client, worker_config):
    client = _client(http_client, worker_config)
    ref = BlobRef(**fake_django.add_blob(b"RIFFdata", work_item="w1"))
    assert client.fetch_blob(ref, max_bytes=1000) == b"RIFFdata"
    with pytest.raises(JobError, match="sha256"):
        client.fetch_blob(ref.model_copy(update={"sha256": "0" * 64}), max_bytes=1000)
    with pytest.raises(JobError, match="exceeds"):
        client.fetch_blob(ref, max_bytes=4)
    missing = ref.model_copy(update={"path": "/api/speech/blobs/nope/content/?work_item=w1"})
    with pytest.raises(JobError, match="404"):
        client.fetch_blob(missing, max_bytes=1000)


def test_fetch_blob_never_calls_foreign_host(fake_django, http_client, worker_config):
    from types import SimpleNamespace
    client = _client(http_client, worker_config)
    ref = SimpleNamespace(path="http://evil.example/api/speech/blobs/x/content/", size_bytes=1, sha256="a" * 64)
    with pytest.raises(JobError):
        client.fetch_blob(ref, max_bytes=10)
    assert all(r.url.host == "django.test" for r in fake_django.requests)


def test_upload_blob(fake_django, http_client, worker_config):
    client = _client(http_client, worker_config, service="tts")
    spec = UploadSpec(upload_path="/api/speech/blobs/?work_item=w9", max_bytes=100)
    out = client.upload_blob(spec, b"RIFFwav", "audio/wav", "w9.wav")
    assert out["sha256"] == hashlib.sha256(b"RIFFwav").hexdigest()
    assert fake_django.uploads[0]["work_item"] == "w9" and fake_django.uploads[0]["data"] == b"RIFFwav"
    with pytest.raises(JobError, match="max_bytes"):
        client.upload_blob(spec.model_copy(update={"max_bytes": 3}), b"RIFFwav", "audio/wav", "w9.wav")


def test_auth_error_after_second_401(worker_config):
    def handler(request):
        if request.url.path == "/api/llm/register/":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(AuthError):
            DjangoEndpointClient(BASE, worker_config(), http).next_task(("asr_transcribe",))
```

- [ ] **Step 2: Run to verify failure**

Run: `RTTEST tests/services/django_worker/test_jobs.py tests/services/django_worker/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: …jobs` / `…client`.

- [ ] **Step 3: Implement `jobs.py`**

```python
# src/services/common/django_worker/jobs.py
"""Job plumbing shared by all rt-speech handlers (spec 2026-10-06 §3.2, §5.3)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.services.common.django_worker.errors import JobError

logger = logging.getLogger(__name__)
PAYLOAD_VERSION = 1


def ensure_speech_path(path: str) -> str:
    """Blob paths in payloads are RELATIVE to the endpoint the task came from and
    confined to /api/speech/ — the worker never sends its token anywhere else."""
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or not parts.path.startswith("/api/speech/") or ".." in parts.path:
        raise JobError(f"refusing blob path {path!r} (must be a relative /api/speech/ path)")
    return path


class BlobRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    content_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_s: float | None = None

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        try:
            return ensure_speech_path(value)
        except JobError as exc:
            raise ValueError(str(exc)) from exc


class UploadSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    upload_path: str
    max_bytes: int = Field(gt=0)

    @field_validator("upload_path")
    @classmethod
    def _path(cls, value: str) -> str:
        try:
            return ensure_speech_path(value)
        except JobError as exc:
            raise ValueError(str(exc)) from exc


class JobContext:
    """What a handler may do besides inference: fetch its input blob, upload its output."""

    def __init__(self, client, work_item_uuid: str, max_download_bytes: int):
        self._client = client
        self.work_item_uuid = work_item_uuid
        self._max_download_bytes = max_download_bytes

    def fetch_blob(self, ref: BlobRef) -> bytes:
        return self._client.fetch_blob(ref, self._max_download_bytes)

    def upload_blob(self, spec: UploadSpec, data: bytes, content_type: str) -> dict:
        return self._client.upload_blob(spec, data, content_type, f"{self.work_item_uuid}.wav")


JobHandler = Callable[[dict, JobContext], dict]


def _error(message: str, seconds: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"raw_result": "", "status": "error", "error_message": message}
    if seconds is not None:
        result["generation_seconds"] = seconds
    return result


def run_job(task: dict, handlers: Mapping[str, JobHandler], ctx: JobContext | None,
            clock: Callable[[], float] = time.perf_counter) -> dict:
    """Execute one claimed WorkItem; every failure becomes status=error for Django
    (its retry/deferred-parent logic decides what happens next)."""
    artifact_type = task.get("artifact_type")
    handler = handlers.get(artifact_type)
    if handler is None:
        return _error(f"JobError: no handler for artifact_type {artifact_type!r}")
    started = clock()
    try:
        payload = json.loads(task.get("prompt") or "")
        if not isinstance(payload, dict):
            raise JobError("payload is not a JSON object")
        if payload.get("v") != PAYLOAD_VERSION:
            raise JobError(f"unsupported payload version {payload.get('v')!r}")
        out = handler(payload, ctx)
    except Exception as exc:  # report every failure, never crash the loop
        logger.warning("job %s (%s) failed: %s", task.get("uuid"), artifact_type, exc, exc_info=True)
        return _error(f"{type(exc).__name__}: {exc}", clock() - started)
    result = {"v": PAYLOAD_VERSION, **out}
    return {"raw_result": json.dumps(result, ensure_ascii=False), "status": "done",
            "error_message": "", "generation_seconds": clock() - started}
```

- [ ] **Step 4: Implement `client.py`**

```python
# src/services/common/django_worker/client.py
"""HTTP client for one Django endpoint (spec 2026-10-06 django-engine-integration §5.4)."""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Callable

import httpx

from src.services.common.django_worker.auth import TokenManager
from src.services.common.django_worker.errors import AuthError, JobError, TransportError
from src.services.common.django_worker.jobs import ensure_speech_path

logger = logging.getLogger(__name__)
BACKOFF_MIN_S, BACKOFF_MAX_S = 5.0, 300.0


class DjangoEndpointClient:
    def __init__(self, base_url: str, config, http: httpx.Client, tokens: TokenManager | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.base_url = base_url.rstrip("/")
        self._http = http
        self._clock = clock
        self.tokens = tokens or TokenManager(self.base_url, config, http, clock)
        self._api_timeout = httpx.Timeout(config.api_timeout, connect=config.connect_timeout)
        self._blob_timeout = httpx.Timeout(config.blob_timeout, connect=config.connect_timeout)
        self.paused_until = 0.0
        self.backoff_until = 0.0
        self._backoff = 0.0
        self.last_error: str | None = None

    # --- health of this endpoint ---
    def blocked(self) -> bool:
        now = self._clock()
        return now < self.paused_until or now < self.backoff_until

    def note_failure(self, message: str) -> None:
        self._backoff = min(BACKOFF_MAX_S, max(BACKOFF_MIN_S, self._backoff * 2))
        self.backoff_until = self._clock() + self._backoff
        self.last_error = message
        logger.warning("endpoint %s failing (%s); backing off %.0fs", self.base_url, message, self._backoff)

    def note_success(self) -> None:
        self._backoff = 0.0
        self.backoff_until = 0.0
        self.last_error = None

    # --- transport ---
    def _send(self, method: str, path: str, *, timeout: httpx.Timeout, stream: bool = False, **kwargs) -> httpx.Response:
        for _ in range(2):
            token = self.tokens.get_token()
            request = self._http.build_request(method, self.base_url + path, timeout=timeout,
                                               headers={"Authorization": f"Bearer {token}"}, **kwargs)
            try:
                response = self._http.send(request, stream=stream)
            except httpx.HTTPError as exc:
                raise TransportError(f"{method} {path}: {exc}") from exc
            if response.status_code != 401:
                return response
            response.close()
            self.tokens.invalidate()
        raise AuthError(f"{method} {path}: 401 even after re-registration")

    # --- WorkItem API ---
    def next_task(self, artifact_types) -> dict | None:
        resp = self._send("GET", "/api/engine/work-items/next/", timeout=self._api_timeout,
                          params={"artifact_types": ",".join(artifact_types)})
        if resp.status_code == 204:
            return None
        if resp.status_code != 200:
            raise TransportError(f"/next/ HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if body.get("paused"):
            self.paused_until = self._clock() + float(body.get("retry_after", 300))
            logger.info("%s paused this service for %ss: %s", self.base_url, body.get("retry_after"), body.get("message", ""))
            return None
        return body

    def complete(self, work_item_uuid: str, result: dict, *, attempts: int = 3,
                 sleep: Callable[[float], None] = time.sleep) -> bool:
        path = f"/api/engine/work-items/{work_item_uuid}/complete/"
        for attempt in range(attempts):
            try:
                resp = self._send("POST", path, timeout=self._api_timeout, json=result)
            except (TransportError, AuthError) as exc:
                problem = str(exc)
            else:
                if resp.status_code < 300 or resp.status_code == 422:
                    return True  # 422 = Django recorded a validation failure: delivered
                if 400 <= resp.status_code < 500:
                    logger.warning("complete %s rejected (HTTP %s): %s", work_item_uuid, resp.status_code, resp.text[:200])
                    return False  # e.g. "Task is not in progress" after a stale reset — do not retry
                problem = f"HTTP {resp.status_code}"
            if attempt < attempts - 1:
                sleep(2 ** attempt)
        logger.error("complete %s failed after %s attempts: %s", work_item_uuid, attempts, problem)
        return False

    # --- blobs ---
    def fetch_blob(self, ref, max_bytes: int) -> bytes:
        path = ensure_speech_path(ref.path)
        limit = min(ref.size_bytes, max_bytes)
        resp = self._send("GET", path, timeout=self._blob_timeout, stream=True)
        try:
            if resp.status_code != 200:
                resp.read()
                raise JobError(f"blob download HTTP {resp.status_code}: {resp.text[:200]}")
            digest, chunks, total = hashlib.sha256(), [], 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > limit:
                    raise JobError(f"blob exceeds {limit} bytes")
                digest.update(chunk)
                chunks.append(chunk)
        finally:
            resp.close()
        if total != ref.size_bytes:
            raise JobError(f"blob size {total} != declared {ref.size_bytes}")
        if digest.hexdigest() != ref.sha256:
            raise JobError("blob sha256 mismatch")
        return b"".join(chunks)

    def upload_blob(self, spec, data: bytes, content_type: str, filename: str) -> dict:
        path = ensure_speech_path(spec.upload_path)
        if len(data) > spec.max_bytes:
            raise JobError(f"output of {len(data)} bytes exceeds max_bytes={spec.max_bytes}")
        resp = self._send("POST", path, timeout=self._blob_timeout, files={"file": (filename, data, content_type)})
        if resp.status_code != 201:
            raise JobError(f"blob upload HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()
```

- [ ] **Step 5: Run tests**

Run: `RTTEST tests/services/django_worker/test_jobs.py tests/services/django_worker/test_client.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/common/django_worker/jobs.py src/services/common/django_worker/client.py tests/services/django_worker/test_jobs.py tests/services/django_worker/test_client.py
git commit -m "feat(django-worker): endpoint client, blob transport and job runner"
```

### Task B4: `WorkerLoop`

**Files:**
- Create: `src/services/common/django_worker/loop.py`
- Test: `tests/services/django_worker/test_loop.py`

**Interfaces:**
- Consumes: `DjangoEndpointClient`, `run_job`, `JobContext` (B3).
- Produces: `IDLE = "idle"`, `WORKED = "worked"`; `WorkerStatus` (dataclass `running, error, done, failed, last_job_at`); `WorkerLoop(config, clients, handlers, can_accept, clock=time.monotonic)` з `run_once() -> str`, `run(stop_event)`, `status_dict() -> dict` (формат spec §5.5).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/django_worker/test_loop.py
import json
import threading

import httpx

from src.services.common.django_worker.client import DjangoEndpointClient
from src.services.common.django_worker.loop import IDLE, WORKED, WorkerLoop

BASE = "http://django.test"


def _loop(fake_django, http_client, worker_config, handlers=None, can_accept=lambda: True, clients=None):
    cfg = worker_config("asr")
    clients = clients or [DjangoEndpointClient(BASE, cfg, http_client)]
    handlers = handlers or {"asr_transcribe": lambda payload, ctx: {"text": "ok"}}
    return WorkerLoop(cfg, clients, handlers, can_accept=can_accept)


def test_processes_one_task_and_reports(fake_django, http_client, worker_config):
    wi = fake_django.enqueue("asr_transcribe", {"v": 1})
    loop = _loop(fake_django, http_client, worker_config)
    assert loop.run_once() == WORKED
    assert json.loads(fake_django.completed[wi]["raw_result"]) == {"v": 1, "text": "ok"}
    assert loop.run_once() == IDLE
    status = loop.status_dict()
    assert status["jobs"]["done"] == 1 and status["artifact_types"] == ["asr_transcribe"]
    assert status["endpoints"][0]["registered"] is True


def test_does_not_poll_when_runtime_busy(fake_django, http_client, worker_config):
    fake_django.enqueue("asr_transcribe", {"v": 1})
    assert _loop(fake_django, http_client, worker_config, can_accept=lambda: False).run_once() == IDLE
    assert fake_django.requests == []


def test_handler_failure_is_reported_as_error(fake_django, http_client, worker_config):
    wi = fake_django.enqueue("asr_transcribe", {"v": 1})

    def boom(payload, ctx):
        raise RuntimeError("cuda oom")

    loop = _loop(fake_django, http_client, worker_config, handlers={"asr_transcribe": boom})
    assert loop.run_once() == WORKED
    assert fake_django.completed[wi]["status"] == "error"
    assert "cuda oom" in fake_django.completed[wi]["error_message"]
    assert loop.status_dict()["jobs"]["failed"] == 1


def test_transport_error_backs_off_endpoint_and_tries_next(fake_django, http_client, worker_config):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    cfg = worker_config("asr")
    dead_http = httpx.Client(transport=httpx.MockTransport(down))
    dead = DjangoEndpointClient("http://dead.test", cfg, dead_http)
    alive = DjangoEndpointClient(BASE, cfg, http_client)
    wi = fake_django.enqueue("asr_transcribe", {"v": 1})
    loop = _loop(fake_django, http_client, worker_config, clients=[dead, alive])
    assert loop.run_once() == WORKED
    assert wi in fake_django.completed
    assert dead.blocked() and dead.last_error
    assert loop.status_dict()["endpoints"][0]["last_error"]


def test_round_robin_between_endpoints(fake_django, http_client, worker_config):
    cfg = worker_config("asr")
    served = []
    clients = []
    for name in ("a", "b"):
        client = DjangoEndpointClient(BASE, cfg, http_client)
        original = client.next_task

        def spy(types, _original=original, _name=name):
            task = _original(types)
            if task:
                served.append(_name)
            return task

        client.next_task = spy
        clients.append(client)
    for _ in range(2):
        fake_django.enqueue("asr_transcribe", {"v": 1})
    loop = _loop(fake_django, http_client, worker_config, clients=clients)
    loop.run_once()
    loop.run_once()
    assert served == ["a", "b"]


def test_run_stops_on_event(fake_django, http_client, worker_config):
    loop = _loop(fake_django, http_client, worker_config)
    stop = threading.Event()
    thread = threading.Thread(target=loop.run, args=(stop,), daemon=True)
    thread.start()
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert loop.status_dict()["running"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `RTTEST tests/services/django_worker/test_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: …loop`.

- [ ] **Step 3: Implement**

```python
# src/services/common/django_worker/loop.py
"""Poll → execute → complete loop over all configured Django endpoints (spec §5.3)."""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from src.services.common.django_worker.errors import AuthError, TransportError
from src.services.common.django_worker.jobs import JobContext, JobHandler, run_job

logger = logging.getLogger(__name__)
IDLE, WORKED = "idle", "worked"


@dataclass
class WorkerStatus:
    running: bool = False
    error: str | None = None
    done: int = 0
    failed: int = 0
    last_job_at: str | None = None


class WorkerLoop:
    def __init__(self, config, clients: list, handlers: Mapping[str, JobHandler],
                 can_accept: Callable[[], bool], clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.clients = clients
        self.handlers = handlers
        self._can_accept = can_accept
        self._clock = clock
        self._start = 0
        self.status = WorkerStatus()

    def run_once(self) -> str:
        if not self._can_accept():
            return IDLE
        count = len(self.clients)
        for offset in range(count):
            index = (self._start + offset) % count
            client = self.clients[index]
            if client.blocked():
                continue
            try:
                task = client.next_task(self.config.artifact_types)
            except (TransportError, AuthError) as exc:
                client.note_failure(str(exc))
                continue
            client.note_success()
            if task is None:
                continue
            self._start = (index + 1) % count  # fairness between Django instances
            ctx = JobContext(client, task["uuid"], self.config.max_download_bytes)
            result = run_job(task, self.handlers, ctx)
            if result["status"] == "done":
                self.status.done += 1
            else:
                self.status.failed += 1
            self.status.last_job_at = datetime.now(timezone.utc).isoformat()
            client.complete(task["uuid"], result)
            return WORKED
        return IDLE

    def run(self, stop_event: threading.Event) -> None:
        self.status.running = True
        logger.info("django worker started: %s → %s", self.config.artifact_types, [c.base_url for c in self.clients])
        try:
            while not stop_event.is_set():
                try:
                    outcome = self.run_once()
                except Exception:  # the loop must survive anything a single poll throws
                    logger.exception("django worker iteration failed")
                    outcome = IDLE
                if outcome == IDLE:
                    stop_event.wait(self.config.poll_interval + random.uniform(0, 0.5))
        finally:
            self.status.running = False
            logger.info("django worker stopped")

    def status_dict(self) -> dict:
        now = self._clock()

        def remaining(until: float) -> float | None:
            return round(until - now, 1) if until > now else None

        return {
            "enabled": True,
            "running": self.status.running,
            "error": self.status.error,
            "artifact_types": list(self.config.artifact_types),
            "endpoints": [
                {"url": c.base_url, "registered": c.tokens.registered, "paused_for_s": remaining(c.paused_until),
                 "backoff_for_s": remaining(c.backoff_until), "last_error": c.last_error}
                for c in self.clients
            ],
            "jobs": {"done": self.status.done, "failed": self.status.failed, "last_job_at": self.status.last_job_at},
        }
```

- [ ] **Step 4: Run tests**

Run: `RTTEST tests/services/django_worker/test_loop.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/common/django_worker/loop.py tests/services/django_worker/test_loop.py
git commit -m "feat(django-worker): polling loop with endpoint fairness, backoff and status"
```

### Task B5: `status_extras` у runtime + `hooks.py`

**Files:**
- Modify: `src/services/common/runtime.py` (`ServiceRuntime.__init__`), `src/services/common/health.py` (`/status` handler)
- Create: `src/services/common/django_worker/hooks.py`
- Modify: `src/services/common/django_worker/__init__.py` (експорт)
- Test: `tests/services/django_worker/test_hooks.py`

**Interfaces:**
- Consumes: B1–B4; SP1 `ServiceRuntime`, `ModelState`, `create_app(…, lifespan_hooks=…)`.
- Produces: `ServiceRuntime.status_extras: dict[str, Callable[[], dict]]` (злиття в `/status`); `runtime_can_accept(runtime) -> bool`; `build_django_worker_hooks(service: str, handlers_factory: Callable[[ServiceRuntime], Mapping[str, JobHandler]], environ=None, http_factory=None) -> list[hook]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/django_worker/test_hooks.py
import time

import httpx

from src.services.common.django_worker.hooks import build_django_worker_hooks, runtime_can_accept


def test_disabled_reports_and_starts_nothing(fake_runtime):
    runtime = fake_runtime(backend=object())
    (hook,) = build_django_worker_hooks("asr", lambda rt: {}, environ={})
    stop = hook(runtime)
    assert runtime.status_extras["worker"]() == {"enabled": False, "running": False, "error": None}
    stop()


def test_enabled_without_token_file_reports_error_and_starts_no_thread(fake_runtime):
    import threading
    before = threading.active_count()
    runtime = fake_runtime(backend=object())
    (hook,) = build_django_worker_hooks("asr", lambda rt: {}, environ={"RT_WORKER_ENABLED": "true", "RT_DJANGO_ENDPOINTS": "http://x"})
    hook(runtime)
    status = runtime.status_extras["worker"]()
    assert status["enabled"] is True and status["running"] is False
    assert "RT_DJANGO_BOOTSTRAP_TOKEN_FILE" in status["error"]
    assert threading.active_count() == before


def test_can_accept_requires_ready_and_idle_queue(fake_runtime):
    from src.services.common.runtime import ModelState
    runtime = fake_runtime(backend=object())
    assert runtime_can_accept(runtime) is True
    runtime.worker.pending = 1
    assert runtime_can_accept(runtime) is False
    runtime.worker.pending = 0
    runtime.state = ModelState.LOADING
    assert runtime_can_accept(runtime) is False


def test_enabled_worker_processes_task_and_stops(fake_django, fake_runtime, worker_config):
    cfg_env = {"RT_WORKER_ENABLED": "true", "RT_DJANGO_ENDPOINTS": "http://django.test",
               "RT_DJANGO_BOOTSTRAP_TOKEN_FILE": str(worker_config.token_file), "RT_WORKER_POLL_INTERVAL": "0.01"}
    wi = fake_django.enqueue("mt_translate", {"v": 1})
    runtime = fake_runtime(backend=object())
    (hook,) = build_django_worker_hooks(
        "mt", lambda rt: {"mt_translate": lambda p, ctx: {"translations": []}}, environ=cfg_env,
        http_factory=lambda: httpx.Client(transport=httpx.MockTransport(fake_django.handler)),
    )
    stop = hook(runtime)
    deadline = time.monotonic() + 5
    while wi not in fake_django.completed and time.monotonic() < deadline:
        time.sleep(0.01)
    stop()
    assert fake_django.completed[wi]["status"] == "done"
    assert runtime.status_extras["worker"]()["running"] is False
    assert fake_django.registrations[0]["kind"] == "mt"
```

Додатково — тест злиття в `/status` реальним SP1 app (скопіюй спосіб побудови app з `tests/services/conftest.py`, карта імен B0):

```python
def test_status_endpoint_includes_worker_block():
    from fastapi.testclient import TestClient
    from src.services.mt.app import create_app
    from src.services.mt.backend import FakeMtBackend
    from src.services.mt.settings import MtSettings

    settings = MtSettings.from_env({"RT_MT_BACKEND": "fake"})
    hooks = build_django_worker_hooks("mt", lambda rt: {}, environ={})
    with TestClient(create_app(settings, backend_factory=lambda *a: FakeMtBackend(), lifespan_hooks=hooks)) as client:
        body = client.get("/status").json()
    assert body["worker"] == {"enabled": False, "running": False, "error": None}
```

- [ ] **Step 2: Run to verify failure**

Run: `RTTEST tests/services/django_worker/test_hooks.py -v`
Expected: FAIL — `ModuleNotFoundError: …hooks`.

- [ ] **Step 3: Extend SP1 runtime/health (мінімально, additive)**

`src/services/common/runtime.py` — у `ServiceRuntime.__init__` додати (імпорт `Callable` з `typing`, якщо його нема):

```python
        # Extra /status blocks contributed by optional components (e.g. the Django
        # worker, spec 2026-10-06 django-engine-integration §5.5): {key: () -> dict}.
        self.status_extras: dict[str, Callable[[], dict]] = {}
```

`src/services/common/health.py` — у handler'і `/status`, після побудови словника відповіді (назвемо його `body`; використай фактичне ім'я змінної) і перед `return`:

```python
        for key, provider in runtime.status_extras.items():
            body[key] = provider()
```

Прогони тести SP1 health: `RTTEST tests/services/test_svc_runtime_health.py -v` → PASS (форма `/status` без extras не змінилась).

- [ ] **Step 4: Implement hooks**

```python
# src/services/common/django_worker/hooks.py
"""Lifespan hooks that run the Django worker inside an rt-speech service
(SP1 `lifespan_hooks`; spec 2026-10-06 django-engine-integration §5.1, §5.3)."""
from __future__ import annotations

import logging
import threading
from typing import Callable, Mapping

import httpx

from src.services.common.django_worker.client import DjangoEndpointClient
from src.services.common.django_worker.config import WorkerConfig, WorkerConfigError
from src.services.common.django_worker.jobs import JobHandler
from src.services.common.django_worker.loop import WorkerLoop

logger = logging.getLogger(__name__)


def runtime_can_accept(runtime) -> bool:
    """Take batch work only when the model is ready and the inference queue is idle
    (no preemption exists — SP1 Q5)."""
    from src.services.common.runtime import ModelState

    return runtime.state == ModelState.READY and runtime.worker.pending == 0


def _status_only_hook(block: dict):
    def hook(runtime):
        runtime.status_extras["worker"] = lambda: dict(block)
        return lambda: None
    return hook


def build_django_worker_hooks(service: str, handlers_factory: Callable[[object], Mapping[str, JobHandler]],
                              environ=None, http_factory: Callable[[], httpx.Client] | None = None) -> list:
    try:
        config = WorkerConfig.from_env(service, environ)
    except WorkerConfigError as exc:
        logger.error("Django worker NOT started: %s", exc)
        return [_status_only_hook({"enabled": True, "running": False, "error": str(exc)})]
    if not config.enabled:
        return [_status_only_hook({"enabled": False, "running": False, "error": None})]

    def worker_hook(runtime):
        http = http_factory() if http_factory else httpx.Client()
        clients = [DjangoEndpointClient(url, config, http) for url in config.endpoints]
        loop = WorkerLoop(config, clients, handlers_factory(runtime), can_accept=lambda: runtime_can_accept(runtime))
        runtime.status_extras["worker"] = loop.status_dict
        stop = threading.Event()
        thread = threading.Thread(target=loop.run, args=(stop,), name=f"django-worker-{service}", daemon=True)
        thread.start()

        def shutdown() -> None:
            stop.set()
            thread.join(timeout=10)
            http.close()

        return shutdown

    return [worker_hook]
```

Add to `__init__.py`: `from src.services.common.django_worker.hooks import build_django_worker_hooks, runtime_can_accept` + `JobContext`, `JobHandler` з `jobs` у `__all__`.

- [ ] **Step 5: Run tests**

Run: `RTTEST tests/services/django_worker/ tests/services/test_svc_runtime_health.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/common/runtime.py src/services/common/health.py src/services/common/django_worker/ tests/services/django_worker/test_hooks.py
git commit -m "feat(django-worker): lifespan hooks and /status worker block"
```

### Task B6: ASR handler

**Files:**
- Create: `src/services/asr/django_job.py`
- Test: `tests/services/django_worker/test_asr_job.py`

**Interfaces:**
- Consumes: `BlobRef`, `JobContext`, `JobError` (B3); SP1 `DecodeOptions`, `Priority`, `decode_audio_file`, `FakeAsrBackend`.
- Produces: `AsrJobPayload` (pydantic); `make_asr_handlers(runtime, settings, *, decode=decode_audio_file) -> {"asr_transcribe": handler}`; результат = spec §3.2 (без `v` — додає `run_job`).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/django_worker/test_asr_job.py
import io
import wave
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.services.asr.backend import FakeAsrBackend
from src.services.asr.django_job import make_asr_handlers
from src.services.common.django_worker.client import DjangoEndpointClient
from src.services.common.django_worker.jobs import JobContext
from src.services.common.worker import Priority


def _wav(seconds=1.0, rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


SETTINGS = SimpleNamespace(asr_beam_size=5, asr_vad=True)


def test_transcribes_downloaded_blob(fake_django, http_client, worker_config, fake_runtime):
    backend = FakeAsrBackend()
    runtime = fake_runtime(backend)
    client = DjangoEndpointClient("http://django.test", worker_config("asr"), http_client)
    ref = fake_django.add_blob(_wav(2.0), work_item="w1")
    handler = make_asr_handlers(runtime, SETTINGS)["asr_transcribe"]

    out = handler({"v": 1, "audio": ref, "language": "uk", "initial_prompt": "Привіт", "temperature": 0.0},
                  JobContext(client, "w1", 10_000_000))

    assert set(out) == {"text", "language", "duration_s", "segments", "model"}
    assert out["language"] == "uk" and out["model"] == backend.model_id
    assert out["duration_s"] == pytest.approx(2.0, abs=0.05)
    opts = backend.calls[-1]
    assert (opts.language, opts.prompt, opts.beam_size, opts.vad_filter) == ("uk", "Привіт", 5, True)
    assert runtime.priorities == [Priority.REQUEST]


def test_rejects_bad_payload(fake_runtime):
    handler = make_asr_handlers(fake_runtime(FakeAsrBackend()), SETTINGS)["asr_transcribe"]
    with pytest.raises(ValidationError):
        handler({"v": 1, "audio": {"path": "http://evil/x", "content_type": "audio/wav", "size_bytes": 1, "sha256": "a" * 64}}, None)
```

- [ ] **Step 2: Run to verify failure**

Run: `RTTEST tests/services/django_worker/test_asr_job.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.asr.django_job'`.

- [ ] **Step 3: Implement**

```python
# src/services/asr/django_job.py
"""asr_transcribe WorkItem handler (spec 2026-10-06 django-engine-integration §3.2)."""
from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.services.asr.backend import DecodeOptions
from src.services.common.audio import decode_audio_file
from src.services.common.django_worker.jobs import BlobRef, JobContext
from src.services.common.worker import Priority


class AsrJobPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    v: Literal[1]
    audio: BlobRef
    language: str | None = None
    initial_prompt: str | None = None
    temperature: float = Field(0.0, ge=0.0, le=1.0)


def make_asr_handlers(runtime, settings, *, decode: Callable[[bytes], object] = decode_audio_file) -> dict:
    def handle(payload: dict, ctx: JobContext) -> dict:
        job = AsrJobPayload.model_validate(payload)
        audio = decode(ctx.fetch_blob(job.audio))
        opts = DecodeOptions(language=job.language, prompt=job.initial_prompt, temperature=job.temperature,
                             beam_size=settings.asr_beam_size, vad_filter=settings.asr_vad)
        backend = runtime.backend
        result = runtime.submit_threadsafe(backend.transcribe, audio, opts, priority=Priority.REQUEST).result()
        return {
            "text": result.text,
            "language": result.language,
            "duration_s": result.duration,
            "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in result.segments],
            "model": backend.model_id,
        }

    return {"asr_transcribe": handle}
```

- [ ] **Step 4: Run tests**

Run: `RTTEST tests/services/django_worker/test_asr_job.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/asr/django_job.py tests/services/django_worker/test_asr_job.py
git commit -m "feat(asr): asr_transcribe Django job handler"
```

### Task B7: MT handler

**Files:**
- Create: `src/services/mt/django_job.py`
- Test: `tests/services/django_worker/test_mt_job.py`

**Interfaces:**
- Consumes: `JobError` (B1); SP1 `FakeMtBackend`, `to_nllb` (карта B0), `Priority`.
- Produces: `MtJobPayload`; `make_mt_handlers(runtime, settings, *, to_nllb=to_nllb) -> {"mt_translate": handler}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/django_worker/test_mt_job.py
from types import SimpleNamespace

import pytest

from src.services.common.django_worker.errors import JobError
from src.services.mt.backend import FakeMtBackend
from src.services.mt.django_job import make_mt_handlers

SETTINGS = SimpleNamespace(mt_num_beams=4, mt_max_chars=20, mt_max_batch=2)
CODES = {"uk": "ukr_Cyrl", "en": "eng_Latn"}


def _handler(runtime):
    return make_mt_handlers(runtime, SETTINGS, to_nllb=lambda code: CODES[code])["mt_translate"]


def test_translates_in_microbatches(fake_runtime):
    backend = FakeMtBackend()
    runtime = fake_runtime(backend)
    out = _handler(runtime)({"v": 1, "texts": ["a", "b", "c"], "source_lang": "uk", "target_lang": "en", "num_beams": None}, None)
    assert out == {"translations": ["[eng_Latn] a", "[eng_Latn] b", "[eng_Latn] c"],
                   "source_lang": "uk", "target_lang": "en", "model": backend.model_id}
    assert len(runtime.priorities) == 2  # 2 + 1 with mt_max_batch=2


def test_item_too_long(fake_runtime):
    with pytest.raises(JobError, match="mt_max_chars"):
        _handler(fake_runtime(FakeMtBackend()))({"v": 1, "texts": ["x" * 21], "source_lang": "uk", "target_lang": "en"}, None)


def test_unknown_language(fake_runtime):
    with pytest.raises(KeyError):
        _handler(fake_runtime(FakeMtBackend()))({"v": 1, "texts": ["a"], "source_lang": "xx", "target_lang": "en"}, None)
```

- [ ] **Step 2: Run to verify failure**

Run: `RTTEST tests/services/django_worker/test_mt_job.py -v` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# src/services/mt/django_job.py
"""mt_translate WorkItem handler (spec 2026-10-06 django-engine-integration §3.2)."""
from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.services.common.django_worker.errors import JobError
from src.services.common.worker import Priority
from src.services.mt.languages import to_nllb as _to_nllb


class MtJobPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    v: Literal[1]
    texts: list[str] = Field(min_length=1)
    source_lang: str
    target_lang: str
    num_beams: int | None = Field(None, ge=1, le=8)


def make_mt_handlers(runtime, settings, *, to_nllb: Callable[[str], str] = _to_nllb) -> dict:
    def handle(payload: dict, ctx) -> dict:
        job = MtJobPayload.model_validate(payload)
        too_long = [i for i, text in enumerate(job.texts) if len(text) > settings.mt_max_chars]
        if too_long:
            raise JobError(f"texts{too_long} exceed mt_max_chars={settings.mt_max_chars}")
        src, tgt = to_nllb(job.source_lang), to_nllb(job.target_lang)
        beams = job.num_beams or settings.mt_num_beams
        backend = runtime.backend
        translations: list[str] = []
        for start in range(0, len(job.texts), settings.mt_max_batch):
            chunk = job.texts[start:start + settings.mt_max_batch]
            translations += runtime.submit_threadsafe(backend.translate, chunk, src, tgt, beams,
                                                      priority=Priority.REQUEST).result()
        if len(translations) != len(job.texts):
            raise JobError(f"backend returned {len(translations)} translations for {len(job.texts)} texts")
        return {"translations": translations, "source_lang": job.source_lang,
                "target_lang": job.target_lang, "model": backend.model_id}

    return {"mt_translate": handle}
```

(Якщо `FakeMtBackend` форматує як `f"[{tgt}] {text}"` з NLLB-кодом — очікування тесту правильне; інакше підстав фактичний формат із `src/services/mt/backend.py`.)

- [ ] **Step 4: Run tests** — `RTTEST tests/services/django_worker/test_mt_job.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/mt/django_job.py tests/services/django_worker/test_mt_job.py
git commit -m "feat(mt): mt_translate Django job handler"
```

### Task B8: TTS handler (вибір голосу + upload)

**Files:**
- Create: `src/services/tts/django_job.py`
- Test: `tests/services/django_worker/test_tts_job.py`

**Interfaces:**
- Consumes: `UploadSpec`, `JobContext`, `JobError` (B3); SP1 `FakeTtsBackend`, `Voice`, `wav_bytes`, `resample`, `Priority`.
- Produces: `TtsJobPayload`; `pick_voice(voices, lang, requested, default) -> str`; `make_tts_handlers(runtime, settings, *, encode_wav=wav_bytes, resample_fn=resample) -> {"tts_synthesize": handler}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/django_worker/test_tts_job.py
from types import SimpleNamespace

import pytest

from src.services.common.django_worker.client import DjangoEndpointClient
from src.services.common.django_worker.errors import JobError
from src.services.common.django_worker.jobs import JobContext
from src.services.tts.backend import FakeTtsBackend, Voice
from src.services.tts.django_job import make_tts_handlers, pick_voice

VOICES = [Voice("af_heart", "en-us", "female"), Voice("pf_dora", "pt-br", "female"), Voice("ef_dora", "es", "female")]
SETTINGS = SimpleNamespace(tts_voice="af_heart", tts_max_chars=50)


@pytest.mark.parametrize("lang,requested,expected", [
    ("en", None, "af_heart"), ("pt", None, "pf_dora"), ("es", None, "ef_dora"), ("en", "bf_emma", "bf_emma"),
])
def test_pick_voice(lang, requested, expected):
    assert pick_voice(VOICES, lang, requested, "af_heart") == expected


def test_pick_voice_no_match():
    with pytest.raises(JobError, match="no TTS voice"):
        pick_voice(VOICES, "uk", None, "af_heart")


def test_synthesizes_and_uploads(fake_django, http_client, worker_config, fake_runtime):
    backend = FakeTtsBackend()
    runtime = fake_runtime(backend)
    client = DjangoEndpointClient("http://django.test", worker_config("tts"), http_client)
    handler = make_tts_handlers(runtime, SETTINGS)["tts_synthesize"]
    out = handler({"v": 1, "text": "Hello there.", "lang": "en", "voice": None, "speed": 1.0, "format": "wav",
                   "sample_rate": 24000, "output": {"upload_path": "/api/speech/blobs/?work_item=w3", "max_bytes": 10_000_000}},
                  JobContext(client, "w3", 1000))
    upload = fake_django.uploads[0]
    assert upload["work_item"] == "w3" and upload["content_type"] == "audio/wav" and upload["data"][:4] == b"RIFF"
    assert out["audio"]["blob_id"] == upload["blob_id"]
    assert out["audio"]["size_bytes"] == len(upload["data"]) and out["audio"]["sample_rate"] == 24000
    assert out["audio"]["duration_s"] > 0
    assert (out["voice"], out["model"]) == ("af_heart", backend.model_id)


def test_text_too_long(fake_runtime):
    handler = make_tts_handlers(fake_runtime(FakeTtsBackend()), SETTINGS)["tts_synthesize"]
    with pytest.raises(JobError, match="tts_max_chars"):
        handler({"v": 1, "text": "x" * 51, "lang": "en", "output": {"upload_path": "/api/speech/blobs/?work_item=w", "max_bytes": 10}}, None)
```

- [ ] **Step 2: Run to verify failure** — `RTTEST tests/services/django_worker/test_tts_job.py -v` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# src/services/tts/django_job.py
"""tts_synthesize WorkItem handler (spec 2026-10-06 django-engine-integration §3.2, §5.6)."""
from __future__ import annotations

from typing import Callable, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from src.services.common.audio import resample
from src.services.common.django_worker.errors import JobError
from src.services.common.django_worker.jobs import JobContext, UploadSpec
from src.services.common.worker import Priority
from src.services.tts.encoding import wav_bytes


class TtsJobPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    v: Literal[1]
    text: str = Field(min_length=1)
    lang: str
    voice: str | None = None
    speed: float = Field(1.0, ge=0.25, le=4.0)
    format: Literal["wav"] = "wav"
    sample_rate: int = Field(24000, ge=8000, le=48000)
    output: UploadSpec


def pick_voice(voices, lang: str, requested: str | None, default: str) -> str:
    """Explicit voice wins; else the configured default if it speaks `lang`;
    else the first catalog voice of that language (Kokoro: 'en' → 'en-us')."""
    if requested:
        return requested
    lang = lang.lower()

    def speaks(voice) -> bool:
        language = voice.language.lower()
        return language == lang or language.startswith(f"{lang}-")

    by_id = {v.id: v for v in voices}
    if default in by_id and speaks(by_id[default]):
        return default
    for voice in voices:
        if speaks(voice):
            return voice.id
    raise JobError(f"no TTS voice for lang {lang!r}")


def make_tts_handlers(runtime, settings, *, encode_wav: Callable = wav_bytes, resample_fn: Callable = resample) -> dict:
    def handle(payload: dict, ctx: JobContext) -> dict:
        job = TtsJobPayload.model_validate(payload)
        if len(job.text) > settings.tts_max_chars:
            raise JobError(f"text of {len(job.text)} chars exceeds tts_max_chars={settings.tts_max_chars}")
        backend = runtime.backend
        voice = pick_voice(backend.voices(), job.lang, job.voice, settings.tts_voice)

        def synthesize() -> np.ndarray:
            chunks = list(backend.synthesize(job.text, voice, job.speed))
            return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

        audio = runtime.submit_threadsafe(synthesize, priority=Priority.REQUEST).result()
        if backend.sample_rate != job.sample_rate:
            audio = resample_fn(audio, backend.sample_rate, job.sample_rate)
        data = encode_wav(audio, job.sample_rate)
        uploaded = ctx.upload_blob(job.output, data, "audio/wav")
        return {
            "audio": {"blob_id": uploaded["blob_id"], "content_type": "audio/wav",
                      "size_bytes": uploaded["size_bytes"], "sha256": uploaded["sha256"],
                      "duration_s": round(len(audio) / job.sample_rate, 3), "sample_rate": job.sample_rate},
            "voice": voice,
            "model": backend.model_id,
        }

    return {"tts_synthesize": handle}
```

- [ ] **Step 4: Run tests** — `RTTEST tests/services/django_worker/test_tts_job.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/tts/django_job.py tests/services/django_worker/test_tts_job.py
git commit -m "feat(tts): tts_synthesize Django job handler with language-aware voice"
```

### Task B9: підключення в `__main__` + e2e через `FakeDjango`

**Files:**
- Modify: `src/services/asr/__main__.py`, `src/services/mt/__main__.py`, `src/services/tts/__main__.py`
- Test: `tests/services/django_worker/test_e2e_fake_django.py`

**Interfaces:**
- Consumes: усе з B1–B8.
- Produces: сервіс з `RT_WORKER_ENABLED=true` реєструється, поллить і виконує свої artifact_types.

- [ ] **Step 1: Write the e2e test**

```python
# tests/services/django_worker/test_e2e_fake_django.py
"""ASR → MT → TTS through the wire contract: Django's chaining is simulated by
feeding each result into the next payload exactly as speech/services/payloads.py does."""
import io
import json
import wave
from types import SimpleNamespace

from src.services.asr.backend import FakeAsrBackend
from src.services.asr.django_job import make_asr_handlers
from src.services.common.django_worker.client import DjangoEndpointClient
from src.services.common.django_worker.loop import WORKED, WorkerLoop
from src.services.mt.backend import FakeMtBackend
from src.services.mt.django_job import make_mt_handlers
from src.services.tts.backend import FakeTtsBackend
from src.services.tts.django_job import make_tts_handlers

BASE = "http://django.test"


def _wav(seconds=1.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def _set_payload(fake_django, work_item, payload):
    """Payloads reference their own WorkItem uuid (blob grant / upload path), so set them after enqueue."""
    next(t for t in fake_django.tasks if t["uuid"] == work_item)["prompt"] = json.dumps(payload)


def _loop(service, handlers, fake_django, http_client, worker_config):
    cfg = worker_config(service)
    return WorkerLoop(cfg, [DjangoEndpointClient(BASE, cfg, http_client)], handlers, can_accept=lambda: True)


def test_three_services_chain(fake_django, http_client, worker_config, fake_runtime):
    asr = _loop("asr", make_asr_handlers(fake_runtime(FakeAsrBackend()), SimpleNamespace(asr_beam_size=5, asr_vad=False)),
                fake_django, http_client, worker_config)
    mt = _loop("mt", make_mt_handlers(fake_runtime(FakeMtBackend()), SimpleNamespace(mt_num_beams=4, mt_max_chars=2000, mt_max_batch=64),
                                      to_nllb=lambda c: {"uk": "ukr_Cyrl", "en": "eng_Latn"}[c]),
               fake_django, http_client, worker_config)
    tts = _loop("tts", make_tts_handlers(fake_runtime(FakeTtsBackend()), SimpleNamespace(tts_voice="af_heart", tts_max_chars=4096)),
                fake_django, http_client, worker_config)

    asr_wi = fake_django.enqueue("asr_transcribe", {})
    _set_payload(fake_django, asr_wi, {"v": 1, "audio": fake_django.add_blob(_wav(), work_item=asr_wi), "language": "uk"})

    assert mt.run_once() != WORKED and tts.run_once() != WORKED  # nobody else claims ASR work
    assert asr.run_once() == WORKED
    asr_result = json.loads(fake_django.completed[asr_wi]["raw_result"])
    assert asr_result["v"] == 1 and asr_result["text"]

    texts = [s["text"] for s in asr_result["segments"]] or [asr_result["text"]]
    mt_wi = fake_django.enqueue("mt_translate", {"v": 1, "texts": texts, "source_lang": asr_result["language"], "target_lang": "en", "num_beams": None})
    assert mt.run_once() == WORKED
    translations = json.loads(fake_django.completed[mt_wi]["raw_result"])["translations"]
    assert len(translations) == len(texts)

    tts_wi = fake_django.enqueue("tts_synthesize", {})
    _set_payload(fake_django, tts_wi, {"v": 1, "text": " ".join(translations), "lang": "en", "voice": None, "speed": 1.0,
                                       "format": "wav", "sample_rate": 24000,
                                       "output": {"upload_path": f"/api/speech/blobs/?work_item={tts_wi}", "max_bytes": 209715200}})
    assert tts.run_once() == WORKED
    tts_result = json.loads(fake_django.completed[tts_wi]["raw_result"])
    assert tts_result["audio"]["blob_id"] == fake_django.uploads[0]["blob_id"]
    assert fake_django.uploads[0]["work_item"] == tts_wi
    assert {r["kind"] for r in fake_django.registrations} == {"asr", "mt", "tts"}
```

- [ ] **Step 2: Run** — `RTTEST tests/services/django_worker/test_e2e_fake_django.py -v` → PASS (усі компоненти вже є; падіння = розбіжність контракту між B6–B8 — виправити у відповідній задачі).

- [ ] **Step 3: Wire `__main__`**

У кожному `src/services/<svc>/__main__.py::main()` — там, де викликається `create_app(settings…)`, передати hooks (приклад для ASR; MT/TTS — те саме з `"mt"`/`make_mt_handlers` і `"tts"`/`make_tts_handlers`):

```python
from src.services.asr.django_job import make_asr_handlers
from src.services.common.django_worker import build_django_worker_hooks

    hooks = build_django_worker_hooks("asr", lambda runtime: make_asr_handlers(runtime, settings))
    app = create_app(settings, lifespan_hooks=hooks)
```

Smoke (без Django): `RT_ASR_BACKEND=fake RT_PORT=18091 nix develop --command timeout 5 python -m src.services.asr; true` у іншому терміналі `curl -s localhost:18091/status | python -m json.tool` → `"worker": {"enabled": false, …}`.

- [ ] **Step 4: Full suite**

Run: `RTTEST tests/services -v` → PASS (SP1 + django_worker).

- [ ] **Step 5: Commit**

```bash
git add src/services/asr/__main__.py src/services/mt/__main__.py src/services/tts/__main__.py tests/services/django_worker/test_e2e_fake_django.py
git commit -m "feat(services): run the Django worker in rt-asr/rt-mt/rt-tts when RT_WORKER_ENABLED"
```

---

# Part C — живий e2e (після Part A, Part B, підпроєктів 2–3)

### Task C1: розгортання і живий доказ

Не автоматизується тестами; кожен крок, що змінює живу систему, — **з явного дозволу власника**.

- [ ] **Step 1 (Django, власник):** з `~/repos/swarm-nix`: `DJMANAGE migrate engine 0078` → `DJMANAGE migrate llm_services 0007` → `DJMANAGE migrate speech` → `sudo systemctl restart obsidian-django.service` → `DJMANAGE bootstrap_blueprints --worker-types` → `DJMANAGE bootstrap_blueprints --workflows`. Для `obsidian-django.service` задати `SPEECH_STORAGE_ROOT` (напр. `/var/lib/obsidian-django/speech`) — інфра-зміна в `machines/<host>/modules/flake-services/django-obsidian-automation.nix` (підпроєкт 3 / власник).
- [ ] **Step 2 (сервіси):** на хості rt-asr/mt/tts (підпроєкт 3) — файл bootstrap-токена (clusterops `Secret` → sops, ADR-055) **по одному на сервіс**, `file_owner = "rt-<svc>"`, `file_mode = "0400"`, `target_path = "/run/secrets/rt-django-bootstrap-<svc>"` (system-user сервісу має читати файл сам — `LoadCredential` модуля SP2 покриває лише `apiTokenFile`); env — через `services.rtSpeech.<svc>.extraEnv = { RT_WORKER_ENABLED = "true"; RT_DJANGO_ENDPOINTS = "http://<cyborg-tailscale>:8000"; RT_DJANGO_BOOTSTRAP_TOKEN_FILE = "/run/secrets/rt-django-bootstrap-<svc>"; };` у `machines/<dir>/modules/flake-services/rt-speech.nix` → colmena/`nixos-rebuild` (секрет) → deploy-rs (env). Рекомендація spec §5.3: для важкого batch — окремий інстанс, на realtime-інстансі воркер вимкнений. Перевір: `curl -s http://<host>:8091/status | jq .worker` → `running: true`, `endpoints[0].registered: true`; у Django `/workers/` з'явились `…-rt-asr|mt|tts` з `kind` asr/mt/tts і статусом `online` після `sync_all_services`.
- [ ] **Step 3 (доказ):** `DJMANAGE speech_submit ~/sample-uk.wav --blueprint speech_translate_audio --language uk --target-lang en` → `AnalysisRun N`. Спостерігати `/engine/queue/` до завершення трьох кроків; завантажити результат як staff: `GET /api/speech/blobs/<blob_id>/content/` (blob_id — з `parsed_result.audio.blob_id` кроку `speech_synthesize`) і прослухати.
- [ ] **Step 4 (регресія llama):** за час Step 3 жоден llama-воркер не отримав `audio`/`mt` WorkItem: `DJMANAGE shell -c "from engine.models import WorkItem; print(WorkItem.objects.filter(worker_type__in=['audio','mt']).values_list('status', 'validation_error'))"` — усі `done`, без помилок «LLM»-типу; llama-черга обробляється як раніше.
- [ ] **Step 5:** зафіксувати результат у ADR-057 ("Живий e2e: дата, хости, тривалість кроків") і закрити задачу в `context/tasks.md` (архівація — `archive_tasks`).


---

## Self-review (виконано при написанні плану)

- **Покриття spec:** §3.1 → A1, A2; §3.2 → A10, B3, B6–B8; §3.3 → A4; §3.4 → A3; §3.5 → A5; §3.6 → A8–A12; §3.7 → A5, A10, A11, B1; §3.8 → A6, A7; §3.9 → B1, B2, B3, A9; §4 → A13, B9; §5 → B0–B9; §7 → тести в кожній задачі + A13/B9/C1; §8 → A14; живий доказ (критерій успіху §0) → C1.
- **Узгодженість імен:** `get_structured_worker_types`, `parse_artifact_types`, `apply_claim_scope`, `resolve_deferred_parent_waiting_on`, `overdue_claims`, `SERVICE_KINDS`, `store_blob/get_live_blob/blob_content_path/blob_upload_path`, `build_*_job`, `handle_speech_step`, `validate_*`; сервісний бік — `WorkerConfig`, `TokenManager`, `DjangoEndpointClient`, `BlobRef/UploadSpec/JobContext/run_job`, `WorkerLoop`, `build_django_worker_hooks`, `make_*_handlers` — однакові в Interfaces і коді.
- **Залежність від SP1:** імена, не зафіксовані в SP1-спеці, винесені в карту Task B0; решта коду Part B від них не залежить.
