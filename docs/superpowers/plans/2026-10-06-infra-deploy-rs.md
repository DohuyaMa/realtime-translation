# Infra deploy-rs integration (rt-asr / rt-mt / rt-tts) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Задеплоїти `rt-asr`, `rt-mt`, `rt-tts` у swarm-nix так само, як `llama-service`: colmena ставить NixOS-модуль, deploy-rs — код у `/var/lib/rt-<svc>/current`, registry генерується clusterops з Django DB, код rt-репо приходить лише з `github:DohuyaMa/realtime-translation` через закомічений `flake.lock`.

**Architecture:** Overlay rt-flake у єдиний `pkgs` (`system-conf/flake.nix`) → `pkgs.rtSpeech.<svc>.<variant>`; один хелпер `applications/lib/mkRtSpeechApplication.nix` + по файлу на clusterops Service у `applications/services/`; `mkDeployProfiles` отримує generic hook `app.envLinesFor hostConfig` (llama мігрує на нього byte-identically); per-host `services.rtSpeech.*` у `machines/<dir>/modules/flake-services/rt-speech.nix`. Перед створенням Service виправляються три баги clusterops (importer перекласифікує `rt-*-gpu` у `llm-gpu`; `_profile_name`; шлях генерації registry).

**Tech Stack:** Nix flakes (nixos-unstable), deploy-rs, colmena, NixOS modules, sops-nix; Django 5 + DRF (clusterops), pytest-django.

**Spec:** `/home/dmaslo/repos/real-time-transletor/docs/superpowers/specs/2026-10-06-infra-deploy-rs-design.md` (далі «spec»); контракт — `/home/dmaslo/repos/real-time-transletor/docs/superpowers/specs/2026-10-06-distributed-speech-services-overview.md` («overview»).

## Global Constraints

- Імена/порти/шляхи: `rt-asr`:8091:`/var/lib/rt-asr`, `rt-mt`:8092:`/var/lib/rt-mt`, `rt-tts`:8093:`/var/lib/rt-tts`; профілі/Service: `rt-{asr,mt,tts}-{cpu,gpu,rocm}`; variant→device: `cpu→cpu`, `gpu→cuda`, `rocm→rocm` (overview §4.1).
- rt-код потрапляє в інфру **лише** як `realtime-translation.url = "github:DohuyaMa/realtime-translation"` з ревізією в закоміченому `system-conf/flake.lock`. Жодних `path:`/`git+file:` inputs і `--override-input` ні в файлах, ні в командах деплою (overview §4.5a). Release: push rt → `nix flake update realtime-translation` → commit+push `flake.lock` → `git pull` на контролері → deploy.
- `applications/registry.nix`, `machines/inventory.nix`, `machines/*/secrets*.{yaml,nix}` — AUTO-GENERATED; руками не редагувати, лише через clusterops.
- Перед будь-яким деплоєм: `nix flake update` → `nix flake check` (або `--no-build`, spec Q4) → commit lock → деплой (`.claude/rules/nix.md` §5). Збірка — на таргеті (`--remote-build`, `--build-on-target`). Self-deploy cyborg — `sudo nixos-rebuild switch --flake .#cyborg`, не colmena.
- Порядок на хості: colmena/nixos-rebuild (модуль) **до** першого deploy-rs.
- Секрети — лише файлом (clusterops `Secret`, delivery sops); ніколи в Nix store/git.
- Firewall: `openFirewall = false`, `host = "0.0.0.0"`; доступ лише через trusted `tailscale0` (`common/modules/security/firewall.nix`).
- swarm-nix: код/коментарі/коміти англійською; **без** рядків `Co-Authored-By` у комітах (пам'ять користувача). Django-тести — через `nix develop ./flake` з кореня swarm-nix; ніколи `pip install`.
- Тести Django не чіпають живі рядки `applications/registry.nix`/`machines/inventory.nix` у `NixFile` (TD-18) — лише `_uniq`-шляхи/імена.
- Llama-профілі мають лишитись byte-identical (`drvPath` до/після, Task 5).

## Review Focus

1. **`import_infra` після появи `rt-asr-gpu` у registry** — очікується, що Service лишається `custom` з тим самим `config`; тест у Task 1 (`test_import_all_keeps_existing_service_type_and_config`, `test_import_all_new_rt_name_is_custom`).
2. **Рефакторинг env-hook міняє llama** — очікується незмінний `drvPath` п'яти llama-профілів і toplevel п'яти хостів; перевірка в Task 5 Step 7 (diff baseline).
3. **Хост у registry, але `services.rtSpeech.<svc>` вимкнено / інший `device` / `host` = tailscale-IP** — очікується eval-`throw` з іменем хоста, не тихий деплой; перевірка в Task 7 Step 6.
4. **Модуль відкриває 8091–8093 на всіх інтерфейсах** — очікується, що `networking.firewall.allowedTCPPorts` хоста їх не містить, а з LAN-IP порт недоступний; Task 7 Step 5 і Task 9 Step 8.
5. **Контролер з іншою ревізією rt або lock з `path:`** — очікується `type = github` і однаковий `rev` на cyborg і SKTOP-NOTE27-NIXOS; Task 5 Step 8 і Task 9 Step 1.

---

## File Structure

| Файл (swarm-nix) | Дія | Відповідальність |
|---|---|---|
| `django_api/clusterops/services/nix_importer.py` | Modify | `_infer_service_type()`; `import_all` не перезаписує тип/config існуючих Service |
| `django_api/clusterops/services/deploy_service.py` | Modify | `_profile_name` = `service.name` |
| `django_api/clusterops/serializers.py` | Modify | `flake_module` у `ServiceSerializer` |
| `django_api/clusterops/services/nix_generator/io.py` | Modify | ключ `applications/registry.nix`; `GENERATED_FILES`; `regenerate_generated_file()` |
| `django_api/clusterops/services/hostname_rename.py` | Modify | B3-цикл через `regenerate_generated_file()` |
| `django_api/clusterops/management/commands/generate_infra.py` | Modify | `--regenerate <path>` |
| `django_api/clusterops/tests/{test_nixgen,test_deploy_service,test_api}.py` | Modify | тести |
| `system-conf/flake.nix` | Modify | input `realtime-translation`, overlay у `pkgs` |
| `system-conf/flake.lock` | Modify (via `nix flake lock/update`) | пін ревізії |
| `system-conf/applications/lib/mkEnvFile.nix` | Modify | generic `lines -> drv` |
| `system-conf/applications/lib/mkDeployProfiles.nix` | Modify | hook `app.envLinesFor` |
| `system-conf/applications/llm/env.nix` | Create | llama `envLinesFor` |
| `system-conf/applications/llm/{cpu,gpu,rocm}.nix` | Modify | `mergeEnvFromLlamaService` → `envLinesFor` |
| `system-conf/applications/lib/mkRtSpeechApplication.nix` | Create | rt deploy-rs app builder + `envLinesFor` з перевірками |
| `system-conf/applications/services/rt-<svc>-<variant>.nix` | Create (≤9) | по файлу на ім'я Service |
| `system-conf/common/modules/rt-speech.nix` | Create | імпорт `inputs.realtime-translation.nixosModules.rt-speech` |
| `system-conf/common/modules/nix/cuda-substituter.nix` | Create | `cache.nixos-cuda.org` у системних `nix.settings` CUDA-хостів (Task 7a) |
| `system-conf/machines/{cyborg,SKTOP-NOTE27-NIXOS}/configuration.nix` | Modify | імпорт `cuda-substituter.nix` (Task 7a) |
| `system-conf/machines/{cyborg,sktop-nixos-top}/modules/flake-services/rt-speech.nix` (+ fallback MT: `sktop-note27-nixos` або `kira-kiosk`) | Create | per-host `services.rtSpeech.*` |
| `system-conf/machines/{cyborg,sktop-nixos-top}/configuration.nix` (+ fallback MT-хост) | Modify | 2 імпорти |
| `system-conf/machines/cyborg/system.nix` | Modify | прибрати стару HM-згадку (Task 4), edge HM (Task 12) |
| застарілі артефакти (spec §10) | Delete/Modify | Task 4 |
| `system-conf/AGENTS.md`, `system-conf/machines/README.md`, `.claude/rules/nix.md`, `system-conf/README.md`, `system-conf/context/tasks.md` | Modify | документація фактів, що змінились |

Змінні оболонки, що використовуються далі (визначати на початку кожної shell-сесії; `TOKEN`/`API`/`SVC` — з Task 8 Step 1–2):
```bash
SN=/home/dmaslo/repos/swarm-nix
SC=$SN/system-conf
DEVSH() { (cd "$SN" && nix develop ./flake --command bash -c "cd django_api && $*"); }
```

---

## Phase 1 — clusterops (branch `rt-speech-clusterops`)

```bash
cd $SN && git switch -c rt-speech-clusterops
```

### Task 1: Importer не перекласифіковує rt-сервіси

**Files:**
- Modify: `django_api/clusterops/services/nix_importer.py` (`import_all`, новий `_infer_service_type`)
- Test: `django_api/clusterops/tests/test_nixgen.py` (клас `NixImporterImportAllTest`, новий `InferServiceTypeTest`)

**Interfaces:**
- Produces: `clusterops.services.nix_importer._infer_service_type(name: str) -> str` (`"llm-cpu" | "llm-gpu" | "llm-rocm" | "custom"`); `import_all` створює Service лише для нових імен і не змінює `service_type`/`config` існуючих.

- [ ] **Step 1: Write the failing tests** — додати в кінець `NixImporterImportAllTest` і новий клас після нього:

```python
    def test_import_all_new_rt_name_is_custom(self):
        svc_name = _uniq("rt-asr-gpu")  # contains "gpu" but is not an llm service
        registry_fake = {svc_name: [{"hostname": _uniq("host"), "sshUser": "deploy", "user": "root"}]}
        imp = NixImporter(system_conf_path=tempfile.gettempdir())
        with mock.patch("clusterops.services.nix_importer._nix_eval_json",
                        side_effect=[{}, registry_fake]):
            imp.import_all()
        self.assertEqual(Service.objects.get(name=svc_name).service_type, "custom")

    def test_import_all_keeps_existing_service_type_and_config(self):
        svc_name = _uniq("rt-tts-cpu")
        Service.objects.create(name=svc_name, service_type="custom",
                               config={"keep": 1}, status="active")
        registry_fake = {svc_name: [{"hostname": _uniq("host"), "sshUser": "deploy", "user": "root"}]}
        imp = NixImporter(system_conf_path=tempfile.gettempdir())
        with mock.patch("clusterops.services.nix_importer._nix_eval_json",
                        side_effect=[{}, registry_fake]):
            imp.import_all()
        svc = Service.objects.get(name=svc_name)
        self.assertEqual(svc.service_type, "custom")
        self.assertEqual(svc.config, {"keep": 1})


class InferServiceTypeTest(SimpleTestCase):
    def test_llm_names(self):
        from clusterops.services.nix_importer import _infer_service_type
        self.assertEqual(_infer_service_type("llm-gpu"), "llm-gpu")
        self.assertEqual(_infer_service_type("zzqvex-llm-rocm-1a2b3c4d"), "llm-rocm")
        self.assertEqual(_infer_service_type("llm-cpu-extra"), "llm-cpu")

    def test_non_llm_names(self):
        from clusterops.services.nix_importer import _infer_service_type
        for name in ("rt-asr-gpu", "rt-mt-cpu", "rt-tts-rocm", "llm-cpux", "myllm-gpu"):
            self.assertEqual(_infer_service_type(name), "custom", name)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DEVSH "python -m pytest clusterops/tests/test_nixgen.py -k 'ImportAll or InferServiceType' -v"`
Expected: FAIL — `rt-asr-gpu` → `'llm-gpu' != 'custom'`, `ImportError: cannot import name '_infer_service_type'`, config `{} != {'keep': 1}`.

- [ ] **Step 3: Implement** — у `nix_importer.py` до блоку імпортів додати `import re`, а після `NIX_BIN = "nix"`:

```python
# An llm-<variant> token as its own dash-separated segment ("llm-gpu",
# "zzqvex-llm-cpu-1a2b"), never a bare substring — "rt-asr-gpu" is a
# custom deploy-rs app, not an LLM (spec 2026-10-06-infra-deploy-rs §5.5).
_LLM_TYPE_SEGMENT = re.compile(r"(?:^|-)(llm-(?:cpu|gpu|rocm))(?:-|$)")


def _infer_service_type(name: str) -> str:
    """Service type for a registry name seen for the FIRST time (bootstrap
    import). Existing rows keep their DB type — DB is the source of truth,
    registry.nix is generated from it."""
    m = _LLM_TYPE_SEGMENT.search(name)
    return m.group(1) if m else "custom"
```

і в `import_all` замінити блок від `service_type = "llm-cpu" if "cpu" in svc_name else (` до кінця `Service.objects.update_or_create(...)` на:

```python
            svc = Service.objects.filter(name=svc_name).first()
            if svc is None:
                svc = Service.objects.create(
                    name=svc_name,
                    service_type=_infer_service_type(svc_name),
                    config={},
                    status="active",
                )
            elif svc.status != "active":
                svc.status = "active"
                svc.save(update_fields=["status"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `DEVSH "python -m pytest clusterops/tests/test_nixgen.py -k 'ImportAll or InferServiceType or Importer' -v"`
Expected: PASS (включно з наявним `test_import_all_creates_node_service_and_deploy_target`, де ім'я `zzqvex-llm-cpu-<hex>` → `llm-cpu`).

- [ ] **Step 5: Commit**

```bash
cd $SN && git add django_api/clusterops/services/nix_importer.py django_api/clusterops/tests/test_nixgen.py
git commit -m "fix(clusterops): import_infra no longer reclassifies existing services by name substring

rt-asr-gpu matched \"gpu\" and became llm-gpu, so NixGen rendered
./llm/gpu.nix under its key and deploys used profile llm-gpu. Existing
Service rows keep service_type/config (DB is SSOT); new names are llm-*
only when llm-<variant> is a dash segment of the name."
```

### Task 2: deploy-rs профіль = `service.name`; `flake_module` в API

**Files:**
- Modify: `django_api/clusterops/services/deploy_service.py:119-127`
- Modify: `django_api/clusterops/serializers.py:101-110`
- Test: `django_api/clusterops/tests/test_deploy_service.py:66-91`, `django_api/clusterops/tests/test_api.py` (`ServiceAPITestCase`)

**Interfaces:**
- Produces: `deploy_service._profile_name(service) -> service.name` для всіх типів; `POST /api/clusterops/services/` приймає `flake_module`.

- [ ] **Step 1: Write the failing tests** — у `DeployServiceDispatchTestCase` додати:

```python
    @patch("clusterops.services.deploy_service.deploy_executor.start_deploy_run", side_effect=_fake_start)
    def test_deploy_rs_profile_is_service_name_for_llm_type(self, mock_start):
        # registry.nix keys are always Service.name (nix_generator.generate_registry)
        target = self._target(Service.DeployBackend.DEPLOY_RS, Service.ServiceType.LLM_GPU)
        event = deploy_service.deploy(target)
        self.assertEqual(mock_start.call_args.kwargs["profile"], target.service.name)
        self.assertEqual(event.profile_name, target.service.name)
```

у `test_deploy_rs_backend_addresses_node_by_name_not_machine_dir` замінити обидва `"llm-gpu"` (`mock_start.call_args.kwargs["profile"]` і `event.profile_name`) на `target.service.name`. У `ServiceAPITestCase` додати:

```python
    def test_create_service_accepts_flake_module(self):
        name = f"zzqvex-rt-asr-gpu-{uuid.uuid4().hex[:8]}"
        resp = self.client.post("/api/clusterops/services/", {
            "name": name, "service_type": "custom", "deploy_backend": "deploy_rs",
            "state_dir": "/var/lib/rt-asr", "service_name": "rt-asr",
            "flake_module": "applications/services/rt-asr-gpu.nix",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(Service.objects.get(name=name).flake_module,
                         "applications/services/rt-asr-gpu.nix")
```
(додати `import uuid` у `test_api.py`, якщо його нема).

- [ ] **Step 2: Run to verify fail**

Run: `DEVSH "python -m pytest clusterops/tests/test_deploy_service.py clusterops/tests/test_api.py -k 'profile or flake_module or machine_dir' -v"`
Expected: FAIL — `'llm-gpu' != 'zzqvex-svc-…'`; `flake_module` `'' != 'applications/services/rt-asr-gpu.nix'`.

- [ ] **Step 3: Implement**

```python
def _profile_name(service):
    """deploy-rs profile name == applications/registry.nix key ==
    Service.name for every service type (generate_registry keys entries by
    svc.name). Returning service_type for LLM rows only worked while their
    names happened to equal their types."""
    return service.name
```
У `ServiceSerializer.Meta.fields` додати `'flake_module'` після `'magic_rollback_enabled'`.

- [ ] **Step 4: Run to verify pass**

Run: `DEVSH "python -m pytest clusterops/tests/test_deploy_service.py clusterops/tests/test_api.py -v"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $SN && git add django_api/clusterops/services/deploy_service.py django_api/clusterops/serializers.py django_api/clusterops/tests/test_deploy_service.py django_api/clusterops/tests/test_api.py
git commit -m "fix(clusterops): deploy-rs profile is always Service.name; expose flake_module in API"
```

### Task 3: Точкова регенерація `applications/registry.nix`

**Files:**
- Modify: `django_api/clusterops/services/nix_generator/io.py` (`generate_all` ключ; нові `GENERATED_FILES`, `regenerate_generated_file`)
- Modify: `django_api/clusterops/services/hostname_rename.py:258-265`
- Modify: `django_api/clusterops/management/commands/generate_infra.py`
- Test: `django_api/clusterops/tests/test_nixgen.py` (`test_generate_all_writes_both_files`, новий `RegenerateGeneratedFileTest`)

**Interfaces:**
- Produces: `clusterops.services.nix_generator.io.GENERATED_FILES: dict[str, str]` = `{"machines/inventory.nix": "generate_inventory", "applications/registry.nix": "generate_registry"}`; `NixGenerator.regenerate_generated_file(path: str, method: str) -> NixFile` (raises `NixFile.DoesNotExist`); CLI `manage.py generate_infra --regenerate applications/registry.nix`.

- [ ] **Step 1: Write the failing tests**

У `test_generate_all_writes_both_files` замінити `self.assertIn("registry.nix", results)` на `self.assertIn("applications/registry.nix", results)`. Додати клас:

```python
class RegenerateGeneratedFileTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = f"applications/zzqvex-reg-{uuid.uuid4().hex[:8]}.nix"
        self.nf = NixFile.objects.create(path=self.path, source="OLD",
                                         kind=NixFile.NixFileKind.REGISTRY)

    def test_regenerate_writes_db_and_disk(self):
        with override_settings(COLMENA_DIR=self.tmp):
            nf = NixGenerator(system_conf_path=self.tmp).regenerate_generated_file(
                self.path, "generate_registry")
        self.assertIn("{ pkgs, lib }:", nf.source)
        with open(os.path.join(self.tmp, self.path)) as f:
            self.assertEqual(f.read(), nf.source)

    def test_regenerate_missing_row_raises(self):
        with override_settings(COLMENA_DIR=self.tmp):
            with self.assertRaises(NixFile.DoesNotExist):
                NixGenerator(system_conf_path=self.tmp).regenerate_generated_file(
                    "applications/zzqvex-missing.nix", "generate_registry")

    def test_command_regenerate_only_touches_that_file(self):
        from django.core.management import call_command
        from clusterops.services.nix_generator import io as gen_io
        with override_settings(COLMENA_DIR=self.tmp), \
             mock.patch.dict(gen_io.GENERATED_FILES, {self.path: "generate_registry"}, clear=True), \
             mock.patch.object(NixGenerator, "generate_nix_files") as mock_all:
            call_command("generate_infra", "--system-conf", self.tmp, "--regenerate", self.path)
        mock_all.assert_not_called()
        self.nf.refresh_from_db()
        self.assertNotEqual(self.nf.source, "OLD")

    def test_command_rejects_unknown_path(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("generate_infra", "--system-conf", self.tmp, "--regenerate", "flake.nix")
```

- [ ] **Step 2: Run to verify fail**

Run: `DEVSH "python -m pytest clusterops/tests/test_nixgen.py -k 'RegenerateGeneratedFile or generate_all_writes' -v"`
Expected: FAIL — `AttributeError: ... regenerate_generated_file`, `unrecognized arguments: --regenerate`, `'applications/registry.nix' not found`.

- [ ] **Step 3: Implement** — `io.py`: у `generate_all` `results["registry.nix"]` → `results["applications/registry.nix"]`; після `logger = ...`:

```python
# Generated files that live as NixFile rows and are rebuilt from the DB on
# demand (hostname rename Phase B3, `generate_infra --regenerate`).
GENERATED_FILES = {
    "machines/inventory.nix": "generate_inventory",
    "applications/registry.nix": "generate_registry",
}
```
у класі `NixGenerator`:

```python
    def regenerate_generated_file(self, path: str, method: str):
        """Rebuild one generated file from the DB: NixFile.source <- generator
        output, then write it to disk (drift.write_to_disk). Never touches any
        other NixFile (unlike generate_all -> generate_nix_files). Raises
        NixFile.DoesNotExist if the row is missing — run import_infra first."""
        from clusterops.models import NixFile
        from clusterops.services import drift

        nf = NixFile.objects.get(path=path)
        nf.source = getattr(self, method)()
        nf.save()
        drift.write_to_disk(nf)
        return nf
```
`hostname_rename.py` B3-цикл:

```python
    gen = NixGenerator()
    for _path, _method in _GENERATED_FILES:
        if NixFile.objects.filter(path=_path).exists():
            gen.regenerate_generated_file(_path, _method)
```
`generate_infra.py`: в `add_arguments` —
```python
        parser.add_argument("--regenerate", metavar="PATH",
                            help="Rebuild only this generated file from the DB "
                                 "(one of nix_generator.io.GENERATED_FILES) and write it")
```
на початку `handle` після створення `gen`:
```python
        if options["regenerate"]:
            from django.core.management.base import CommandError
            from clusterops.services.nix_generator.io import GENERATED_FILES
            path = options["regenerate"]
            if path not in GENERATED_FILES:
                raise CommandError(f"--regenerate: {path!r} is not a generated file "
                                   f"({', '.join(sorted(GENERATED_FILES))})")
            gen.regenerate_generated_file(path, GENERATED_FILES[path])
            self.stdout.write(self.style.SUCCESS(f"  Regenerated {path}"))
            return
```

- [ ] **Step 4: Run all touched suites**

Run: `DEVSH "python -m pytest clusterops/tests/test_nixgen.py clusterops/tests/test_hostname_rename.py clusterops/tests/test_deploy_service.py clusterops/tests/test_api.py -v"`
Expected: PASS.

- [ ] **Step 5: Commit, merge, roll out Django on both controllers**

```bash
cd $SN && git add django_api/clusterops
git commit -m "fix(clusterops): registry generated at applications/registry.nix; add generate_infra --regenerate

generate_all keyed the registry as \"registry.nix\", so generate_infra --write
wrote a stray system-conf/registry.nix. Hostname-rename's targeted regen is
now NixGenerator.regenerate_generated_file, reused by the new CLI flag
(which never calls generate_nix_files)."
git switch main && git merge --ff-only rt-speech-clusterops && git push
sudo systemctl restart obsidian-django.service            # cyborg (code runs from checkout)
ssh SKTOP-NOTE27-NIXOS 'cd ~/repos/swarm-nix && git pull --ff-only && sudo systemctl restart obsidian-django.service'
```
Expected: `systemctl is-active obsidian-django.service` = `active` на обох. (Шлях checkout-у на note27 перевірити: `systemctl cat obsidian-django.service | grep WorkingDirectory`.)

---

## Phase 2 — system-conf (branch `rt-speech-infra`)

```bash
cd $SN && git switch -c rt-speech-infra
```

### Task 4: Прибрати застарілі rt/wyoming-артефакти

**Files:**
- Delete: `system-conf/machines/cyborg/modules/services/realtime-translation.nix`, `system-conf/machines/sktop-note27-nixos/modules/realtime-translation.nix`, `system-conf/common/modules/services/wyoming-faster-whisper.nix`
- Modify: `system-conf/common/nixos-config.nix:12`, `system-conf/common/nixos-base.nix:11-13`, `system-conf/machines/cyborg/system.nix:11-18`, `system-conf/machines/sktop-note27-nixos/README.md:16`, `system-conf/machines/sktop-note27-nixos/modules/README.md:11`, `system-conf/todo/todo.md:1-91`, `system-conf/README.md:261-264`, `system-conf/context/tasks.md` (Wave 2c), `django_api/context/technical-debt.md` (TD-119)

**Interfaces:** нічого не споживає; після задачі в swarm-nix немає згадок `realtime-translation.nix`, `wyoming-faster-whisper.nix`, `path:/home/dmaslo/repos/real-time-transletor`.

- [ ] **Step 1: Baseline eval (поведінка хостів не має змінитись)**

```bash
mkdir -p /tmp/rt-infra-baseline && cd $SC
for h in cyborg SKTOP-NOTE27-NIXOS SKTOP-NOTE17-NIXOS sktop-nixos-top kira-kiosk; do
  echo "$h $(nix eval --raw ".#nixosConfigurations.\"$h\".config.system.build.toplevel.drvPath")"
done > /tmp/rt-infra-baseline/toplevel-before-cleanup.txt
```

- [ ] **Step 2: Видалити файли**

```bash
cd $SN && git rm system-conf/machines/cyborg/modules/services/realtime-translation.nix \
  system-conf/machines/sktop-note27-nixos/modules/realtime-translation.nix \
  system-conf/common/modules/services/wyoming-faster-whisper.nix
```

- [ ] **Step 3: Прибрати згадки**
  - `common/nixos-config.nix`: видалити рядок `#./modules/services/wyoming-faster-whisper.nix`.
  - `common/nixos-base.nix`: коментар над `nixpkgs.overlays` замінити на
    ```nix
    # One file per logical overlay in ./overlays/, all listed explicitly here.
    # cuda.nix is deliberately NOT enabled: ~30-60 min build, enable only once a
    # consumer actually needs a fleet-wide CUDA ctranslate2/torch (TASK-CLUSTER-07).
    # Speech services (rt-asr/rt-mt/rt-tts) ship their own CUDA builds via
    # pkgs.rtSpeech (flake.nix overlay), not via this file.
    ```
  - `machines/cyborg/system.nix`: блок `home-manager.users.dmaslo` →
    ```nix
      home-manager.users.dmaslo = {
        imports = [ ./home/default.nix ];
      };
    ```
  - note27 `README.md:16` і `modules/README.md:11`: видалити рядок `realtime-translation.nix`.
  - `todo/todo.md`: видалити все від `# TODO: real-time-transletor на skotop-note27-nixos` до рядка перед `# Yggdrasil mesh — PoC та крос-провайдерне з'єднання` (разом із `---`-роздільником); Yggdrasil-секцію не чіпати.
  - `README.md:261-264`: прибрати `# 1. Оновити lock (обов'язково!)` + `nix flake lock --update-input realtime-translation` + `# 2.`-префікс, лишивши `sudo nixos-rebuild switch --flake .#cyborg`.
  - `context/tasks.md` Wave 2c: замінити тіло RTT-1..4 на
    ```markdown
    > **Superseded (2026-10-06):** speech services are rt-asr/rt-mt/rt-tts deploy-rs apps —
    > spec/plan in the real-time-transletor repo:
    > `docs/superpowers/specs/2026-10-06-infra-deploy-rs-design.md`,
    > `docs/superpowers/plans/2026-10-06-infra-deploy-rs.md`. Edge (UI/capture/playback)
    > stays Home Manager on the mic machine.
    ```
  - `technical-debt.md` TD-119: заголовок доповнити ` — **закрито 2026-10-06 (superseded)**`, тіло замінити двома реченнями: Wyoming faster-whisper не деплоїться (модуль видалено; speech — rt-asr через deploy-rs, CUDA всередині `pkgs.rtSpeech`); `common/overlays/cuda.nix` лишається під TASK-CLUSTER-07.

- [ ] **Step 4: Verify**

```bash
cd $SN && grep -rn "wyoming-faster-whisper\|realtime-translation.nix\|update-input realtime-translation\|path:/home/dmaslo/repos/real-time-transletor" \
  --include=*.nix --include=*.md system-conf django_api/context .claude | grep -v "docs/superpowers" ; echo "exit=$?"
cd $SC && for h in cyborg SKTOP-NOTE27-NIXOS SKTOP-NOTE17-NIXOS sktop-nixos-top kira-kiosk; do
  echo "$h $(nix eval --raw ".#nixosConfigurations.\"$h\".config.system.build.toplevel.drvPath")"
done | diff /tmp/rt-infra-baseline/toplevel-before-cleanup.txt - && echo IDENTICAL
python3 $SN/scripts/check_instruction_budget.py
```
Expected: grep без збігів (`exit=1`), `IDENTICAL` (видалені файли не імпортувались), budget OK.

- [ ] **Step 5: Commit**

```bash
cd $SN && git add -A system-conf django_api/context/technical-debt.md
git commit -m "chore(system-conf): remove unused realtime-translation and wyoming modules

None was imported; speech services move to rt-asr/rt-mt/rt-tts deploy-rs
apps from github:DohuyaMa/realtime-translation. Drops the stale path-input
TODO (code is consumed from git only), closes TD-119."
```

### Task 5: Flake input + overlay + generic env-merge hook (llama byte-identical)

**Files:**
- Modify: `system-conf/flake.nix:7-48`, `system-conf/flake.lock`
- Modify: `system-conf/applications/lib/mkEnvFile.nix`, `system-conf/applications/lib/mkDeployProfiles.nix`, `system-conf/applications/llm/{cpu,gpu,rocm}.nix`
- Create: `system-conf/applications/llm/env.nix`

**Interfaces:**
- Consumes: rt-flake `overlays.default` (spec R1), `pkgs.rtSpeech.lib.envLines` (spec §11.1).
- Produces: `pkgs.rtSpeech.{asr,mt,tts}.<variant>` у всіх eval-ах swarm-nix; контракт запису registry `app.envLinesFor :: hostConfig -> [string] | null`; `mkEnvFile :: [string] -> drv` (`$out/env`).

- [ ] **Step 1: Gate — upstream exports (без цього не починати)**

```bash
nix eval --json --impure --expr '
  let f = builtins.getFlake "github:DohuyaMa/realtime-translation";
      p = import f.inputs.nixpkgs { system = "x86_64-linux"; config.allowUnfree = true;
                                    overlays = [ f.overlays.default ]; };
  in { variants = builtins.mapAttrs (_: v: builtins.attrNames v) (removeAttrs p.rtSpeech [ "lib" "mkBundle" "edge" ]);
       lib = builtins.attrNames p.rtSpeech.lib;
       modules = builtins.attrNames f.nixosModules;
       flakeLib = builtins.attrNames f.lib.rtSpeech; }' | tee /tmp/rt-infra-baseline/upstream.json
```
Expected: `variants` = `{ asr = [cpu gpu rocm]; mt = [cpu gpu rocm]; tts = [cpu gpu]; }` (без `tts.rocm` — SP2 D2; зберегти, він визначає файли Task 6; `rocm` може бути відсутнім, якщо SP2 позначив його `broken`), `lib` ∋ `envLines`, `modules` ∋ `rt-speech`, `flakeLib` ∋ `envLines`. Інакше — STOP: підпроєкт 2 не готовий (вимоги R1–R7 spec §12).

- [ ] **Step 2: Baseline drvPath llama-профілів і toplevel**

```bash
cd $SC
for p in kira-kiosk/llm-cpu sktop-nixos-top/llm-cpu cyborg/llm-gpu SKTOP-NOTE27-NIXOS/llm-gpu SKTOP-NOTE17-NIXOS/llm-rocm; do
  h=${p%/*}; a=${p#*/}
  echo "$p $(nix eval --raw ".#deploy.nodes.\"$h\".profiles.\"$a\".path.drvPath")"
done > /tmp/rt-infra-baseline/llama-before.txt
for h in cyborg SKTOP-NOTE27-NIXOS SKTOP-NOTE17-NIXOS sktop-nixos-top kira-kiosk; do
  echo "$h $(nix eval --raw ".#nixosConfigurations.\"$h\".config.system.build.toplevel.drvPath")"
done > /tmp/rt-infra-baseline/toplevel-before.txt
```

- [ ] **Step 3: flake.nix** — у `inputs` після `sops-nix`:

```nix
    # Speech services (rt-asr/rt-mt/rt-tts). Consumed ONLY from git: the
    # revision is pinned in flake.lock and must be committed — every deploy
    # controller (cyborg, SKTOP-NOTE27-NIXOS) resolves the same rev. Never a
    # path:/git+file: input or --override-input (overview §4.5a).
    # Release: push there -> `nix flake update realtime-translation` -> commit lock.
    realtime-translation = {
      url = "github:DohuyaMa/realtime-translation";
      inputs.nixpkgs.follows = "nixpkgs";
    };
```
і `pkgs`:
```nix
      # rt overlay only ADDS pkgs.rtSpeech (spec R1) — registry.nix sees just
      # { pkgs, lib }, so this is how applications/services/rt-*.nix reach the
      # packages; the same pkgs feeds nixosSystem and colmena.
      pkgs = import nixpkgs {
        inherit system;
        config = pkgsConfig;
        overlays = [ inputs.realtime-translation.overlays.default ];
      };
```
Якщо `nix flake metadata github:DohuyaMa/realtime-translation --json | jq '.locks.nodes.root.inputs'` показує `home-manager` — додати `inputs.home-manager.follows = "home-manager";` поруч з `nixpkgs.follows`.

- [ ] **Step 4: Lock лише новий input**

Run: `cd $SC && nix flake lock && git diff --stat flake.lock`
Expected: у `flake.lock` додано лише вузол `realtime-translation` (і його не-follows inputs); ревізії `nixpkgs` та інших **не** змінились (`git diff flake.lock | grep '"rev"' ` — лише нові рядки).

- [ ] **Step 5: Generic env-file + hook**

`applications/lib/mkEnvFile.nix` (повністю):
```nix
# /system-conf/applications/lib/mkEnvFile.nix
#
# Renders env lines (from an app's `envLinesFor hostConfig`, see
# mkDeployProfiles.nix) into a systemd EnvironmentFile at $out/env. Merged
# into the deploy-rs bundle so `EnvironmentFile = "-${deployRsPath}/env"`
# picks up the host's configuration.nix on a deploy-rs-only push.
# Generic since 2026-10-06 (was llama-only); the option->env mapping stays
# with each app (llama: common/modules/llm/lib/env-lines.nix,
# rt-speech: pkgs.rtSpeech.lib.envLines).
{ pkgs, lib }:

lines:

pkgs.writeTextDir "env" (lib.concatStringsSep "\n" lines + "\n")
```
`applications/llm/env.nix` (новий):
```nix
# /system-conf/applications/llm/env.nix
# envLinesFor hook for the llm-* apps: this host's services.llamaService
# rendered by the module's own mapping (common/modules/llm/lib/env-lines.nix).
# null when the host does not import the llama module -> no env merged.
{ lib }:

hostConfig:

let
  cfg = hostConfig.services.llamaService or null;
in
if cfg == null then null
else import ../../common/modules/llm/lib/env-lines.nix { inherit lib; } cfg
```
У кожному `applications/llm/{cpu,gpu,rocm}.nix` рядок `mergeEnvFromLlamaService = true;` замінити на `envLinesFor = import ./env.nix { inherit lib; };` (коментар над ним лишити, слово `mergeEnvFromLlamaService` у коментарях замінити на `envLinesFor`).

`applications/lib/mkDeployProfiles.nix`: у шапці абзац про `mergeEnvFromLlamaService` замінити на
```nix
#     nixosConfigurations — flake.nix's per-host NixOS evaluations; when an
#                           app entry has `envLinesFor` (hostConfig -> [line] | null),
#                           its package is merged with an `env` file rendered
#                           from THIS host's own config (mkEnvFile.nix) — so
#                           deploy-rs always reflects configuration.nix,
#                           per-host, without a colmena apply first.
```
у `let` прибрати нічого, а в `builtins.mapAttrs (name: entry: let … in` замінити `hostLlamaCfg = …;` і `package = …;` на:
```nix
        hostConfig = nixosConfigurations.${hostname}.config;
        envLines =
          if entry.app ? envLinesFor then entry.app.envLinesFor hostConfig else null;
        package =
          if envLines != null
          then pkgs.symlinkJoin {
            name = "${entry.app.name}-${hostname}-with-env";
            paths = [ entry.app.package (mkEnvFile envLines) ];
          }
          else entry.app.package;
```

- [ ] **Step 6: Eval-check**

Run: `cd $SC && git add -A applications flake.nix flake.lock && nix flake check --no-build`
Expected: PASS (лише відомі warnings про невідомий output `applications`).

- [ ] **Step 7: Verify byte-identical**

```bash
cd $SC
for p in kira-kiosk/llm-cpu sktop-nixos-top/llm-cpu cyborg/llm-gpu SKTOP-NOTE27-NIXOS/llm-gpu SKTOP-NOTE17-NIXOS/llm-rocm; do
  h=${p%/*}; a=${p#*/}
  echo "$p $(nix eval --raw ".#deploy.nodes.\"$h\".profiles.\"$a\".path.drvPath")"
done | diff /tmp/rt-infra-baseline/llama-before.txt - && echo LLAMA-IDENTICAL
for h in cyborg SKTOP-NOTE27-NIXOS SKTOP-NOTE17-NIXOS sktop-nixos-top kira-kiosk; do
  echo "$h $(nix eval --raw ".#nixosConfigurations.\"$h\".config.system.build.toplevel.drvPath")"
done | diff /tmp/rt-infra-baseline/toplevel-before.txt - && echo TOPLEVEL-IDENTICAL
```
Expected: `LLAMA-IDENTICAL` і `TOPLEVEL-IDENTICAL`. Різниця в toplevel = overlay rt-flake змінює наявні пакети (порушення R1) → STOP, issue в rt-репо; різниця лише в llama = помилка в Step 5.

- [ ] **Step 8: Verify the lock is git-only**

Run: `cd $SC && nix flake metadata --json | jq -r '.locks.nodes."realtime-translation".locked | "\(.type) \(.owner)/\(.repo) \(.rev)"'`
Expected: `github DohuyaMa/realtime-translation <40-hex>`.

- [ ] **Step 9: Commit**

```bash
cd $SN && git add system-conf/flake.nix system-conf/flake.lock system-conf/applications
git commit -m "feat(system-conf): add realtime-translation input/overlay and generic deploy-rs env hook

pkgs gains pkgs.rtSpeech (overlay only adds attrs; all llm-* profile and
host toplevel drvPaths unchanged). mergeEnvFromLlamaService becomes the
generic app.envLinesFor hostConfig hook; llama implements it in
applications/llm/env.nix."
```

### Task 6: rt deploy-rs app builder + app-файли

**Files:**
- Create: `system-conf/applications/lib/mkRtSpeechApplication.nix`
- Create: `system-conf/applications/services/rt-<svc>-<variant>.nix` — по одному на кожну пару `svc × variant`, присутню в `/tmp/rt-infra-baseline/upstream.json` `.variants` (до 9)
- Create: `system-conf/common/modules/rt-speech.nix`

**Interfaces:**
- Consumes: `mkApplication { name, serviceName, appDir, stateDir, healthCheck }`; `pkgs.rtSpeech.<svc>.<variant>`; `pkgs.rtSpeech.lib.envLines :: svc -> cfg -> [string]`; module options `services.rtSpeech.<svc>.{enable,device,host}`.
- Produces: registry-app `{ name = "rt-<svc>-<variant>"; serviceName = "rt-<svc>"; stateDir = "/var/lib/rt-<svc>"; package; activationScript; envLinesFor; }`; NixOS-модуль `common/modules/rt-speech.nix`.

- [ ] **Step 1: `applications/lib/mkRtSpeechApplication.nix`**

```nix
# /system-conf/applications/lib/mkRtSpeechApplication.nix
#
# deploy-rs app for one speech service variant (rt-asr/rt-mt/rt-tts x
# cpu/gpu/rocm) from github:DohuyaMa/realtime-translation (pkgs.rtSpeech via
# the flake.nix overlay). The systemd unit/user/stateDir come from that
# repo's nixosModules.rt-speech (imported per host via
# common/modules/rt-speech.nix); deploy-rs only swaps /var/lib/rt-<svc>/current.
#
# envLinesFor turns silent runtime mismatches into eval errors: the host
# must enable the service, with the device matching this variant, bound to
# loopback or any (the health check below uses 127.0.0.1).
{ pkgs, lib }:

{ svc, variant }:

let
  mkApplication = import ./mkApplication.nix { inherit pkgs lib; };
  unit = "rt-${svc}";
  stateDir = "/var/lib/${unit}";
  device = { cpu = "cpu"; gpu = "cuda"; rocm = "rocm"; }.${variant};
in
mkApplication {
  name = "rt-${svc}-${variant}";
  serviceName = unit;
  appDir = pkgs.rtSpeech.${svc}.${variant};
  inherit stateDir;
  healthCheck = {
    # Port comes from the merged per-host env (services.rtSpeech.<svc>.port),
    # so a per-host port override needs no change here.
    command = ''
      port=$(sed -n 's/^RT_PORT=//p' ${stateDir}/current/env)
      [ -n "$port" ] || { echo "RT_PORT missing in ${stateDir}/current/env"; exit 1; }
      curl -sf "http://127.0.0.1:$port/health" > /dev/null
    '';
  };
} // {
  envLinesFor = hostConfig:
    let
      cfg = hostConfig.services.rtSpeech.${svc} or null;
      host = hostConfig.networking.hostName;
    in
    if cfg == null || !cfg.enable then
      throw "rt-${svc}-${variant} targets ${host}, but services.rtSpeech.${svc}.enable is not true there (machines/<dir>/modules/flake-services/rt-speech.nix)"
    else if cfg.device != device then
      throw "rt-${svc}-${variant} (device ${device}) targets ${host}, but services.rtSpeech.${svc}.device = ${cfg.device}"
    else if !(builtins.elem cfg.host [ "127.0.0.1" "0.0.0.0" ]) then
      throw "rt-${svc} on ${host}: host must be 127.0.0.1 or 0.0.0.0 (deploy-rs health check uses loopback), got ${cfg.host}"
    else pkgs.rtSpeech.lib.envLines svc cfg;
}
```

- [ ] **Step 2: App-файли** — для кожної пари з `upstream.json` (приклад для `asr`/`gpu`; інші — заміна `asr`/`gpu` в імені файлу, коментарі й аргументах):

```nix
# /system-conf/applications/services/rt-asr-gpu.nix
# deploy-rs app for clusterops Service "rt-asr-gpu" (NixGen imports
# ./services/<Service.name>.nix — file name must equal the Service name).
{ pkgs, lib }:
import ../lib/mkRtSpeechApplication.nix { inherit pkgs lib; } { svc = "asr"; variant = "gpu"; }
```
Згенерувати всі одним циклом:
```bash
cd $SC && mkdir -p applications/services
for pair in $(jq -r '.variants | to_entries[] | .key as $s | .value[] | "\($s):\(.)"' /tmp/rt-infra-baseline/upstream.json); do
  s=${pair%:*}; v=${pair#*:}
  cat > applications/services/rt-$s-$v.nix <<EOF
# /system-conf/applications/services/rt-$s-$v.nix
# deploy-rs app for clusterops Service "rt-$s-$v" (NixGen imports
# ./services/<Service.name>.nix — file name must equal the Service name).
{ pkgs, lib }:
import ../lib/mkRtSpeechApplication.nix { inherit pkgs lib; } { svc = "$s"; variant = "$v"; }
EOF
done
ls applications/services
```

- [ ] **Step 3: `common/modules/rt-speech.nix`**

```nix
# /system-conf/common/modules/rt-speech.nix
# NixOS module for rt-asr/rt-mt/rt-tts (system units, users, stateDirs) from
# github:DohuyaMa/realtime-translation. Import per host next to that host's
# ./modules/flake-services/rt-speech.nix (same pattern as llm/llama-service.nix).
{ inputs, ... }:
{
  imports = [ inputs.realtime-translation.nixosModules.rt-speech ];
}
```

- [ ] **Step 4: Eval the apps (без registry)**

```bash
cd $SC && git add -A applications common/modules/rt-speech.nix
nix eval --json --impure --expr '
  let sc = builtins.getFlake (toString ./.);
      pkgs = import sc.inputs.nixpkgs { system = "x86_64-linux"; config.allowUnfree = true;
                                        overlays = [ sc.inputs.realtime-translation.overlays.default ]; };
      names = builtins.filter (n: builtins.match "rt-.*\\.nix" n != null)
                (builtins.attrNames (builtins.readDir ./applications/services));
      ev = n: let a = import (./applications/services + "/${n}") { inherit pkgs; lib = pkgs.lib; };
              in { inherit (a) name serviceName stateDir; drv = a.package.drvPath;
                   hc = pkgs.lib.hasInfix "RT_PORT" a.activationScript; };
  in map ev names'
```
Expected: для кожного файлу `name = "rt-<svc>-<variant>"`, `serviceName = "rt-<svc>"`, `stateDir = "/var/lib/rt-<svc>"`, `drv` — шлях `/nix/store/…drv`, `hc = true`.

- [ ] **Step 5: Commit**

```bash
cd $SN && git add system-conf/applications system-conf/common/modules/rt-speech.nix
git commit -m "feat(system-conf): rt-speech deploy-rs app builder and per-Service app files"
```

### Task 7: cyborg — `services.rtSpeech.asr` (T1)

**Files:**
- Create: `system-conf/machines/cyborg/modules/flake-services/rt-speech.nix`
- Modify: `system-conf/machines/cyborg/configuration.nix` (imports)

**Interfaces:**
- Consumes: `common/modules/rt-speech.nix` (Task 6); module options overview §4.4.
- Produces: `nixosConfigurations.cyborg.config.services.rtSpeech.asr` (enable, device `cuda`, host `0.0.0.0`) — споживається `envLinesFor` для профілю `cyborg.rt-asr-gpu`.

- [ ] **Step 1: Per-host файл** — `model` — alias реєстру підпроєкту 5 (`docs/superpowers/specs/2026-10-06-model-optimization-design.md` §4.3/§4.8; довільні HF id сервіс не приймає, SP5 D5): `"whisper-large-v3-turbo"` / `"int8_float16"` (або виміряне рішення SP5 Task 19, напр. `whisper-small`):

```nix
# system-conf/machines/cyborg/modules/flake-services/rt-speech.nix
# Speech services on cyborg (deploy-rs app: rt-asr-gpu).
# NOTE: deliberately no top-level `enable` — flake-services/default.nix only
# forwards systemd.services/timers/deployment.keys of files with
# `enable = true` and would silently drop services.rtSpeech. This is a real
# NixOS module, imported explicitly from ../../configuration.nix.
{ ... }:
{
  services.rtSpeech.asr = {
    enable = true;
    deployRsPath = "/var/lib/rt-asr/current";
    # Bound on all interfaces but reachable only via tailscale0, which is
    # trusted fleet-wide (common/modules/security/firewall.nix); every other
    # interface stays firewalled, so no port is opened here.
    host = "0.0.0.0";
    openFirewall = false;
    device = "cuda";
    # VRAM budget (sub-project 5 §4.6, infra spec §7): llama (Qwen3.5-4B Q4,
    # ctx 16384) + rt-asr + rt-mt on the RTX 4060 8 GB, gate <= 7.4 GiB.
    model = "whisper-large-v3-turbo";   # sub-project 5 registry alias
    computeType = "int8_float16";
  };
}
```

- [ ] **Step 2: Імпорти** — у `machines/cyborg/configuration.nix` після `../../common/modules/llm/llama-service.nix`:

```nix
    ../../common/modules/rt-speech.nix
```
і після `./modules/flake-services/llama-service.nix`:
```nix
    ./modules/flake-services/rt-speech.nix
```

- [ ] **Step 3: Build toplevel**

Run: `cd $SC && git add -A machines/cyborg && nix build --no-link .#nixosConfigurations.cyborg.config.system.build.toplevel`
Expected: успіх (rt-пакети в toplevel не входять — лише юніт з `ExecStart` на `/var/lib/rt-asr/current`).

- [ ] **Step 4: Unit shape**

```bash
cd $SC
nix eval --json .#nixosConfigurations.cyborg.config.systemd.services.rt-asr.serviceConfig.EnvironmentFile
nix eval --json .#nixosConfigurations.cyborg.config.systemd.services.rt-asr.unitConfig
nix eval --json .#nixosConfigurations.cyborg.config.users.users.rt-asr.isSystemUser
```
Expected: `EnvironmentFile` містить `"-/var/lib/rt-asr/current/env"`; `unitConfig.ConditionPathExists` вказує в `/var/lib/rt-asr/current/…` (R5); `true`. Відсутність `ConditionPathExists` → issue в rt-репо (R5), не обходити тут.

- [ ] **Step 5: Firewall не відкрито глобально**

Run: `cd $SC && nix eval --json .#nixosConfigurations.cyborg.config.networking.firewall.allowedTCPPorts`
Expected: той самий список, що `git -C $SN stash && nix eval --json .#nixosConfigurations.cyborg.config.networking.firewall.allowedTCPPorts; git -C $SN stash pop` (стан до Step 1–2), і **без** `8091`.

- [ ] **Step 6: Eval-перевірки `envLinesFor` (Review Focus 3)**

```bash
cd $SC && nix eval --json --impure --expr '
  let sc = builtins.getFlake (toString ./.);
      pkgs = import sc.inputs.nixpkgs { system = "x86_64-linux"; config.allowUnfree = true;
                                        overlays = [ sc.inputs.realtime-translation.overlays.default ]; };
      app = import ./applications/services/rt-asr-gpu.nix { inherit pkgs; lib = pkgs.lib; };
      real = sc.nixosConfigurations.cyborg.config;
      with_ = patch: real // { services = real.services // { rtSpeech = real.services.rtSpeech // {
                 asr = real.services.rtSpeech.asr // patch; }; }; };
      ok = app.envLinesFor real;
  in {
    hasPort = builtins.elem "RT_PORT=8091" ok;
    portOverride = builtins.elem "RT_PORT=9191" (app.envLinesFor (with_ { port = 9191; }));
    disabled = (builtins.tryEval (app.envLinesFor (with_ { enable = false; }))).success;
    wrongDevice = (builtins.tryEval (app.envLinesFor (with_ { device = "cpu"; }))).success;
    tailscaleBind = (builtins.tryEval (app.envLinesFor (with_ { host = "100.69.30.96"; }))).success;
  }'
```
Expected: `{"disabled":false,"hasPort":true,"portOverride":true,"tailscaleBind":false,"wrongDevice":false}`. (`portOverride` залежить від того, що `envLines` рендерить `port` з cfg — R2.)

- [ ] **Step 7: Commit + import у clusterops**

```bash
cd $SN && git add system-conf/machines/cyborg
git commit -m "feat(cyborg): enable rt-asr (CUDA) speech service module"
DEVSH "python manage.py import_infra"
```
Expected: `import_infra` завершується `Done (…)`; у `/cluster/config/?tab=artifacts` файли cyborg — не `divergent`.

### Task 7a: CUDA substituter `cache.nixos-cuda.org` на CUDA-хостах (до першого `rt-*-gpu` деплою)

`rt-*-gpu` бандли будуються з окремого `cudaSupport`-інстансу nixpkgs (SP2 `variant-pkgs.nix`); важкі CUDA-пакети (`ctranslate2`, `onnxruntime`, `torch`, `libnvshmem`) є лише в `cache.nixos-cuda.org` (SP5 F7). deploy-rs збирає на таргеті як `deploy`, звичайні користувачі не trusted → `nixConfig` rt-flake і `--option` ігноруються. Без цієї задачі Task 9 Step 3 почне години source-build (spec §5.8, overview §7 C4).

**Files:**
- Create: `system-conf/common/modules/nix/cuda-substituter.nix`
- Modify: `system-conf/machines/cyborg/configuration.nix`, `system-conf/machines/sktop-note27-nixos/configuration.nix` (imports)

**Interfaces:** Produces: `nix.settings.extra-substituters ∋ https://cache.nixos-cuda.org` (+ ключ) на cyborg і SKTOP-NOTE27-NIXOS. Не змінює llama-профілі (лише toplevel цих двох хостів).

Задача незалежна від Tasks 5–7 і може виконуватись раніше (overview §6 крок 1∥ — потрібна SP5 `bench-gpu` на cyborg). Якщо `/tmp/rt-infra-baseline/llama-before.txt` ще не існує — спершу виконати Task 5 Step 2 (baseline) на поточному `main`.

- [ ] **Step 1: Модуль**

```nix
# /system-conf/common/modules/nix/cuda-substituter.nix
# CUDA binary cache for rt-*-gpu bundles (github:DohuyaMa/realtime-translation
# builds them from a separate cudaSupport nixpkgs instance with default
# cudaCapabilities -> substitutable from cache.nixos-cuda.org). Must be in the
# SYSTEM nix.settings: deploy-rs builds on target, normal users are not
# trusted, so flake nixConfig / --option are ignored. Import on CUDA hosts only.
{ ... }:
{
  nix.settings.extra-substituters = [ "https://cache.nixos-cuda.org" ];
  nix.settings.extra-trusted-public-keys = [
    "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M="
  ];
}
```
Імпорт `../../common/modules/nix/cuda-substituter.nix` у `machines/cyborg/configuration.nix` і `machines/sktop-note27-nixos/configuration.nix` (поруч з іншими `common/modules`).

- [ ] **Step 2: Eval + llama незмінна**

```bash
cd $SC && git add -A common/modules/nix machines/cyborg machines/sktop-note27-nixos
nix eval --json .#nixosConfigurations.cyborg.config.nix.settings.extra-substituters
for p in kira-kiosk/llm-cpu sktop-nixos-top/llm-cpu cyborg/llm-gpu SKTOP-NOTE27-NIXOS/llm-gpu SKTOP-NOTE17-NIXOS/llm-rocm; do
  h=${p%/*}; a=${p#*/}; echo "$p $(nix eval --raw ".#deploy.nodes.\"$h\".profiles.\"$a\".path.drvPath")"
done | diff /tmp/rt-infra-baseline/llama-before.txt - && echo LLAMA-IDENTICAL
```
Expected: список містить `https://cache.nixos-cuda.org`; `LLAMA-IDENTICAL`.

- [ ] **Step 3: Застосувати і перевірити**

```bash
cd $SN && git commit -m "feat(nix): cache.nixos-cuda.org substituter on CUDA hosts (rt-*-gpu bundles)" && git push
cd $SC && sudo nixos-rebuild switch --flake .#cyborg
nix run .#colmena -- apply --on SKTOP-NOTE27-NIXOS
nix config show substituters; ssh SKTOP-NOTE27-NIXOS nix config show substituters
DEVSH "python manage.py import_infra"
```
Expected: обидва виводи містять `https://cache.nixos-cuda.org`. Далі Task 9 Step 3 (`--dry-activate`) має тягнути CUDA-пакети з кешу, а не збирати.

### Task 8: clusterops Service `rt-asr-gpu` + згенерований registry

**Files:**
- Modify (generated): `system-conf/applications/registry.nix` — лише через `generate_infra --regenerate`
- Modify: `.claude/rules/nix.md` §2 (`зараз лише llm-cpu/llm-gpu/llm-rocm`) і §5 (`deploy-rs = лише кастомні аплікації (llm-*)`), `system-conf/AGENTS.md` (таблиця Machines, колонка deploy-rs app; рядок `deploy-rs apps` у WHERE TO LOOK), `system-conf/machines/README.md` (Deploy Methods)

**Interfaces:**
- Consumes: API F8; `generate_infra --regenerate` (Task 3); `applications/services/rt-asr-gpu.nix` (Task 6).
- Produces: registry-ключ `"rt-asr-gpu"` з target `cyborg` → `deploy.nodes.cyborg.profiles."rt-asr-gpu"`.

- [ ] **Step 1: Токен і pk вузла**

```bash
TOKEN=$(DEVSH "python manage.py shell -c \"from rest_framework.authtoken.models import Token; from django.contrib.auth import get_user_model; u=get_user_model().objects.filter(is_superuser=True).order_by('pk').first(); print(Token.objects.get_or_create(user=u)[0].key)\"" | tail -1)
NODE=$(DEVSH "python manage.py shell -c \"from clusterops.models import Node; print(Node.objects.get(name='cyborg').pk)\"" | tail -1)
API=http://127.0.0.1:8000/api/clusterops
```

- [ ] **Step 2: Створити Service і target**

```bash
SVC=$(curl -sf -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -X POST $API/services/ -d '{
  "name": "rt-asr-gpu", "service_type": "custom", "deploy_backend": "deploy_rs",
  "state_dir": "/var/lib/rt-asr", "service_name": "rt-asr",
  "health_check_cmd": "curl -sf http://127.0.0.1:8091/health",
  "activation_script": "systemctl restart rt-asr",
  "magic_rollback_enabled": true, "status": "active",
  "flake_module": "applications/services/rt-asr-gpu.nix"}' | jq -r .id)
curl -sf -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -X POST $API/services/$SVC/targets/ \
  -d "{\"node\": $NODE, \"ssh_user\": \"deploy\", \"target_user\": \"root\"}" | jq .
```
Expected: `SVC` — число; target `{"service_name":"rt-asr-gpu","node_name":"cyborg",…}`.

- [ ] **Step 3: Регенерувати registry**

Run: `DEVSH "python manage.py generate_infra --regenerate applications/registry.nix" && cd $SC && git diff applications/registry.nix`
Expected: diff = лише новий блок
```nix
  "rt-asr-gpu" = {
    app = import ./services/rt-asr-gpu.nix { inherit pkgs lib; };
    deploy.targets = [
      { hostname = "cyborg"; sshUser = "deploy"; user = "root"; }
    ];
  };
```
(llm-записи без змін). Якщо змінились llm-записи — STOP: БД розійшлась з registry, розібратися до коміту.

- [ ] **Step 4: Eval профілю**

```bash
cd $SC && nix flake check --no-build
nix eval --raw '.#deploy.nodes.cyborg.profiles."rt-asr-gpu".path.drvPath'
nix eval --json .#deploy.nodes.cyborg.profiles --apply builtins.attrNames
```
Expected: check PASS; drvPath друкується; профілі `["llm-gpu","rt-asr-gpu"]`.

- [ ] **Step 5: Документація фактів, що змінились** — у `.claude/rules/nix.md` §2 `зараз лише llm-cpu/llm-gpu/llm-rocm` → `llm-cpu/llm-gpu/llm-rocm і rt-{asr,mt,tts}-<variant> (applications/services/, builder applications/lib/mkRtSpeechApplication.nix; модуль — nixosModules.rt-speech з github:DohuyaMa/realtime-translation)`; §5 `(llm-*)` → `(llm-*, rt-*)`; у `system-conf/AGENTS.md` колонку deploy-rs app рядка `cyborg` → `llm-gpu`, `rt-asr-gpu`; у `machines/README.md` Deploy Methods рядок cyborg доповнити `deploy-rs .#cyborg.rt-asr-gpu`. Потім `python3 $SN/scripts/check_instruction_budget.py` → OK.

- [ ] **Step 6: Commit, merge, push**

```bash
cd $SN && git add system-conf/applications/registry.nix .claude/rules/nix.md system-conf/AGENTS.md system-conf/machines/README.md
git commit -m "feat(registry): add rt-asr-gpu -> cyborg (generated by clusterops NixGen)"
git switch main && git merge --ff-only rt-speech-infra && git push
DEVSH "python manage.py import_infra"
```

---

## Phase 3 — Deploy & verify

### Task 9: Деплой T1 (rt-asr-gpu @ cyborg) і smoke

**Files:** лише `system-conf/flake.lock` (оновлення перед деплоєм).

**Interfaces:** Consumes: `deploy.nodes.cyborg.profiles."rt-asr-gpu"`; `services.rtSpeech.asr` на cyborg.

- [ ] **Step 1: Release-sync і консистентність контролерів (Review Focus 5)**

```bash
cd $SN && git pull --ff-only && cd $SC && nix flake update && nix flake check --no-build
git -C $SN add system-conf/flake.lock && git -C $SN commit -m "chore(flake): nix flake update" && git -C $SN push
nix flake metadata --json | jq -r '.locks.nodes."realtime-translation".locked | "\(.type) \(.rev)"'
ssh SKTOP-NOTE27-NIXOS 'cd ~/repos/swarm-nix && git pull --ff-only && cd system-conf && nix flake metadata --json | jq -r ".locks.nodes.\"realtime-translation\".locked | \"\(.type) \(.rev)\""'
```
Expected: обидва рядки однакові `github <sha>`. (Якщо `nix flake update` нічого не змінив — пропустити commit.)

- [ ] **Step 2: Платформа (модуль) — self-deploy**

Run: `cd $SC && sudo nixos-rebuild switch --flake .#cyborg`
Expected: успіх; `systemctl status rt-asr` → `inactive (dead)`, `Condition: start condition unmet` (не `failed`); `id rt-asr` існує; `ls -ld /var/lib/rt-asr`.

- [ ] **Step 3: Dry-activate профілю**

Run: `cd $SC && sudo systemctl reset-failed rt-asr || true; nix run .#deploy-rs -- '.#cyborg.rt-asr-gpu' --remote-build --skip-checks --dry-activate`
Expected: збірка на cyborg завершується, dry activation без помилок.

- [ ] **Step 4: Перший деплой (план відновлення — spec §8.4)**

Run: `cd $SC && nix run .#deploy-rs -- '.#cyborg.rt-asr-gpu' --remote-build --skip-checks`
Expected: `✓ Health check passed`, `✓ rt-asr-gpu deployed successfully`. Якщо health check провалився: `sudo systemctl stop rt-asr; sudo rm /var/lib/rt-asr/current; sudo systemctl reset-failed rt-asr; journalctl -u rt-asr -n 100` → виправити → повторити Step 3–4.

- [ ] **Step 5: Локальна перевірка**

```bash
systemctl is-active rt-asr
cat /var/lib/rt-asr/current/env | grep -E '^RT_(HOST|PORT|DEVICE)='
for i in $(seq 1 120); do curl -sf http://127.0.0.1:8091/status | jq -e '.ready == true' >/dev/null && break; sleep 5; done
curl -sf http://127.0.0.1:8091/status | jq .
```
Expected: `active`; `RT_HOST=0.0.0.0`, `RT_PORT=8091`, `RT_DEVICE=cuda`; `/status` `ready: true`, `device: "cuda"`.

- [ ] **Step 6: Smoke через Tailscale (з SKTOP-NOTE27-NIXOS)**

```bash
ssh SKTOP-NOTE27-NIXOS 'set -e
  curl -sf http://100.69.30.96:8091/health
  nix run nixpkgs#espeak-ng -- -v uk -w /tmp/rt-uk.wav "Привіт, це перевірка розпізнавання мовлення"
  curl -sf -F file=@/tmp/rt-uk.wav -F language=uk -F response_format=json http://100.69.30.96:8091/v1/audio/transcriptions'
```
Expected: `{"status":"ok"}`; JSON з непорожнім `text` українською.

- [ ] **Step 7: Повторний деплой через clusterops (шлях системи)**

```bash
TGT=$(curl -sf -H "Authorization: Token $TOKEN" $API/services/$SVC/targets/ | jq -r 'if type=="array" then .[0].id else .results[0].id end')
curl -s -H "Authorization: Token $TOKEN" -X POST $API/service-targets/$TGT/deploy/ | jq '{status, profile_name}'
```
Expected: `{"status":"running","profile_name":"rt-asr-gpu"}`, далі в `/api/clusterops/nodes/$NODE/deploys/` подія `success`.

- [ ] **Step 8: Firewall з LAN (Review Focus 4)** — з `kira-kiosk` (direct LAN peer):

```bash
LAN_IP=$(ip -4 -o addr show dev "$(ip route show default | awk '{print $5; exit}')" | awk '{print $4}' | cut -d/ -f1)
ssh kira-kiosk "curl -m 5 -s -o /dev/null -w '%{http_code}\n' http://$LAN_IP:8091/health"
```
Expected: `000` (timeout/refused), тоді як `ssh kira-kiosk curl -sf http://100.69.30.96:8091/health` → `{"status":"ok"}`.

- [ ] **Step 9: VRAM з llama (критерій 4)**

```bash
curl -s -X POST 'http://127.0.0.1:8080/models/load?model_name=Qwen3.5-4B.Q4_K_M.gguf' | jq -c .
curl -sf -F file=@<(ssh SKTOP-NOTE27-NIXOS cat /tmp/rt-uk.wav) -F language=uk http://127.0.0.1:8091/v1/audio/transcriptions >/dev/null
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
nvidia-smi --query-compute-apps=process_name,used_memory --format=csv
```
Expected: `memory.used` ≤ 7577 MiB (7.4 GiB, поріг gate `system-cyborg` SP5; тут лише llama + ASR — запас під MT має лишитися ≥ ~0.9 GiB). Більше — зменшити `model` у `machines/cyborg/modules/flake-services/rt-speech.nix` (`whisper-large-v3-turbo` → `whisper-small`, драбина SP5 §4.6), commit+push, `nix run .#deploy-rs -- '.#cyborg.rt-asr-gpu' --remote-build --skip-checks` (зміна значення — deploy-rs-only), повторити Step 9. Записати фактичні числа в `system-conf/machines/cyborg/README.md` (розділ GPU/VRAM) і закомітити.

### Task 10: T2 — rt-mt-gpu @ cyborg (gate SP5) або fallback, і rt-tts-cpu @ sktop-nixos-top

**Files:**
- Modify: `system-conf/machines/cyborg/modules/flake-services/rt-speech.nix` (додати `services.rtSpeech.mt`) — основний шлях; **або** (fallback) Create `system-conf/machines/sktop-note27-nixos/modules/flake-services/rt-speech.nix` / `system-conf/machines/kira-kiosk/modules/flake-services/rt-speech.nix`
- Create: `system-conf/machines/sktop-nixos-top/modules/flake-services/rt-speech.nix`
- Modify: відповідні `configuration.nix` (2 імпорти кожен, лише для нових хостів), registry (генерація), `system-conf/AGENTS.md`, `system-conf/machines/README.md`, `system-conf/machines/cyborg/README.md` (VRAM)

**Interfaces:** Consumes: Task 3/6/7a механізми; результат gate `system-cyborg` (SP5 план Task 19 Step 5, записаний у SP5 spec §11). Produces: профілі `cyborg.rt-mt-gpu` (або fallback `SKTOP-NOTE27-NIXOS.rt-mt-gpu` / `kira-kiosk.rt-mt-cpu`) і `sktop-nixos-top.rt-tts-cpu`.

- [ ] **Step 1: Рішення про розміщення MT (overview §7 C3, spec §7)**

Прочитати SP5 spec §11 (`docs/superpowers/specs/2026-10-06-model-optimization-design.md`): gate `system-cyborg` (llama завантажена + ASR + MT, 5 хв) — PASS з піком ≤ 7577 MiB і без OOM?
- PASS → **Step 2a** (`rt-mt-gpu` @ cyborg, ASR-модель — та, з якою gate пройдено).
- FAIL або SP5 Task 19 ще не виконано → **Step 1b** (fallback).

- [ ] **Step 1b (лише fallback): VRAM-гейт note27**

```bash
ssh SKTOP-NOTE27-NIXOS 'm=$(systemctl show llamaService -p Environment | tr " " "\n" | sed -n "s/^MODEL_PATH=//p")
  curl -s -X POST "http://127.0.0.1:8080/models/load?model_name=$(basename "$m")" >/dev/null
  nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv'
```
Рішення: `memory.total − memory.used ≥ 1536 MiB` → **Step 2b** (`rt-mt-gpu` @ SKTOP-NOTE27-NIXOS); інакше → **Step 2c** (`rt-mt-cpu` @ kira-kiosk). Записати назву GPU і числа в `system-conf/machines/sktop-note27-nixos/README.md` (відповідь на spec Q2).

- [ ] **Step 2a: cyborg — додати MT у наявний per-host файл** (`machines/cyborg/modules/flake-services/rt-speech.nix`, поруч з `services.rtSpeech.asr`; імпорти вже є з Task 7):

```nix
  # rt-mt on the same RTX 4060 as llama + rt-asr: allowed only because the
  # sub-project 5 gate system-cyborg (llama loaded + ASR + MT, 5 min) passed
  # at <= 7.4 GiB (infra spec §7). Fallback: rt-mt-cpu on kira-kiosk.
  services.rtSpeech.mt = {
    enable = true;
    deployRsPath = "/var/lib/rt-mt/current";
    host = "0.0.0.0";        # tailscale0 trusted fleet-wide; no port opened
    openFirewall = false;
    device = "cuda";
    model = "nllb-200-distilled-600M-ct2-int8";   # sub-project 5 registry alias
    computeType = "int8_float16";
  };
```

- [ ] **Step 2b (fallback): note27 per-host файл** (`machines/sktop-note27-nixos/modules/flake-services/rt-speech.nix`) — той самий блок `services.rtSpeech.mt` у файлі зі шапкою `# Speech services on SKTOP-NOTE27-NIXOS (deploy-rs app: rt-mt-gpu). No top-level enable (see cyborg's copy); imported from ../../configuration.nix.`, обгорнутий у `{ ... }: { … }`. Імпорти в `machines/sktop-note27-nixos/configuration.nix`: `../../common/modules/rt-speech.nix` після `../../common/modules/llm/llama-service.nix`; `./modules/flake-services/rt-speech.nix` після `./modules/flake-services/llama-service.nix` (substituter — уже з Task 7a).

- [ ] **Step 2c (fallback): kira-kiosk** — той самий файл у `machines/kira-kiosk/modules/flake-services/rt-speech.nix` з `device = "cpu"; computeType = "int8";` (`extraEnv.RT_NUM_THREADS = "2";`, SP5 §4.6) і коментарем `(deploy-rs app: rt-mt-cpu)`; імпорти в `machines/kira-kiosk/configuration.nix` після рядків llama.

- [ ] **Step 3: sktop-nixos-top** (`machines/sktop-nixos-top/modules/flake-services/rt-speech.nix`):

```nix
# system-conf/machines/sktop-nixos-top/modules/flake-services/rt-speech.nix
# Speech services on sktop-nixos-top (deploy-rs app: rt-tts-cpu).
# Imported from ../../configuration.nix. Shares CPU with llm-cpu (ctx 4096):
# expect TTS latency spikes while the llama worker generates (spec §7, R-CPU).
{ ... }:
{
  services.rtSpeech.tts = {
    enable = true;
    deployRsPath = "/var/lib/rt-tts/current";
    host = "0.0.0.0";        # tailscale0 trusted fleet-wide; no port opened
    openFirewall = false;
    device = "cpu";
    model = "kokoro-82m-v1.0-onnx";   # sub-project 5 registry alias (ORT engine, RT_TTS_ENGINE=onnx default)
  };
}
```
Імпорти в `machines/sktop-nixos-top/configuration.nix`: `../../common/modules/rt-speech.nix` після `../../common/modules/llm/llama-service.nix`; `./modules/flake-services/rt-speech.nix` після `./modules/flake-services/llama-service.nix`.

- [ ] **Step 4: Build + firewall + eval-перевірки** (для кожного зміненого `<node>`/`<svc>`):

```bash
cd $SC && git add -A machines
for h in cyborg sktop-nixos-top; do   # fallback: + SKTOP-NOTE27-NIXOS або kira-kiosk
  nix build --no-link ".#nixosConfigurations.\"$h\".config.system.build.toplevel"
  nix eval --json ".#nixosConfigurations.\"$h\".config.networking.firewall.allowedTCPPorts"
done
```
Expected: збірки успішні; у списках портів немає `8092`/`8093`.
Додатково для 2a: повторити Task 7 Step 6 для `applications/services/rt-mt-gpu.nix` проти `nixosConfigurations.cyborg` (`hasPort` з `RT_PORT=8092`).

- [ ] **Step 5: Commit, import, Services, registry**

```bash
cd $SN && git add system-conf/machines && git commit -m "feat(machines): enable rt-mt and rt-tts speech services (T2 topology)"
DEVSH "python manage.py import_infra"
for spec in "rt-mt-gpu:cyborg:mt:8092" "rt-tts-cpu:sktop-nixos-top:tts:8093"; do   # fallback: rt-mt-gpu:SKTOP-NOTE27-NIXOS:mt:8092 або rt-mt-cpu:kira-kiosk:mt:8092
  IFS=: read name node svc port <<<"$spec"
  NPK=$(DEVSH "python manage.py shell -c \"from clusterops.models import Node; print(Node.objects.get(name='$node').pk)\"" | tail -1)
  SPK=$(curl -sf -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -X POST $API/services/ -d "{
    \"name\": \"$name\", \"service_type\": \"custom\", \"deploy_backend\": \"deploy_rs\",
    \"state_dir\": \"/var/lib/rt-$svc\", \"service_name\": \"rt-$svc\",
    \"health_check_cmd\": \"curl -sf http://127.0.0.1:$port/health\",
    \"activation_script\": \"systemctl restart rt-$svc\", \"magic_rollback_enabled\": true,
    \"status\": \"active\", \"flake_module\": \"applications/services/$name.nix\"}" | jq -r .id)
  curl -sf -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -X POST $API/services/$SPK/targets/ \
    -d "{\"node\": $NPK, \"ssh_user\": \"deploy\", \"target_user\": \"root\"}" | jq -c '{service_name,node_name}'
done
DEVSH "python manage.py generate_infra --regenerate applications/registry.nix"
cd $SC && git diff applications/registry.nix && nix flake check --no-build
```
Expected: diff — лише два нові блоки; check PASS.

- [ ] **Step 6: Docs + commit + push** — `system-conf/AGENTS.md` колонка deploy-rs app: cyborg `llm-gpu`, `rt-asr-gpu`, `rt-mt-gpu` (fallback: note27 `llm-gpu`, `rt-mt-gpu` або kira-kiosk `llm-cpu`, `rt-mt-cpu`); sktop-nixos-top `llm-cpu`, `rt-tts-cpu`; `machines/README.md` Deploy Methods — відповідні `deploy-rs .#<node>.<profile>`.

```bash
cd $SN && git add system-conf && git commit -m "feat(registry): add rt-mt and rt-tts targets (generated by clusterops NixGen)" && git push
DEVSH "python manage.py import_infra"
```

- [ ] **Step 7: Платформа → deploy-rs на кожному хості** (з cyborg; спершу Task 9 Step 1 для синхронізації lock):

```bash
cd $SC
# cyborg (2a): self-deploy платформи — sudo nixos-rebuild switch --flake .#cyborg; потім deploy-rs як нижче для "cyborg:rt-mt-gpu:rt-mt"
for p in "sktop-nixos-top:rt-tts-cpu:rt-tts"; do   # fallback MT: + "SKTOP-NOTE27-NIXOS:rt-mt-gpu:rt-mt" або "kira-kiosk:rt-mt-cpu:rt-mt"
  IFS=: read node prof unit <<<"$p"
  nix run .#colmena -- apply --on "$node"
  nix run .#colmena -- exec --on "$node" "sudo systemctl reset-failed $unit || true"
  nix run .#deploy-rs -- ".#$node.$prof" --remote-build --skip-checks --dry-activate
  nix run .#deploy-rs -- ".#$node.$prof" --remote-build --skip-checks
done
```
Expected: кожен деплой — `✓ Health check passed`. Провал першого деплою → план відновлення spec §8.4 на тому хості.

- [ ] **Step 8: Smoke T2 через Tailscale (з cyborg)**

```bash
MT=127.0.0.1   # 2a: rt-mt на cyborg; fallback: $(tailscale ip -4 sktop-note27-nixos) або $(tailscale ip -4 kira-kiosk)
TTS=$(tailscale ip -4 sktop-nixos-top)
curl -sf http://$MT:8092/health; curl -sf http://$TTS:8093/health
curl -sf -H 'Content-Type: application/json' -d '{"text":"Привіт, як справи?","source_lang":"uk","target_lang":"en"}' http://$MT:8092/v1/translate | jq .
curl -sf -H 'Content-Type: application/json' -d '{"input":"Hello, how are you?","response_format":"wav"}' -o /tmp/rt-tts.wav http://$TTS:8093/v1/audio/speech
file /tmp/rt-tts.wav
```
Expected: `{"status":"ok"}` ×2; `translations[0]` — англійський переклад; `/tmp/rt-tts.wav: RIFF (little-endian) data, WAVE audio`.

- [ ] **Step 9 (лише 2a): живий VRAM-вимір на cyborg (критерій 4)** — llama завантажена (як Task 9 Step 9), паралельно 2 хв ASR-запитів і MT-запитів (`for i in $(seq 60); do curl … /v1/audio/transcriptions; curl … /v1/translate; done`), під час навантаження `nvidia-smi --query-gpu=memory.used --format=csv,noheader -lms 500 | sort -n | tail -1`.
Expected: пік ≤ 7577 MiB (7.4 GiB), жодного OOM у `journalctl -u rt-asr -u rt-mt -u llamaService`. Більше → драбина SP5 §4.6 (ASR → `whisper-small`, deploy-rs-only), якщо й тоді більше — перенести MT на fallback (Step 1b) і видалити `rt-mt-gpu`-target cyborg через clusterops. Числа записати в `system-conf/machines/cyborg/README.md`.

### Task 11: Фаза B — токен через clusterops Secret (sops)

**Files:**
- Modify (generated): `system-conf/machines/<dir>/secrets.sops.yaml`, `system-conf/machines/<dir>/secrets-module.nix` — лише генерацією
- Modify: `system-conf/machines/<dir>/configuration.nix` (імпорт `secrets-module.nix`), `system-conf/machines/<dir>/modules/flake-services/rt-speech.nix` (`apiTokenFile`) — для cyborg, sktop-nixos-top (+ fallback MT-хост note27/kira-kiosk, якщо використано)

**Interfaces:** Consumes: `POST /api/clusterops/secrets/`, `NixGenerator.generate_secrets/_module`, R3 (`LoadCredential`). Produces: `/run/secrets/rt-api-token` (root 0400) на кожному rt-хості; `apiTokenFile` у модулі.

- [ ] **Step 1: Перевірити передумову sops для кожного хоста**

Run: `DEVSH "python manage.py shell -c \"from clusterops.models import Secret; [print(n, Secret.objects.filter(name__endswith='-ssh-host-ed25519-public', target_node__name=n).exists()) for n in ['cyborg','SKTOP-NOTE27-NIXOS','sktop-nixos-top','kira-kiosk']]\""`
Expected: `True` для rt-хостів. Хост з `False` лишається у фазі A (без токена) — записати в `system-conf/context/tasks.md` як окрему задачу (onboarding host pubkey), не обходити.

- [ ] **Step 2: Згенерувати одне значення і створити Secret-рядки**

```bash
RT_TOKEN=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40)
for node in cyborg sktop-nixos-top; do   # лише хости з True у Step 1 (+ fallback MT-хост, якщо використано)
  NPK=$(DEVSH "python manage.py shell -c \"from clusterops.models import Node; print(Node.objects.get(name='$node').pk)\"" | tail -1)
  curl -sf -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -X POST $API/secrets/ -d "{
    \"name\": \"$node-rt-api-token\", \"value\": \"$RT_TOKEN\", \"target_node\": $NPK,
    \"target_path\": \"/run/secrets/rt-api-token\", \"file_mode\": \"0400\", \"file_owner\": \"root\"}" | jq -c '{name,target_node_name}'
done
NPK=$(DEVSH "python manage.py shell -c \"from clusterops.models import Node; print(Node.objects.get(name='cyborg').pk)\"" | tail -1)
curl -sf -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -X POST $API/secrets/ -d "{
  \"name\": \"cyborg-rt-api-token-dmaslo\", \"value\": \"$RT_TOKEN\", \"target_node\": $NPK,
  \"target_path\": \"/run/secrets/rt-api-token-dmaslo\", \"file_mode\": \"0400\", \"file_owner\": \"dmaslo\"}" | jq -c .
unset RT_TOKEN
```
Expected: 4 рядки створено (останній — для edge-клієнта, Task 12).

- [ ] **Step 3: Згенерувати sops-файли** — для кожного вузла: UI «Regenerate secrets» на `/cluster/nodes/<pk>/` (view `node_regenerate_secrets`), потім `cd $SC && git status machines/` → нові `machines/<dir>/secrets.sops.yaml` і `secrets-module.nix`; `grep -c 'ENC\[' machines/<dir>/secrets.sops.yaml` > 0 (ciphertext, без plaintext).

- [ ] **Step 4: Підключити секрет і токен** — у `machines/<dir>/configuration.nix` кожного вузла в `imports` додати `./secrets-module.nix`; у його `modules/flake-services/rt-speech.nix` для кожного `services.rtSpeech.<svc>` додати:

```nix
    # Bearer token for /v1/* (sops via clusterops Secret "<node>-rt-api-token",
    # root 0400; the unit reads it through LoadCredential).
    apiTokenFile = "/run/secrets/rt-api-token";
```
`nix build --no-link` toplevel кожного вузла → commit `feat(machines): enable rt API token via sops` → push → `import_infra`.

- [ ] **Step 5: Розгорнути** — cyborg: `sudo nixos-rebuild switch --flake .#cyborg`; інші: `nix run .#colmena -- apply --on <node>`; далі `nix run .#deploy-rs -- '.#<node>.<profile>' --remote-build --skip-checks` для кожного rt-профілю (env-файл тепер містить `RT_API_TOKEN_FILE`).

- [ ] **Step 6: Verify (з cyborg)**

```bash
T=$(sudo cat /run/secrets/rt-api-token-dmaslo)
curl -s -o /dev/null -w '%{http_code}\n' -F file=@/tmp/rt-uk.wav http://127.0.0.1:8091/v1/audio/transcriptions
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $T" -F file=@/tmp/rt-uk.wav http://127.0.0.1:8091/v1/audio/transcriptions
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8091/health
sudo ls -l /run/secrets/rt-api-token; systemctl show rt-asr -p LoadCredential
```
Expected: `401`, `200`, `200`; файл `-r-------- root root`; `LoadCredential=api-token:/run/secrets/rt-api-token`. Повторити `401/200` для `$MT:8092/v1/translate` і `$TTS:8093/v1/audio/speech`.

### Task 12: Edge (Home Manager) на cyborg + e2e

**Files:**
- Modify: `system-conf/machines/cyborg/system.nix` (`home-manager.users.dmaslo`)

**Interfaces:** Consumes: `inputs.realtime-translation.homeManagerModules.rt-translator` з опціями `rt-translator.{enable, mode, endpoints.{asr,mt,tts}, apiTokenFile}` (overview §4.4). Gate: `nix eval --impure --expr 'builtins.attrNames (builtins.getFlake "github:DohuyaMa/realtime-translation").homeManagerModules'` містить `rt-translator`, інакше задача чекає підпроєкт 2.

- [ ] **Step 1: Підключити edge**

```nix
  home-manager.users.dmaslo = {
    imports = [
      ./home/default.nix
      inputs.realtime-translation.homeManagerModules.rt-translator
    ];

    # Edge only (UI, capture, playback, PipeWire sinks); compute lives in
    # system services rt-asr + rt-mt (here) and rt-tts (Tailscale peer).
    rt-translator = {
      enable = true;
      mode = "translate";
      endpoints = {
        asr = "http://127.0.0.1:8091";
        mt = "http://127.0.0.1:8092";       # rt-mt-gpu on cyborg (fallback T2: note27 100.87.250.83 / kira-kiosk 100.120.81.4)
        tts = "http://100.123.195.44:8093"; # sktop-nixos-top
      };
      apiTokenFile = "/run/secrets/rt-api-token-dmaslo";   # omit if Task 11 left cyborg in phase A
    };
  };
```
(IP — як у llama-конфігах, spec Q6; перевірити `tailscale ip -4 <host>` перед комітом.)

- [ ] **Step 2: Build + switch**

Run: `cd $SC && git add machines/cyborg/system.nix && nix build --no-link .#nixosConfigurations.cyborg.config.system.build.toplevel && sudo nixos-rebuild switch --flake .#cyborg`
Expected: успіх; `systemctl --user list-units 'rt-*'` показує edge-юніти підпроєкту 2; `pactl list short sinks | grep rt_virtual`.

- [ ] **Step 3: e2e (доказ на живому кейсі)** — запустити UI (`translator-ui` з HM-профілю), режим `translate`, сказати фразу українською в мікрофон. Expected: у UI з'являються partial/final транскрипт (rt-asr), переклад англійською (rt-mt), озвучка грає локально на cyborg (rt-tts); `journalctl -u rt-asr -n 20` на cyborg, `journalctl -u rt-mt -n 20` (fallback: на MT-хості), `ssh sktop-nixos-top journalctl -u rt-tts -n 20` показують запити. Режим `transcribe` — лише транскрипт, без запитів до rt-mt/rt-tts.

- [ ] **Step 4: Commit + push**

```bash
cd $SN && git add system-conf/machines/cyborg/system.nix
git commit -m "feat(cyborg): enable rt-translator edge (Home Manager) against rt-asr/rt-mt/rt-tts"
git push && DEVSH "python manage.py import_infra"
```

---

## Self-review (виконано при написанні)

- **Spec coverage:** §5.1 → Task 5 (+ git-only, Step 8, Task 9 Step 1); §5.2 → Task 6 Step 3; §5.3 → Task 5; §5.4 → Task 6; §5.5 → Tasks 1–3; §5.6 → Tasks 7, 10; §5.7 → Task 12; §6 throws → Task 7 Step 6, Condition → Task 7 Step 4 / Task 9 Step 2; §5.8 substituter → Task 7a; §7 топології/VRAM → Task 9 Step 9, Task 10 Steps 1/9 (gate SP5); §8.1 порядок → фази 1–3; §8.3 → Task 11; §8.4 → Task 9 Step 4; §9 → кроки verify у кожній задачі; §10 → Task 4; документація → Tasks 4, 8, 10.
- **Placeholders:** немає; моделі мають конкретні дефолти з відсиланням до підпроєкту 5.
- **Type consistency:** `envLinesFor`, `mkEnvFile lines`, `regenerate_generated_file(path, method)`, `GENERATED_FILES`, `_infer_service_type`, імена профілів `rt-<svc>-<variant>` однакові в усіх задачах.
- **Review Focus:** 5 пунктів, кожен прив'язаний до кроку з перевіркою.
