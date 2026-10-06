# Model optimization & VRAM budget — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дати сервісам `rt-asr`/`rt-mt`/`rt-tts` оптимізовані рушії моделей (faster-whisper int8, NLLB на CTranslate2 int8 без transformers/torch, Kokoro через onnxruntime), керований реєстр моделей із pin'ом ревізії/sha256 і провізіонуванням у `<RT_MODEL_DIR>`, та benchmark harness, який вимірює RTF/латентність/VRAM і перевіряє пороги, щоб VRAM-бюджет хостів було підтверджено цифрами.

**Architecture:** Чисті Python-модулі в `src/services/common` (реєстр, сховище моделей, env, device) і `src/services/{asr,mt,tts}` (рушії з інжекцією фабрик для тестів без моделей). Harness у `scripts/bench` запускає кожну конфігурацію в окремому підпроцесі, семплює VRAM/RSS і оцінює `thresholds.yml`. DevShells `bench-cpu`/`bench-gpu`/`model-tools` дають середовище без source-build torch.

**Tech Stack:** Python 3.13, nixpkgs цього репо після SP2 Task 0 (≥ ревізії інфри `7a0f122…`; факти spec §2 перевірені на старому `89570f24e97e614aa34aa9ab1c927b6578a43775` — версії нижче можуть бути новішими: ctranslate2 4.8.0→4.8.2, faster-whisper 1.2.1, onnxruntime 1.26.0, kokoro/misaki 0-unstable-2025-06-16, huggingface-hub 1.10.2, sentencepiece 0.2.1, jiwer 4.0.0, sacrebleu 2.6.0, nvidia-ml-py, psutil, pyarrow), pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-model-optimization-design.md` (базується на `docs/superpowers/specs/2026-10-06-distributed-speech-services-overview.md`).

## Global Constraints

- Nix-only: жодного `pip install`, venv, `requirements.txt`; залежності — лише через `nix/` / `flake.nix` (структура SP2; SP2 Tasks 0–1 виконуються до цього плану — overview §6).
- ROCm (лише Task 22): штатний nixpkgs `ctranslate2.override { rocmSupport = true; rocmGpuTargets = [ "gfx900" ]; }` — без власного `WITH_HIP` overrideAttrs (overview §7 C1).
- Усі `pytest`/`python` команди виконуються як `nix develop .#bench-cpu -c <cmd>` з кореня репо (для GPU-вимірів — `.#bench-gpu`, для конвертації — `.#model-tools`).
- Ніколи не override'ити `torch` → `torch-bin`; GPU-середовища будуються з `import nixpkgs { config = { allowUnfree = true; cudaSupport = true; }; }` з дефолтними `cudaCapabilities`.
- Рантайм-код MT (`src/services/mt/*`) не імпортує `transformers` і `torch`.
- Важкі бібліотеки (`ctranslate2`, `faster_whisper`, `onnxruntime`, `kokoro`, `torch`, `huggingface_hub`, `sentencepiece`) імпортуються **ліниво** всередині функцій/конструкторів, щоб тести працювали з фейками і щоб `HF_HOME`/`HF_HUB_OFFLINE` встигали виставитись до імпорту.
- Env-імена — точно як у spec §4.8 / overview §4.2: `RT_DEVICE`, `RT_MODEL`, `RT_COMPUTE_TYPE`, `RT_MODEL_DIR`, `RT_NUM_THREADS`, `RT_MODEL_DOWNLOAD`, `RT_MODEL_VERIFY`, `RT_HF_TOKEN_FILE`, `RT_TTS_ENGINE`, `RT_ASR_BEAM_SIZE`, `RT_MT_NUM_BEAMS`, `RT_TTS_VOICE`.
- Розкладка моделей: `<RT_MODEL_DIR>/hf` (= `HF_HOME`), `<RT_MODEL_DIR>/rt/<alias>/`, `<RT_MODEL_DIR>/rt/<alias>/.rt-manifest.json`, `<RT_MODEL_DIR>/rt/<alias>.lock`; dev-default `RT_MODEL_DIR` = `~/.cache/rt-speech/models`.
- Реєстр: ревізія — 40 hex, sha256 — 64 hex для **кожного** файла.
- Defaults: compute `asr/mt`: cpu `int8`, cuda/rocm `int8_float16`; `tts`: `float32`; beams: ASR 1 (cpu) / 3 (cuda), MT 2 (cpu) / 4 (cuda); voice `af_heart`; threads `min(4, cpu_count)`.
- `rt-tts` не має `rocm` (DeviceError); CT2-рушії приймають `rocm` (бекенд-рядок `cuda`).
- Коміти: звичайні conventional-повідомлення, **без** рядків `Co-Authored-By` (правило власника репо).
- Не чіпати старі сервіси `src/translate`, `src/whisper`, `src/tts`, `src/models` — міграція поза скоупом.
- Імена тест-файлів унікальні з префіксом `test_rt_` (у `tests/` нема `__init__.py`); виняток — Task 12a `test_svc_sp5_integration.py` (імпортує модулі SP1, тому живе в групі `test_svc_*` і запускається в `nix develop`, не в bench-shell).

## Review Focus

- Два процеси (сервіс і `rt-models fetch`) провізіонують той самий alias одночасно → очікується одне завантаження, другий бачить готовий manifest. Тест: Task 5 `test_concurrent_ensure_downloads_once`.
- Перерване завантаження лишило `rt/<alias>.partial` зі сміттям → наступний `ensure` прибирає і завантажує заново. Тест: Task 5 `test_stale_partial_is_replaced`.
- `RT_MODEL` вказує на файл, а не директорію → зрозуміла помилка `ModelNotProvisioned`, не трейсбек з глибини CT2. Тест: Task 5 `test_resolve_abs_path_file_rejected`.
- Аудіо з WS приходить як PCM16 (`int16`) або стерео → int16 масштабується в float32, 2-D відхиляється `ValueError`. Тест: Task 10 `test_int16_audio_is_scaled` / `test_stereo_rejected`.
- Ім'я голосу з path traversal (`../../etc/passwd`) → `UnknownVoice`, жодного читання файлів поза `voices/`. Тест: Task 11 `test_voice_path_traversal_rejected`.

---

## File Structure

| Файл | Відповідальність |
|---|---|
| `nix/bench-shells.nix` (new) | devShells `bench-cpu`, `bench-gpu` (x86_64-linux), `model-tools` |
| `flake.nix` (modify: рядок `devShells = …`) | підключити bench-shells |
| `.gitignore` (modify) | `bench-data/`, `bench-results/` |
| `pyproject.toml` (modify `[project.scripts]`) | `rt-models` entry point |
| `src/services/__init__.py`, `src/services/{common,asr,mt,tts}/__init__.py` (new, якщо нема) | пакети |
| `src/services/common/model_registry.py` | `FileSpec`, `ModelSpec`, `REGISTRY`, `get_spec`, `specs_for`, `UnknownModel` |
| `src/services/common/hf_env.py` | `configure_hf_home`, `set_offline`, `read_token_file` |
| `src/services/common/device.py` | `normalize_device`, `backend_device`, defaults, `resolve_ct2_compute_type`, `require_gpu`, `DeviceError` |
| `src/services/common/model_store.py` | `ModelStore`, `resolve_model`, `prepare_model`, `ModelSettings`, `sha256_file`, винятки |
| `src/services/common/models_cli.py` | CLI `rt-models list|fetch|verify|path` |
| `src/services/mt/nllb_codes.py` | ISO 639-1 → FLORES-200 |
| `src/services/mt/sentence_split.py` | `split_sentences` |
| `src/services/mt/ct2_translator.py` | `Ct2NllbTranslator`, `chunk_tokens`, `load_vocabulary` |
| `src/services/asr/whisper_engine.py` | `WhisperEngine`, `VadProfile`, `STREAMING_VAD`, `FILE_VAD`, результати |
| `src/services/tts/g2p.py` | `KokoroG2P`, `G2PChunk`, `lang_code_for_voice`, `UnknownVoice` |
| `src/services/tts/kokoro_onnx.py` | `KokoroOnnxSynthesizer` |
| `src/services/tts/kokoro_torch.py` | `KokoroTorchSynthesizer` (fallback) |
| `src/services/tts/engines.py` | `create_synthesizer` |
| `scripts/model_tools/{__init__,pin_model,convert_nllb_ct2,make_nllb_golden,nllb_hf_reference}.py` | offline-інструменти (devShell `model-tools`) |
| `scripts/bench/{__init__,__main__,cli,common,metrics,thresholds,resources,datasets,run_asr,run_mt,run_tts,matrix,system_load}.py` | harness |
| `scripts/bench/nix/ctranslate2-rocm.nix` (Task 22, опційно) | Python-env з nixpkgs `ctranslate2.override { rocmSupport = true; }` для spike на залізі |
| `src/services/mt/ctranslate2_backend.py`, зміни в `src/services/{asr,mt,tts}/{settings,*_backend}.py` (Task 12a) | підключення рушіїв до backend-ів SP1 (CT2 MT default, ORT TTS default, alias-и реєстру) |
| `scripts/bench/thresholds.yml`, `scripts/bench/matrix.yml`, `scripts/bench/data/tts_sentences.txt` | конфіг/дані harness'у |
| `tests/services/test_rt_*.py`, `tests/bench/test_rt_*.py` | unit-тести |
| `tests/fixtures/nllb_golden_tokens.json` (generated, Task 9) | golden токенізація NLLB |

---

## Phase A — Основа

### Task 1: DevShells для бенчмарків і скелет пакетів

**Files:**
- Create: `nix/bench-shells.nix`
- Modify: `flake.nix` (рядок `devShells = forAllSystems (pkgs: { default = import ./nix/devshell.nix { inherit pkgs; }; });` — структура після SP2 Task 1)
- Modify: `.gitignore`
- Create (якщо не існують): `src/services/__init__.py`, `src/services/common/__init__.py`, `src/services/asr/__init__.py`, `src/services/mt/__init__.py`, `src/services/tts/__init__.py`, `scripts/bench/__init__.py`, `scripts/model_tools/__init__.py`

**Interfaces:**
- Produces: `nix develop .#bench-cpu`, `.#bench-gpu` (x86_64-linux), `.#model-tools`; у shell `PYTHONPATH` містить корінь репо.

- [ ] **Step 1: Створити `nix/bench-shells.nix`**

```nix
# DevShells for model benchmarks and offline model tools (sub-project 5).
# bench-gpu MUST come from a nixpkgs instance with global cudaSupport = true and
# default cudaCapabilities, so every CUDA path is substitutable from
# https://cache.nixos-cuda.org (never override torch -> torch-bin).
{ nixpkgs, system }:
let
  lib = nixpkgs.lib;
  mkPkgs = extraConfig: import nixpkgs {
    inherit system;
    config = { allowUnfree = true; } // extraConfig;
  };
  pkgsCpu = mkPkgs { };
  pkgsCuda = mkPkgs { cudaSupport = true; };

  enCoreWebSm = ps: ps.buildPythonPackage {
    pname = "en_core_web_sm";
    version = "3.8.0";
    format = "wheel";
    src = pkgsCpu.fetchurl {
      url = "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl";
      sha256 = "11gvl30zfa49rwkfnbm5yja7gwaxq37k8szdvvrvzm17nyfl4chr";
    };
    doCheck = false;
  };

  benchPython = pkgs: pkgs.python313.withPackages (ps: with ps; [
    ctranslate2
    faster-whisper
    onnxruntime
    sentencepiece
    huggingface-hub
    numpy
    soundfile
    pyyaml
    psutil
    nvidia-ml-py
    jiwer
    sacrebleu
    pyarrow
    kokoro
    (enCoreWebSm ps)
    pytest
    pytest-timeout
  ]);

  modelToolsPython = pkgsCpu.python313.withPackages (ps: with ps; [
    ctranslate2
    transformers
    torch
    sentencepiece
    protobuf
    huggingface-hub
    sacrebleu
    pyyaml
    pytest
    pytest-timeout
  ]);

  mkBenchShell = pkgs: python: pkgs.mkShell {
    packages = [ python ];
    shellHook = ''
      export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"
    '';
  };
in
{
  devShells = {
    bench-cpu = mkBenchShell pkgsCpu (benchPython pkgsCpu);
    model-tools = mkBenchShell pkgsCpu modelToolsPython;
  } // lib.optionalAttrs (system == "x86_64-linux") {
    bench-gpu = mkBenchShell pkgsCuda (benchPython pkgsCuda);
  };
}
```

- [ ] **Step 2: Підключити у `flake.nix`**

Замінити рядок

```nix
      devShells = forAllSystems (pkgs: { default = import ./nix/devshell.nix { inherit pkgs; }; });
```

на

```nix
      devShells = forAllSystems (pkgs:
        { default = import ./nix/devshell.nix { inherit pkgs; }; }
        # bench-cpu / bench-gpu / model-tools (SP5 Task 1); bench-gpu uses its own cudaSupport instance
        // lib.optionalAttrs (builtins.pathExists ./nix/bench-shells.nix)
          (import ./nix/bench-shells.nix { inherit nixpkgs; system = pkgs.stdenv.hostPlatform.system; }).devShells);
```

- [ ] **Step 3: Скелет пакетів і `.gitignore`**

```bash
for d in src/services src/services/common src/services/asr src/services/mt src/services/tts scripts/bench scripts/model_tools; do
  mkdir -p "$d"; [ -f "$d/__init__.py" ] || : > "$d/__init__.py"
done
mkdir -p tests/services tests/bench tests/fixtures
grep -qx 'bench-data/' .gitignore || printf '\n# Model benchmarks (sub-project 5)\nbench-data/\nbench-results/\n' >> .gitignore
git add nix/bench-shells.nix flake.nix .gitignore src/services scripts/bench scripts/model_tools
```

- [ ] **Step 4: Перевірити, що CPU-shell повністю підтягується з кешу**

Run: `nix build --dry-run .#devShells.x86_64-linux.bench-cpu 2>&1 | sed -n '/will be built/,/will be fetched/p'`
Expected: у списку "will be built" лише дрібні похідні (`python3-3.13…-env`, `en_core_web_sm`, `bench-cpu`, можливо `pytest-timeout`); **нема** `torch`, `ctranslate2`, `onnxruntime`, `libnvshmem`.

- [ ] **Step 5: Smoke-імпорт**

Run: `nix develop .#bench-cpu -c python -c "import ctranslate2, faster_whisper, onnxruntime, kokoro, sentencepiece, jiwer, sacrebleu, psutil, pynvml, pyarrow, soundfile, yaml; print(ctranslate2.__version__, sorted(ctranslate2.get_supported_compute_types('cpu')))"`
Expected: `4.8.x ['float32', 'int8', 'int8_float32']` (версія — з lock після SP2 Task 0)

- [ ] **Step 6: Перевірити GPU-shell (лише x86_64-linux, cyborg)**

Run: `nix config show substituters`
Expected: містить `https://cache.nixos-cuda.org`. Якщо **ні** — зупинитись: це SP3 Task 7a (модуль `common/modules/nix/cuda-substituter.nix`, імпорт на cyborg і SKTOP-NOTE27-NIXOS); суть налаштування:
```nix
nix.settings.extra-substituters = [ "https://cache.nixos-cuda.org" ];
nix.settings.extra-trusted-public-keys = [ "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M=" ];
```
(`trusted-users = root deploy`, тож `--option` від звичайного користувача ігнорується.) Потім:
Run: `nix build --dry-run .#devShells.x86_64-linux.bench-gpu 2>&1 | sed -n '/will be built/,/will be fetched/p' | grep -E 'torch-2|ctranslate2-4|onnxruntime-1|libnvshmem' || echo NO-HEAVY-BUILDS`
Expected: `NO-HEAVY-BUILDS` (чисто-Python пакети на кшталт `kokoro`/`faster-whisper` можуть збиратись локально — це секунди).

- [ ] **Step 7: Commit**

```bash
git add nix/bench-shells.nix flake.nix .gitignore src/services scripts/bench scripts/model_tools
git commit -m "build: add bench-cpu/bench-gpu/model-tools devShells and services package skeleton"
```

---

### Task 2: Реєстр моделей

**Files:**
- Create: `src/services/common/model_registry.py`
- Test: `tests/services/test_rt_model_registry.py`

**Interfaces:**
- Produces: `FileSpec(path: str, size: int, sha256: str)`, `ModelSpec(alias, service, repo_id, revision, license, files: tuple[FileSpec, ...])` з property `allow_patterns -> list[str]`; `REGISTRY: dict[str, ModelSpec]`; `get_spec(alias: str) -> ModelSpec` (кидає `UnknownModel(LookupError)`); `specs_for(service: str) -> list[ModelSpec]`; `_SPECS: tuple[ModelSpec, ...]`.

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_model_registry.py
import re

import pytest

from src.services.common import model_registry as mr

DEFAULT_ALIASES = [
    "whisper-small",
    "whisper-medium",
    "whisper-large-v3-turbo",
    "nllb-200-distilled-600M-ct2-int8",
    "kokoro-82m-v1.0-onnx",
    "kokoro-82m-v1.0-torch",
]


def test_aliases_unique():
    assert len(mr.REGISTRY) == len(mr._SPECS)


def test_every_spec_is_pinned():
    for spec in mr._SPECS:
        assert re.fullmatch(r"[0-9a-f]{40}", spec.revision), spec.alias
        assert spec.service in {"asr", "mt", "tts"}
        assert spec.files, spec.alias
        for f in spec.files:
            assert re.fullmatch(r"[0-9a-f]{64}", f.sha256), (spec.alias, f.path)
            assert f.size > 0
            assert not f.path.startswith("/") and ".." not in f.path


@pytest.mark.parametrize("alias", DEFAULT_ALIASES)
def test_default_aliases_present(alias):
    assert mr.get_spec(alias).alias == alias


def test_unknown_alias():
    with pytest.raises(mr.UnknownModel, match="unknown model alias 'nope'"):
        mr.get_spec("nope")


def test_specs_for_service():
    assert {s.alias for s in mr.specs_for("mt")} == {"nllb-200-distilled-600M-ct2-int8"}


def test_onnx_spec_has_required_files():
    paths = {f.path for f in mr.get_spec("kokoro-82m-v1.0-onnx").files}
    assert {
        "onnx/model.onnx",
        "onnx/model_fp16.onnx",
        "onnx/model_quantized.onnx",
        "tokenizer.json",
        "voices/af_heart.bin",
        "voices/bf_emma.bin",
    } <= paths


def test_allow_patterns_are_file_paths():
    spec = mr.get_spec("whisper-small")
    assert spec.allow_patterns == [f.path for f in spec.files]
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_model_registry.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.services.common.model_registry'`)

- [ ] **Step 3: Реалізувати**

```python
# src/services/common/model_registry.py
"""Pinned model registry (spec §4.7).

Every model a service may load is listed here with an immutable HF revision and
the size + sha256 of each file. RT_MODEL accepts only these aliases or an
absolute directory path. Values were produced by scripts/model_tools/pin_model.py.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileSpec:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    service: str  # "asr" | "mt" | "tts"
    repo_id: str
    revision: str
    license: str
    files: tuple[FileSpec, ...]

    @property
    def allow_patterns(self) -> list[str]:
        return [f.path for f in self.files]


class UnknownModel(LookupError):
    """Raised for an alias that is not in the registry."""


_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        alias="whisper-small",
        service="asr",
        repo_id="Systran/faster-whisper-small",
        revision="536b0662742c02347bc0e980a01041f333bce120",
        license="MIT",
        files=(
            FileSpec("config.json", 2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
            FileSpec("model.bin", 483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
            FileSpec("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
            FileSpec("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
        ),
    ),
    ModelSpec(
        alias="whisper-medium",
        service="asr",
        repo_id="Systran/faster-whisper-medium",
        revision="08e178d48790749d25932bbc082711ddcfdfbc4f",
        license="MIT",
        files=(
            FileSpec("config.json", 2257, "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807"),
            FileSpec("model.bin", 1527906378, "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae"),
            FileSpec("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
            FileSpec("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
        ),
    ),
    ModelSpec(
        alias="whisper-large-v3-turbo",
        service="asr",
        repo_id="dropbox-dash/faster-whisper-large-v3-turbo",
        revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        license="MIT",
        files=(
            FileSpec("config.json", 2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
            FileSpec("model.bin", 1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
            FileSpec("preprocessor_config.json", 340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
            FileSpec("tokenizer.json", 2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
            FileSpec("vocabulary.json", 1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
        ),
    ),
    ModelSpec(
        alias="nllb-200-distilled-600M-ct2-int8",
        service="mt",
        repo_id="JustFrederik/nllb-200-distilled-600M-ct2-int8",
        revision="302d78f00e6fdb50a1064059df7c392b735e9d05",
        license="CC-BY-NC-4.0",
        files=(
            FileSpec("config.json", 159, "0c2f6fa2057c7264d052fb4a62ba3476eeae70487acddfa8e779a53a00cbf44c"),
            FileSpec("model.bin", 622595991, "ed1beaf75134de7505315a5223162f56acff397eff6b50638a500d3936fe707b"),
            FileSpec("sentencepiece.bpe.model", 4852054, "14bb8dfb35c0ffdea7bc01e56cea38b9e3d5efcdcb9c251d6b40538e1aab555a"),
            FileSpec("shared_vocabulary.txt", 2568098, "a132a83330f45514c2476eb81d1d69b3c41762264d16ce0a7ea982e5d6c728e5"),
        ),
    ),
    ModelSpec(
        alias="kokoro-82m-v1.0-onnx",
        service="tts",
        repo_id="onnx-community/Kokoro-82M-v1.0-ONNX",
        revision="1939ad2a8e416c0acfeecc08a694d14ef25f2231",
        license="Apache-2.0",
        files=(
            FileSpec("onnx/model.onnx", 325532232, "8fbea51ea711f2af382e88c833d9e288c6dc82ce5e98421ea61c058ce21a34cb"),
            FileSpec("onnx/model_fp16.onnx", 163234740, "ba4527a874b42b21e35f468c10d326fdff3c7fc8cac1f85e9eb6c0dfc35c334a"),
            FileSpec("onnx/model_quantized.onnx", 92361116, "fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478"),
            FileSpec("tokenizer.json", 3497, "77a02c8e164413299b4b4c403b14f8e0e1c1b727db4d46a09d6327b861060a34"),
            FileSpec("voices/af_alloy.bin", 522240, "c4a6b876047fd7fb472edf4ebd63cfac7c3b958a7cae7c106e8f038ca6308c45"),
            FileSpec("voices/af_aoede.bin", 522240, "4a004c33430762e2461eedb2013fad808ef4ab3121f5300f554476caf58d8361"),
            FileSpec("voices/af_bella.bin", 522240, "f69d836209b78eb8c66e75e3cda491e26ea838a3674257e9d4e5703cbaf55c8b"),
            FileSpec("voices/af_heart.bin", 522240, "d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b"),
            FileSpec("voices/af_jessica.bin", 522240, "a240a5e3c15b43563d6e923bdca8ef5613a23471d9b77653694012435df23bd8"),
            FileSpec("voices/af_kore.bin", 522240, "9be5221b6a941c04b561959b8ff0b06e809444dcc4ab7e75a7b23606f691819e"),
            FileSpec("voices/af_nicole.bin", 522240, "cd2191ab31b914ed7b318416b0e4440fdf392ddad9106a060819aa600a64f59a"),
            FileSpec("voices/af_nova.bin", 522240, "18778272caa0d0eebaea251c35fd635f038434f9eee5e691d02a174bd328414f"),
            FileSpec("voices/af_river.bin", 522240, "00a2bcf82b1d86e8f19902ede58c65ccf6c0e43b44b7d74fad54e5d8933c9c30"),
            FileSpec("voices/af_sarah.bin", 522240, "4409fbc125afabacc615d94db5398d847006a737b0247d6892b7a9a0007a2f0a"),
            FileSpec("voices/af_sky.bin", 522240, "4435255c9744f3f31659e0d714ab7689bf65d9e77ec1cce060f083912614f0b9"),
            FileSpec("voices/am_adam.bin", 522240, "162b035ed91cfc48b6046982184c645f72edcdd1b82843347f605d7bf7b15716"),
            FileSpec("voices/am_echo.bin", 522240, "3968b92c3c4cd1c4416dbded36c13eaa388a90d5788d02a13e4d781f5f8cf3c3"),
            FileSpec("voices/am_eric.bin", 522240, "e8b5be17edd1e3636901ce7598baafe2dc8dd8ff707a0c23bf9e461add7e2832"),
            FileSpec("voices/am_fenrir.bin", 522240, "c27989f741f7ee34d273a39d8a595cc0837d35f5ced9a29b7cc162614616df43"),
            FileSpec("voices/am_liam.bin", 522240, "52403be32fd047c6a44517cb0bcd6b134f2a18baa73e70ef41651e0eab921ade"),
            FileSpec("voices/am_michael.bin", 522240, "1d1f21dd8da39c30705cd4c75d039d265e9bc4a2a93ed09bc9e1b1225eb95ba1"),
            FileSpec("voices/am_onyx.bin", 522240, "da5d135b424164916d75a68ffb4c2abce3d7d5ccc82dd1ee6cf447ce286145e6"),
            FileSpec("voices/am_puck.bin", 522240, "fcf73c989033e9233e0b98713eca600c8c74dcc1614b37009d5450ff4a2274a0"),
            FileSpec("voices/am_santa.bin", 522240, "61150cf726ab6c5ed7a99f90a304f91f5a72c00c592e89ec94e5df11c319227a"),
            FileSpec("voices/bf_alice.bin", 522240, "08afa6ba24da61ea5e8efa139e5aadc938d83f0a6da5a900adaf763ac1da5573"),
            FileSpec("voices/bf_emma.bin", 522240, "669fe0647f9dd04fcab92f1439a40eeb4c8b4ab1f82e4996fe3d918ce4a63b73"),
            FileSpec("voices/bf_isabella.bin", 522240, "3754352c4aaa46d17f27654ab7518d65b62ad6163a0f55a5f4330c2da2c4e94f"),
            FileSpec("voices/bf_lily.bin", 522240, "5e0ee32ebe64a467124976b14e69590746f1c4ce41a12b587a50c862edfea335"),
            FileSpec("voices/bm_daniel.bin", 522240, "6b3194bbceffb746733cbc22c8f593dd44e401a71d53895a2dca891bc595a1e8"),
            FileSpec("voices/bm_fable.bin", 522240, "f889083196807b4adb15e9204252165f503b8d33d3982e681c52443c49d798f1"),
            FileSpec("voices/bm_george.bin", 522240, "c4b235a4c1f2cd3b939fed08b899ce9385638b763f7b73a59616c4fc9bd6c9bc"),
            FileSpec("voices/bm_lewis.bin", 522240, "b8f671cef828c30e66fdf0b0756a76bba58f6bb3398cbbf27058642acbcedb97"),
        ),
    ),
    ModelSpec(
        alias="kokoro-82m-v1.0-torch",
        service="tts",
        repo_id="hexgrad/Kokoro-82M",
        revision="f3ff3571791e39611d31c381e3a41a3af07b4987",
        license="Apache-2.0",
        files=(
            FileSpec("config.json", 2351, "5abb01e2403b072bf03d04fde160443e209d7a0dad49a423be15196b9b43c17f"),
            FileSpec("kokoro-v1_0.pth", 327212226, "496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4"),
            FileSpec("voices/af_alloy.pt", 523425, "6d877149dd8b348fbad12e5845b7e43d975390e9f3b68a811d1d86168bef5aa3"),
            FileSpec("voices/af_aoede.pt", 523425, "c03bd1a4c3716c2d8eaa3d50022f62d5c31cfbd6e15933a00b17fefe13841cc4"),
            FileSpec("voices/af_bella.pt", 523425, "8cb64e02fcc8de0327a8e13817e49c76c945ecf0052ceac97d3081480e8e48d6"),
            FileSpec("voices/af_heart.pt", 523425, "0ab5709b8ffab19bfd849cd11d98f75b60af7733253ad0d67b12382a102cb4ff"),
            FileSpec("voices/af_jessica.pt", 523435, "cdfdccb8cc975aa34ee6b89642963b0064237675de0e41a30ae64cc958dd4e87"),
            FileSpec("voices/af_kore.pt", 523420, "8bfbc512321c3db49dff984ac675fa5ac7eaed5a96cc31104d3a9080e179d69d"),
            FileSpec("voices/af_nicole.pt", 523430, "c5561808bcf5250fe8c5f5de32caf2d94f27e57e95befdb098c5c85991d4c5da"),
            FileSpec("voices/af_nova.pt", 523420, "e0233676ddc21908c37a1f102f6b88a59e4e5c1bd764983616eb9eda629dbcd2"),
            FileSpec("voices/af_river.pt", 523425, "e149459bd9c084416b74756b9bd3418256a8b839088abb07d463730c369dab8f"),
            FileSpec("voices/af_sarah.pt", 523425, "49bd364ea3be9eb3e9685e8f9a15448c4883112a7c0ff7ab139fa4088b08cef9"),
            FileSpec("voices/af_sky.pt", 523351, "c799548aed06e0cb0d655a85a01b48e7f10484d71663f9a3045a5b9362e8512c"),
            FileSpec("voices/am_adam.pt", 523420, "ced7e284aba12472891be1da3ab34db84cc05cc02b5889535796dbf2d8b0cb34"),
            FileSpec("voices/am_echo.pt", 523420, "8bcfdc852bc985fb45c396c561e571ffb9183930071f962f1b50df5c97b161e8"),
            FileSpec("voices/am_eric.pt", 523420, "ada66f0eefff34ec921b1d7474d7ac8bec00cd863c170f1c534916e9b8212aae"),
            FileSpec("voices/am_fenrir.pt", 523430, "98e507eca1db08230ae3b6232d59c10aec9630022d19accac4f5d12fcec3c37a"),
            FileSpec("voices/am_liam.pt", 523420, "c82550757ddb31308b97f30040dda8c2d609a9e2de6135848d0a948368138518"),
            FileSpec("voices/am_michael.pt", 523435, "9a443b79a4b22489a5b0ab7c651a0bcd1a30bef675c28333f06971abbd47bd37"),
            FileSpec("voices/am_onyx.pt", 523420, "e8452be16cd0f6da7b4579eaf7b1e4506e92524882053d86d72b96b9a7fed584"),
            FileSpec("voices/am_puck.pt", 523420, "dd1d8973f4ce4b7d8ae407c77a435f485dabc052081b80ea75c4f30b84f36223"),
            FileSpec("voices/am_santa.pt", 523425, "7f2f7582fa2b1f160e90aafe6d0b442a685e773608b6667e545d743b073e97a7"),
            FileSpec("voices/bf_alice.pt", 523425, "d292651b6af6c0d81705c2580dcb4463fccc0ff7b8d618a471dbb4e45655b3f3"),
            FileSpec("voices/bf_emma.pt", 523420, "d0a423deabf4a52b4f49318c51742c54e21bb89bbbe9a12141e7758ddb5da701"),
            FileSpec("voices/bf_isabella.pt", 523440, "cdd4c37003805104d1d08fb1e05855c8fb2c68de24ca6e71f264a30aaa59eefd"),
            FileSpec("voices/bf_lily.pt", 523420, "6e09c2e481e2d53004d7e5ae7d3a325369e130a6f45c35a6002de75084be9285"),
            FileSpec("voices/bm_daniel.pt", 523430, "fc3fce4e9c12ed4dbc8fa9680cfe51ee190a96444ce7c3ad647549a30823fc5d"),
            FileSpec("voices/bm_fable.pt", 523425, "d44935f3135257a9064df99f007fc1342ff1aa767552b4a4fa4c3b2e6e59079c"),
            FileSpec("voices/bm_george.pt", 523430, "f1bc812213dc59774769e5c80004b13eeb79bd78130b11b2d7f934542dab811b"),
            FileSpec("voices/bm_lewis.pt", 523425, "b5204750dcba01029d2ac9cec17aec3b20a6d64073c579d694a23cb40effbd0e"),
        ),
    ),
)

REGISTRY: dict[str, ModelSpec] = {spec.alias: spec for spec in _SPECS}


def get_spec(alias: str) -> ModelSpec:
    try:
        return REGISTRY[alias]
    except KeyError:
        raise UnknownModel(
            f"unknown model alias {alias!r}; known: {', '.join(sorted(REGISTRY))}"
        ) from None


def specs_for(service: str) -> list[ModelSpec]:
    return [spec for spec in _SPECS if spec.service == service]
```

- [ ] **Step 4: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_model_registry.py -q`
Expected: PASS (усі тести)

- [ ] **Step 5: Commit**

```bash
git add src/services/common/model_registry.py tests/services/test_rt_model_registry.py
git commit -m "feat(models): add pinned model registry for asr/mt/tts"
```

---

### Task 3: HF-середовище і токен

**Files:**
- Create: `src/services/common/hf_env.py`
- Test: `tests/services/test_rt_hf_env.py`

**Interfaces:**
- Produces: `configure_hf_home(model_dir: Path) -> Path` (створює `<model_dir>/hf`, ставить `HF_HOME`, `HF_HUB_DISABLE_TELEMETRY=1`); `set_offline() -> None` (`HF_HUB_OFFLINE=1` + патч `huggingface_hub.constants.HF_HUB_OFFLINE`, якщо модуль уже імпортовано); `read_token_file(path: str | None) -> str | None`.

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_hf_env.py
import os
import sys
import types

import pytest

from src.services.common import hf_env


def test_configure_hf_home(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)
    hf = hf_env.configure_hf_home(tmp_path)
    assert hf == tmp_path / "hf"
    assert hf.is_dir()
    assert os.environ["HF_HOME"] == str(hf)
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_set_offline(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    hf_env.set_offline()
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_set_offline_patches_imported_hub(monkeypatch):
    fake_constants = types.SimpleNamespace(HF_HUB_OFFLINE=False)
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", fake_constants)
    hf_env.set_offline()
    assert fake_constants.HF_HUB_OFFLINE is True


def test_read_token_file(tmp_path):
    f = tmp_path / "tok"
    f.write_text("  hf_abc123\n")
    assert hf_env.read_token_file(str(f)) == "hf_abc123"


def test_read_token_file_none():
    assert hf_env.read_token_file(None) is None
    assert hf_env.read_token_file("") is None


def test_read_token_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="RT_HF_TOKEN_FILE"):
        hf_env.read_token_file(str(tmp_path / "missing"))


def test_read_token_file_empty(tmp_path):
    f = tmp_path / "tok"
    f.write_text("\n")
    with pytest.raises(ValueError, match="empty"):
        hf_env.read_token_file(str(f))
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_hf_env.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати**

```python
# src/services/common/hf_env.py
"""Hugging Face environment handling (spec §4.7).

huggingface_hub reads HF_HOME / HF_HUB_OFFLINE at import time, so configure_hf_home
must run before any engine imports faster_whisper / kokoro / huggingface_hub.
set_offline additionally patches an already-imported huggingface_hub.constants
(our own downloader may have imported it during provisioning).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_hf_home(model_dir: Path) -> Path:
    hf_home = Path(model_dir) / "hf"
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf_home)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return hf_home


def set_offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    hub_constants = sys.modules.get("huggingface_hub.constants")
    if hub_constants is not None:
        hub_constants.HF_HUB_OFFLINE = True


def read_token_file(path: str | None) -> str | None:
    if not path:
        return None
    token_path = Path(path)
    if not token_path.is_file():
        raise FileNotFoundError(f"RT_HF_TOKEN_FILE not found: {path}")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"RT_HF_TOKEN_FILE is empty: {path}")
    return token
```

- [ ] **Step 4: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_hf_env.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/common/hf_env.py tests/services/test_rt_hf_env.py
git commit -m "feat(models): add HF env helpers (HF_HOME, offline, token file)"
```

---

### Task 4: Device / compute-type / threads

**Files:**
- Create: `src/services/common/device.py`
- Test: `tests/services/test_rt_device.py`

**Interfaces:**
- Produces: `DeviceError(RuntimeError)`; `normalize_device(value: str | None) -> str` (`cpu|cuda|rocm`); `backend_device(device: str) -> str` (`cpu`→`cpu`, `cuda|rocm`→`cuda`); `default_compute_type(service: str, device: str) -> str`; `default_model(service: str, device: str) -> str`; `default_beams(service: str, device: str) -> int`; `default_threads(env: Mapping[str, str] | None = None) -> int`; `resolve_ct2_compute_type(requested: str, supported: set[str]) -> str`; `require_gpu(device: str, gpu_count: int) -> None`.

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_device.py
import logging
import os

import pytest

from src.services.common import device as dv


def test_normalize_device():
    assert dv.normalize_device(None) == "cpu"
    assert dv.normalize_device("") == "cpu"
    assert dv.normalize_device(" CUDA ") == "cuda"
    assert dv.normalize_device("rocm") == "rocm"
    with pytest.raises(dv.DeviceError, match="tpu"):
        dv.normalize_device("tpu")


def test_backend_device():
    assert dv.backend_device("cpu") == "cpu"
    assert dv.backend_device("cuda") == "cuda"
    assert dv.backend_device("rocm") == "cuda"


@pytest.mark.parametrize(
    "service,device,expected",
    [
        ("asr", "cpu", "int8"),
        ("asr", "cuda", "int8_float16"),
        ("asr", "rocm", "int8_float16"),
        ("mt", "cpu", "int8"),
        ("mt", "cuda", "int8_float16"),
        ("tts", "cpu", "float32"),
        ("tts", "cuda", "float32"),
    ],
)
def test_default_compute_type(service, device, expected):
    assert dv.default_compute_type(service, device) == expected


def test_tts_has_no_rocm():
    with pytest.raises(dv.DeviceError, match="rocm"):
        dv.default_compute_type("tts", "rocm")


@pytest.mark.parametrize(
    "service,device,expected",
    [
        ("asr", "cpu", "whisper-small"),
        ("asr", "rocm", "whisper-small"),
        ("asr", "cuda", "whisper-large-v3-turbo"),
        ("mt", "cpu", "nllb-200-distilled-600M-ct2-int8"),
        ("mt", "cuda", "nllb-200-distilled-600M-ct2-int8"),
        ("tts", "cpu", "kokoro-82m-v1.0-onnx"),
        ("tts", "cuda", "kokoro-82m-v1.0-onnx"),
    ],
)
def test_default_model(service, device, expected):
    assert dv.default_model(service, device) == expected


def test_default_beams():
    assert dv.default_beams("asr", "cpu") == 1
    assert dv.default_beams("asr", "cuda") == 3
    assert dv.default_beams("mt", "cpu") == 2
    assert dv.default_beams("mt", "cuda") == 4


def test_default_threads():
    assert dv.default_threads({"RT_NUM_THREADS": "3"}) == 3
    assert dv.default_threads({}) == min(4, os.cpu_count() or 1)
    with pytest.raises(dv.DeviceError):
        dv.default_threads({"RT_NUM_THREADS": "0"})
    with pytest.raises(dv.DeviceError):
        dv.default_threads({"RT_NUM_THREADS": "many"})


def test_resolve_ct2_compute_type(caplog):
    assert dv.resolve_ct2_compute_type("int8_float16", {"int8_float16", "int8"}) == "int8_float16"
    with caplog.at_level(logging.WARNING):
        assert dv.resolve_ct2_compute_type("int8_float16", {"int8", "float32"}) == "int8"
    assert "falling back" in caplog.text
    assert dv.resolve_ct2_compute_type("float16", {"float32"}) == "float32"
    with pytest.raises(dv.DeviceError):
        dv.resolve_ct2_compute_type("int8", set())


def test_require_gpu():
    dv.require_gpu("cpu", 0)
    dv.require_gpu("cuda", 1)
    with pytest.raises(dv.DeviceError, match="no GPU"):
        dv.require_gpu("cuda", 0)
    with pytest.raises(dv.DeviceError):
        dv.require_gpu("rocm", 0)
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_device.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати**

```python
# src/services/common/device.py
"""RT_DEVICE / RT_COMPUTE_TYPE / RT_NUM_THREADS resolution (spec §4.2-§4.4, §4.8)."""
from __future__ import annotations

import logging
import os
from typing import Mapping

log = logging.getLogger(__name__)

DEVICES = ("cpu", "cuda", "rocm")


class DeviceError(RuntimeError):
    """Invalid or unavailable device / compute configuration."""


_DEFAULT_COMPUTE = {
    ("asr", "cpu"): "int8",
    ("asr", "cuda"): "int8_float16",
    ("asr", "rocm"): "int8_float16",
    ("mt", "cpu"): "int8",
    ("mt", "cuda"): "int8_float16",
    ("mt", "rocm"): "int8_float16",
    ("tts", "cpu"): "float32",
    ("tts", "cuda"): "float32",
}

_DEFAULT_MODEL = {
    ("asr", "cpu"): "whisper-small",
    ("asr", "cuda"): "whisper-large-v3-turbo",
    ("asr", "rocm"): "whisper-small",
    ("mt", "cpu"): "nllb-200-distilled-600M-ct2-int8",
    ("mt", "cuda"): "nllb-200-distilled-600M-ct2-int8",
    ("mt", "rocm"): "nllb-200-distilled-600M-ct2-int8",
    ("tts", "cpu"): "kokoro-82m-v1.0-onnx",
    ("tts", "cuda"): "kokoro-82m-v1.0-onnx",
}

_DEFAULT_BEAMS = {
    ("asr", "cpu"): 1,
    ("asr", "cuda"): 3,
    ("asr", "rocm"): 3,
    ("mt", "cpu"): 2,
    ("mt", "cuda"): 4,
    ("mt", "rocm"): 4,
}

# Preference chains when a CT2 build/device lacks the requested type.
_CT2_FALLBACK = {
    "int8_float16": ("int8_float16", "int8", "int8_float32", "float32"),
    "int8_bfloat16": ("int8_bfloat16", "int8_float16", "int8", "float32"),
    "int8_float32": ("int8_float32", "int8", "float32"),
    "int8": ("int8", "int8_float32", "float32"),
    "float16": ("float16", "float32"),
    "bfloat16": ("bfloat16", "float16", "float32"),
    "float32": ("float32",),
}


def normalize_device(value: str | None) -> str:
    device = (value or "cpu").strip().lower()
    if device not in DEVICES:
        raise DeviceError(f"RT_DEVICE must be one of {DEVICES}, got {value!r}")
    return device


def backend_device(device: str) -> str:
    # CTranslate2 HIP builds and ORT keep the "cuda" device string (spec A5).
    return "cpu" if device == "cpu" else "cuda"


def _lookup(table: dict, service: str, device: str, what: str):
    try:
        return table[(service, device)]
    except KeyError:
        raise DeviceError(f"no {what} for service={service!r} device={device!r} "
                          f"(rt-tts has no rocm variant, spec D2)") from None


def default_compute_type(service: str, device: str) -> str:
    return _lookup(_DEFAULT_COMPUTE, service, device, "compute type")


def default_model(service: str, device: str) -> str:
    return _lookup(_DEFAULT_MODEL, service, device, "default model")


def default_beams(service: str, device: str) -> int:
    return _lookup(_DEFAULT_BEAMS, service, device, "default beam size")


def default_threads(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    raw = env.get("RT_NUM_THREADS")
    if not raw:
        return min(4, os.cpu_count() or 1)
    try:
        threads = int(raw)
    except ValueError:
        raise DeviceError(f"RT_NUM_THREADS must be an integer, got {raw!r}") from None
    if threads < 1:
        raise DeviceError(f"RT_NUM_THREADS must be >= 1, got {threads}")
    return threads


def resolve_ct2_compute_type(requested: str, supported: set[str]) -> str:
    for candidate in _CT2_FALLBACK.get(requested, (requested,)):
        if candidate in supported:
            if candidate != requested:
                log.warning("compute_type %s not supported here; falling back to %s",
                            requested, candidate)
            return candidate
    raise DeviceError(f"compute_type {requested!r} unsupported; supported={sorted(supported)}")


def require_gpu(device: str, gpu_count: int) -> None:
    if device != "cpu" and gpu_count < 1:
        raise DeviceError(f"RT_DEVICE={device} but no GPU is visible to the backend")
```

- [ ] **Step 4: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_device.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/common/device.py tests/services/test_rt_device.py
git commit -m "feat(models): add device/compute-type/thread defaults per service"
```

---

### Task 5: Сховище моделей (ensure / verify / manifest / lock)

**Files:**
- Create: `src/services/common/model_store.py`
- Test: `tests/services/test_rt_model_store.py`

**Interfaces:**
- Consumes: `ModelSpec`, `FileSpec`, `get_spec`, `UnknownModel` (Task 2); `configure_hf_home`, `set_offline`, `read_token_file` (Task 3).
- Produces:
  - `MANIFEST = ".rt-manifest.json"`; `ModelNotProvisioned(RuntimeError)`; `ModelIntegrityError(RuntimeError)`;
  - `Downloader = Callable[[ModelSpec, Path, str | None], None]`; `hf_downloader(spec, target_dir, token)`;
  - `sha256_file(path: Path) -> str`; `parse_bool(value: str, name: str) -> bool`;
  - `class ModelStore(root: Path, downloader: Downloader = hf_downloader)` з `model_path(alias) -> Path`, `verify(spec, full=False) -> list[tuple[str, str]]` (kind `"missing"|"mismatch"`, message), `ensure(spec, *, download: bool, token: str | None = None, full_verify: bool = False) -> Path`;
  - `resolve_model(value: str, store: ModelStore, *, download: bool, token: str | None = None, full_verify: bool = False, lookup=get_spec) -> Path`;
  - `@dataclass ModelSettings(model_dir: Path, download: bool, full_verify: bool, token_file: str | None)` з `from_env(env=None)`, `store()`, `token()`;
  - `prepare_model(value: str, settings: ModelSettings) -> Path` (HF_HOME → resolve → offline).

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_model_store.py
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from src.services.common import model_store as ms
from src.services.common.model_registry import FileSpec, ModelSpec

FILES = {"model.bin": b"weights-123", "sub/config.json": b'{"a": 1}'}


def make_spec(files=FILES, alias="m", revision="a" * 40):
    return ModelSpec(
        alias=alias,
        service="mt",
        repo_id="org/m",
        revision=revision,
        license="MIT",
        files=tuple(FileSpec(p, len(b), hashlib.sha256(b).hexdigest()) for p, b in files.items()),
    )


class FakeDownloader:
    def __init__(self, files=FILES, corrupt=None, fail=None, delay=0.0):
        self.files, self.corrupt, self.fail, self.delay = files, corrupt or {}, fail, delay
        self.calls = 0
        self.tokens = []

    def __call__(self, spec, target, token):
        self.calls += 1
        self.tokens.append(token)
        time.sleep(self.delay)
        if self.fail:
            raise self.fail
        for path, data in self.files.items():
            dst = Path(target) / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(self.corrupt.get(path, data))


def test_ensure_downloads_and_writes_manifest(tmp_path):
    dl = FakeDownloader()
    store = ms.ModelStore(tmp_path, downloader=dl)
    path = store.ensure(make_spec(), download=True, token="tok")
    assert path == tmp_path / "rt" / "m"
    assert (path / "model.bin").read_bytes() == b"weights-123"
    manifest = json.loads((path / ms.MANIFEST).read_text())
    assert manifest["revision"] == "a" * 40
    assert manifest["files"]["model.bin"]["sha256"] == hashlib.sha256(b"weights-123").hexdigest()
    assert dl.tokens == ["tok"]
    assert not (tmp_path / "rt" / "m.partial").exists()


def test_ensure_is_idempotent(tmp_path):
    dl = FakeDownloader()
    store = ms.ModelStore(tmp_path, downloader=dl)
    store.ensure(make_spec(), download=True)
    store.ensure(make_spec(), download=True)
    assert dl.calls == 1


def test_ensure_without_download_raises(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader())
    with pytest.raises(ms.ModelNotProvisioned, match="rt-models fetch m"):
        store.ensure(make_spec(), download=False)


def test_corrupt_download_is_rejected(tmp_path):
    dl = FakeDownloader(corrupt={"model.bin": b"weights-XYZ"})
    store = ms.ModelStore(tmp_path, downloader=dl)
    with pytest.raises(ms.ModelIntegrityError, match="model.bin"):
        store.ensure(make_spec(), download=True)
    assert not (tmp_path / "rt" / "m").exists()
    assert not (tmp_path / "rt" / "m.partial").exists()


def test_failed_download_cleans_partial(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader(fail=OSError("net down")))
    with pytest.raises(ms.ModelNotProvisioned, match="net down"):
        store.ensure(make_spec(), download=True)
    assert not (tmp_path / "rt" / "m.partial").exists()


def test_stale_partial_is_replaced(tmp_path):
    junk = tmp_path / "rt" / "m.partial"
    junk.mkdir(parents=True)
    (junk / "junk.bin").write_bytes(b"old")
    path = ms.ModelStore(tmp_path, downloader=FakeDownloader()).ensure(make_spec(), download=True)
    assert not junk.exists()
    assert not (path / "junk.bin").exists()


def test_verify_quick_vs_full(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader())
    spec = make_spec()
    path = store.ensure(spec, download=True)
    (path / "model.bin").write_bytes(b"weights-999")  # same size, different content
    assert store.verify(spec, full=False) == []
    problems = store.verify(spec, full=True)
    assert problems and problems[0][0] == "mismatch"


def test_revision_change_triggers_redownload(tmp_path):
    dl = FakeDownloader()
    store = ms.ModelStore(tmp_path, downloader=dl)
    store.ensure(make_spec(), download=True)
    store.ensure(make_spec(revision="b" * 40), download=True)
    assert dl.calls == 2


def test_concurrent_ensure_downloads_once(tmp_path):
    dl = FakeDownloader(delay=0.3)
    spec = make_spec()
    errors = []

    def worker():
        try:
            ms.ModelStore(tmp_path, downloader=dl).ensure(spec, download=True)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert dl.calls == 1


def test_resolve_alias_uses_lookup(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader())
    path = ms.resolve_model("m", store, download=True, lookup=lambda alias: make_spec(alias=alias))
    assert path == tmp_path / "rt" / "m"


def test_resolve_abs_path_without_manifest(tmp_path, caplog):
    model_dir = tmp_path / "own"
    model_dir.mkdir()
    store = ms.ModelStore(tmp_path / "store", downloader=FakeDownloader())
    assert ms.resolve_model(str(model_dir), store, download=False) == model_dir
    assert "unmanaged" in caplog.text


def test_resolve_abs_path_missing(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader())
    with pytest.raises(ms.ModelNotProvisioned, match="does not exist"):
        ms.resolve_model(str(tmp_path / "nope"), store, download=False)


def test_resolve_abs_path_file_rejected(tmp_path):
    f = tmp_path / "model.bin"
    f.write_bytes(b"x")
    store = ms.ModelStore(tmp_path / "store", downloader=FakeDownloader())
    with pytest.raises(ms.ModelNotProvisioned, match="not a directory"):
        ms.resolve_model(str(f), store, download=False)


def test_resolve_abs_path_with_bad_manifest(tmp_path):
    model_dir = tmp_path / "own"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"short")
    (model_dir / ms.MANIFEST).write_text(json.dumps(
        {"files": {"model.bin": {"size": 999, "sha256": "0" * 64}}}))
    store = ms.ModelStore(tmp_path / "store", downloader=FakeDownloader())
    with pytest.raises(ms.ModelIntegrityError, match="model.bin"):
        ms.resolve_model(str(model_dir), store, download=False)


def test_settings_from_env_defaults(monkeypatch):
    s = ms.ModelSettings.from_env({})
    assert s.model_dir == Path.home() / ".cache" / "rt-speech" / "models"
    assert s.download is True and s.full_verify is False and s.token_file is None


def test_settings_from_env_values(tmp_path):
    s = ms.ModelSettings.from_env({
        "RT_MODEL_DIR": str(tmp_path),
        "RT_MODEL_DOWNLOAD": "false",
        "RT_MODEL_VERIFY": "FULL",
        "RT_HF_TOKEN_FILE": "/run/secrets/hg-token",
    })
    assert s.model_dir == tmp_path and s.download is False and s.full_verify is True
    assert s.token_file == "/run/secrets/hg-token"


def test_settings_reject_bad_values():
    with pytest.raises(ValueError, match="RT_MODEL_DOWNLOAD"):
        ms.ModelSettings.from_env({"RT_MODEL_DOWNLOAD": "maybe"})
    with pytest.raises(ValueError, match="RT_MODEL_VERIFY"):
        ms.ModelSettings.from_env({"RT_MODEL_VERIFY": "deep"})


def test_prepare_model_sets_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    model_dir = tmp_path / "own"
    model_dir.mkdir()
    settings = ms.ModelSettings(model_dir=tmp_path / "models", download=False,
                                full_verify=False, token_file=None)
    assert ms.prepare_model(str(model_dir), settings) == model_dir
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HOME"] == str(tmp_path / "models" / "hf")
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_model_store.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати**

```python
# src/services/common/model_store.py
"""Model provisioning into <RT_MODEL_DIR>/rt/<alias> (spec §4.7)."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional

from .hf_env import configure_hf_home, read_token_file, set_offline
from .model_registry import ModelSpec, get_spec

log = logging.getLogger(__name__)

MANIFEST = ".rt-manifest.json"
Downloader = Callable[[ModelSpec, Path, Optional[str]], None]
Problem = tuple[str, str]  # (kind: "missing" | "mismatch", message)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ModelNotProvisioned(RuntimeError):
    """Model files are absent and cannot (or may not) be downloaded."""


class ModelIntegrityError(RuntimeError):
    """Model files do not match the pinned size/sha256."""


def parse_bool(value: str, name: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean (true/false), got {value!r}")


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def hf_downloader(spec: ModelSpec, target_dir: Path, token: str | None) -> None:
    from huggingface_hub import snapshot_download  # lazy: HF_HOME must be set first

    snapshot_download(
        repo_id=spec.repo_id,
        revision=spec.revision,
        local_dir=str(target_dir),
        allow_patterns=spec.allow_patterns,
        token=token,
    )


class ModelStore:
    def __init__(self, root: Path, downloader: Downloader = hf_downloader):
        self.root = Path(root)
        self._downloader = downloader

    def model_path(self, alias: str) -> Path:
        return self.root / "rt" / alias

    @contextmanager
    def _lock(self, alias: str) -> Iterator[None]:
        lock_path = self.root / "rt" / f"{alias}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def verify(self, spec: ModelSpec, full: bool = False) -> list[Problem]:
        path = self.model_path(spec.alias)
        manifest_path = path / MANIFEST
        if not manifest_path.is_file():
            return [("missing", f"no manifest in {path}")]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("revision") != spec.revision:
            return [("mismatch", f"revision {manifest.get('revision')} != pinned {spec.revision}")]
        problems: list[Problem] = []
        for f in spec.files:
            file_path = path / f.path
            if not file_path.is_file():
                problems.append(("missing", f"missing file {f.path}"))
            elif file_path.stat().st_size != f.size:
                problems.append(("mismatch", f"size mismatch for {f.path}"))
            elif full and sha256_file(file_path) != f.sha256:
                problems.append(("mismatch", f"sha256 mismatch for {f.path}"))
        return problems

    def ensure(self, spec: ModelSpec, *, download: bool, token: str | None = None,
               full_verify: bool = False) -> Path:
        path = self.model_path(spec.alias)
        with self._lock(spec.alias):
            problems = self.verify(spec, full=full_verify)
            if not problems:
                return path
            if not download:
                raise ModelNotProvisioned(
                    f"model {spec.alias} not provisioned in {path}: {problems[0][1]}; "
                    f"run: rt-models fetch {spec.alias}")
            self._install(spec, token)
            return path

    def _install(self, spec: ModelSpec, token: str | None) -> None:
        final = self.model_path(spec.alias)
        partial = final.with_name(final.name + ".partial")
        if partial.exists():
            shutil.rmtree(partial)
        partial.mkdir(parents=True)
        try:
            self._downloader(spec, partial, token)
        except Exception as exc:
            shutil.rmtree(partial, ignore_errors=True)
            raise ModelNotProvisioned(f"download of {spec.alias} failed: {exc}") from exc
        files: dict[str, dict] = {}
        for f in spec.files:
            file_path = partial / f.path
            if not file_path.is_file():
                shutil.rmtree(partial, ignore_errors=True)
                raise ModelIntegrityError(f"{spec.alias}: {f.path} missing after download")
            size = file_path.stat().st_size
            digest = sha256_file(file_path)
            if size != f.size or digest != f.sha256:
                shutil.rmtree(partial, ignore_errors=True)
                raise ModelIntegrityError(
                    f"{spec.alias}: {f.path} size/sha256 mismatch (got {size}, {digest})")
            files[f.path] = {"size": size, "sha256": digest}
        manifest = {
            "alias": spec.alias,
            "repo_id": spec.repo_id,
            "revision": spec.revision,
            "license": spec.license,
            "files": files,
            "verified_at": int(time.time()),
        }
        (partial / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True),
                                        encoding="utf-8")
        if final.exists():
            shutil.rmtree(final)
        os.replace(partial, final)


def _verify_manifest_dir(path: Path) -> None:
    manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    for rel, meta in manifest.get("files", {}).items():
        file_path = path / rel
        if not file_path.is_file() or file_path.stat().st_size != meta["size"]:
            raise ModelIntegrityError(f"{path}: {rel} missing or size differs from manifest")


def resolve_model(value: str, store: ModelStore, *, download: bool, token: str | None = None,
                  full_verify: bool = False,
                  lookup: Callable[[str], ModelSpec] = get_spec) -> Path:
    if os.path.isabs(value):
        path = Path(value)
        if not path.exists():
            raise ModelNotProvisioned(f"model path does not exist: {value}")
        if not path.is_dir():
            raise ModelNotProvisioned(f"model path is not a directory: {value}")
        if (path / MANIFEST).is_file():
            _verify_manifest_dir(path)
        else:
            log.warning("unmanaged model directory (no %s): %s", MANIFEST, path)
        return path
    return store.ensure(lookup(value), download=download, token=token, full_verify=full_verify)


@dataclass(frozen=True)
class ModelSettings:
    model_dir: Path
    download: bool
    full_verify: bool
    token_file: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ModelSettings":
        env = os.environ if env is None else env
        model_dir = Path(env.get("RT_MODEL_DIR") or Path.home() / ".cache" / "rt-speech" / "models")
        download = parse_bool(env.get("RT_MODEL_DOWNLOAD", "true"), "RT_MODEL_DOWNLOAD")
        verify = env.get("RT_MODEL_VERIFY", "quick").strip().lower()
        if verify not in ("quick", "full"):
            raise ValueError(f"RT_MODEL_VERIFY must be quick|full, got {verify!r}")
        return cls(model_dir=model_dir, download=download, full_verify=verify == "full",
                   token_file=env.get("RT_HF_TOKEN_FILE") or None)

    def store(self) -> ModelStore:
        return ModelStore(self.model_dir)

    def token(self) -> str | None:
        return read_token_file(self.token_file)


def prepare_model(value: str, settings: ModelSettings) -> Path:
    """Loader entry point: HF_HOME -> resolve/provision -> HF_HUB_OFFLINE=1."""
    configure_hf_home(settings.model_dir)
    path = resolve_model(value, settings.store(), download=settings.download,
                         token=settings.token(), full_verify=settings.full_verify)
    set_offline()
    return path
```

- [ ] **Step 4: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_model_store.py -q`
Expected: PASS (усі 18 тестів)

- [ ] **Step 5: Commit**

```bash
git add src/services/common/model_store.py tests/services/test_rt_model_store.py
git commit -m "feat(models): add model store with pinned download, sha256 verify and offline mode"
```

---

### Task 6: CLI `rt-models`

**Files:**
- Create: `src/services/common/models_cli.py`
- Modify: `pyproject.toml` (`[project.scripts]`, додати рядок)
- Test: `tests/services/test_rt_models_cli.py`

**Interfaces:**
- Consumes: `REGISTRY`, `get_spec`, `UnknownModel` (Task 2); `ModelStore`, `ModelSettings`, `ModelNotProvisioned`, `ModelIntegrityError` (Task 5); `configure_hf_home` (Task 3).
- Produces: `main(argv: list[str] | None = None, *, registry: dict | None = None, store_factory: Callable[[Path], ModelStore] | None = None) -> int`; exit codes `0` ok, `2` not provisioned / unknown, `3` integrity. Entry point `rt-models`.

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_models_cli.py
import hashlib
from pathlib import Path

import pytest

from src.services.common import model_store as ms
from src.services.common import models_cli
from src.services.common.model_registry import FileSpec, ModelSpec

DATA = b"weights"
SPEC = ModelSpec(alias="tiny", service="asr", repo_id="org/tiny", revision="c" * 40,
                 license="MIT", files=(FileSpec("model.bin", len(DATA), hashlib.sha256(DATA).hexdigest()),))
REGISTRY = {"tiny": SPEC}


@pytest.fixture(autouse=True)
def _hf_env_sandbox(monkeypatch):
    # main() calls configure_hf_home(); monkeypatch restores HF_* env afterwards.
    for name in ("HF_HOME", "HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "RT_HF_TOKEN_FILE"):
        monkeypatch.setenv(name, "")


def fake_downloader(spec, target, token):
    (Path(target) / "model.bin").write_bytes(DATA)


def store_factory(root):
    return ms.ModelStore(root, downloader=fake_downloader)


def run(tmp_path, *args):
    return models_cli.main(["--model-dir", str(tmp_path), *args],
                           registry=REGISTRY, store_factory=store_factory)


def test_list_shows_missing_then_ok(tmp_path, capsys):
    assert run(tmp_path, "list") == 0
    assert "tiny\tasr\tMIT\tmissing" in capsys.readouterr().out
    assert run(tmp_path, "fetch", "tiny") == 0
    capsys.readouterr()
    assert run(tmp_path, "list") == 0
    assert "tiny\tasr\tMIT\tok" in capsys.readouterr().out


def test_verify_missing_returns_2(tmp_path):
    assert run(tmp_path, "verify", "tiny") == 2


def test_verify_full_detects_corruption(tmp_path):
    assert run(tmp_path, "fetch", "tiny") == 0
    (tmp_path / "rt" / "tiny" / "model.bin").write_bytes(b"WEIGHTS")
    assert run(tmp_path, "verify", "tiny") == 0
    assert run(tmp_path, "verify", "--full", "tiny") == 3


def test_path_prints_location(tmp_path, capsys):
    assert run(tmp_path, "path", "tiny") == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "rt" / "tiny")


def test_unknown_alias_returns_2(tmp_path, capsys):
    assert run(tmp_path, "fetch", "nope") == 2
    assert "unknown model alias" in capsys.readouterr().err
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_models_cli.py -q`
Expected: FAIL (`ModuleNotFoundError` / `AttributeError`)

- [ ] **Step 3: Реалізувати**

```python
# src/services/common/models_cli.py
"""rt-models: list / fetch / verify / path for pinned models (spec §4.7)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from .hf_env import configure_hf_home
from .model_registry import REGISTRY as DEFAULT_REGISTRY
from .model_registry import ModelSpec, UnknownModel
from .model_store import ModelIntegrityError, ModelNotProvisioned, ModelSettings, ModelStore


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rt-models", description=__doc__)
    p.add_argument("--model-dir", help="default: $RT_MODEL_DIR or ~/.cache/rt-speech/models")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("aliases", nargs="+")
    verify = sub.add_parser("verify")
    verify.add_argument("--full", action="store_true")
    verify.add_argument("aliases", nargs="*")
    path = sub.add_parser("path")
    path.add_argument("alias")
    return p


def main(argv: list[str] | None = None, *, registry: dict[str, ModelSpec] | None = None,
         store_factory: Callable[[Path], ModelStore] | None = None) -> int:
    args = _parser().parse_args(argv)
    registry = DEFAULT_REGISTRY if registry is None else registry
    settings = ModelSettings.from_env()
    model_dir = Path(args.model_dir) if args.model_dir else settings.model_dir
    configure_hf_home(model_dir)
    store = (store_factory or ModelStore)(model_dir)

    def spec_of(alias: str) -> ModelSpec:
        if alias not in registry:
            raise UnknownModel(f"unknown model alias {alias!r}; known: {', '.join(sorted(registry))}")
        return registry[alias]

    try:
        if args.cmd == "list":
            for spec in registry.values():
                status = "ok" if not store.verify(spec) else "missing"
                print(f"{spec.alias}\t{spec.service}\t{spec.license}\t{status}")
            return 0
        if args.cmd == "path":
            print(store.model_path(spec_of(args.alias).alias))
            return 0
        if args.cmd == "fetch":
            for alias in args.aliases:
                path = store.ensure(spec_of(alias), download=True, token=settings.token(),
                                    full_verify=True)
                print(f"{alias}: ok {path}")
            return 0
        if args.cmd == "verify":
            worst = 0
            for alias in args.aliases or list(registry):
                problems = store.verify(spec_of(alias), full=args.full)
                for kind, message in problems:
                    print(f"{alias}: {kind}: {message}", file=sys.stderr)
                    worst = max(worst, 2 if kind == "missing" else 3)
                if not problems:
                    print(f"{alias}: ok")
            return worst
    except (UnknownModel, ModelNotProvisioned) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ModelIntegrityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Додати entry point у `pyproject.toml`**

Після рядка `translator-ui               = "src.main:main"` додати:

```toml
rt-models                   = "src.services.common.models_cli:main"
```

- [ ] **Step 5: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_models_cli.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/services/common/models_cli.py pyproject.toml tests/services/test_rt_models_cli.py
git commit -m "feat(models): add rt-models CLI (list/fetch/verify/path)"
```

---

## Phase B — MT (NLLB на CTranslate2)

### Task 7: Мовні коди NLLB і розбиття на речення

**Files:**
- Create: `src/services/mt/nllb_codes.py`, `src/services/mt/sentence_split.py`
- Test: `tests/services/test_rt_mt_text.py`

**Interfaces:**
- Produces: `ISO_TO_FLORES: dict[str, str]`; `UnsupportedLanguage(ValueError)`; `to_nllb_code(lang: str) -> str`; `supported_languages() -> list[str]`; `split_sentences(text: str) -> list[str]`.

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_mt_text.py
import pytest

from src.services.mt.nllb_codes import UnsupportedLanguage, supported_languages, to_nllb_code
from src.services.mt.sentence_split import split_sentences


@pytest.mark.parametrize("lang,code", [
    ("uk", "ukr_Cyrl"), ("PL", "pol_Latn"), (" en ", "eng_Latn"),
    ("zh", "zho_Hans"), ("ukr_Cyrl", "ukr_Cyrl"), ("deu_Latn", "deu_Latn"),
])
def test_to_nllb_code(lang, code):
    assert to_nllb_code(lang) == code


@pytest.mark.parametrize("bad", ["xx", "", "ukr_cyrl", "ukrainian"])
def test_to_nllb_code_rejects(bad):
    with pytest.raises(UnsupportedLanguage):
        to_nllb_code(bad)


def test_supported_languages():
    assert {"uk", "pl", "en"} <= set(supported_languages())
    assert supported_languages() == sorted(supported_languages())


@pytest.mark.parametrize("text,expected", [
    ("Привіт. Як справи? Добре!", ["Привіт.", "Як справи?", "Добре!"]),
    ("Це т. зв. приклад", ["Це т. зв. приклад"]),
    ("Ціна 3.5 грн. Далі буде", ["Ціна 3.5 грн.", "Далі буде"]),
    ("Dzień dobry. Łódź jest duża.", ["Dzień dobry.", "Łódź jest duża."]),
    ("Він сказав. «Так»", ["Він сказав.", "«Так»"]),
    ("без пунктуації", ["без пунктуації"]),
    ("  багато   пробілів  ", ["багато пробілів"]),
    ("", []),
    ("   ", []),
])
def test_split_sentences(text, expected):
    assert split_sentences(text) == expected
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_mt_text.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати**

```python
# src/services/mt/nllb_codes.py
"""ISO 639-1 (API) -> FLORES-200 (NLLB) language codes (spec §4.2)."""
from __future__ import annotations

import re

ISO_TO_FLORES: dict[str, str] = {
    "uk": "ukr_Cyrl",
    "en": "eng_Latn",
    "pl": "pol_Latn",
    "de": "deu_Latn",
    "fr": "fra_Latn",
    "es": "spa_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "ru": "rus_Cyrl",
    "ja": "jpn_Jpan",
    "zh": "zho_Hans",
    "ko": "kor_Hang",
    "ar": "arb_Arab",
}

_FLORES_RE = re.compile(r"^[a-z]{3}_[A-Z][a-z]{3}$")


class UnsupportedLanguage(ValueError):
    """Language is not supported by the MT model."""


def to_nllb_code(lang: str) -> str:
    code = (lang or "").strip()
    if _FLORES_RE.match(code):
        return code
    iso = code.lower()
    if iso in ISO_TO_FLORES:
        return ISO_TO_FLORES[iso]
    raise UnsupportedLanguage(f"unsupported language {lang!r}; supported: {supported_languages()}")


def supported_languages() -> list[str]:
    return sorted(ISO_TO_FLORES)
```

Примітка: поточний `src/translate/translate_service.py` мапить `ar` → `ara_Arab`, якого немає у словнику NLLB-200; правильний код — `arb_Arab` (Modern Standard Arabic).

```python
# src/services/mt/sentence_split.py
"""Split text into sentences before NLLB batching (spec §4.2)."""
from __future__ import annotations

import re

_BOUNDARY = re.compile(
    r"(?<=[.!?…])\s+(?=[\"'«„“(\[]?[A-ZА-ЯІЇЄҐĄĆĘŁŃÓŚŹŻ0-9])"
)


def split_sentences(text: str) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    return [part for part in _BOUNDARY.split(normalized) if part]
```

- [ ] **Step 4: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_mt_text.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/mt/nllb_codes.py src/services/mt/sentence_split.py tests/services/test_rt_mt_text.py
git commit -m "feat(mt): add NLLB language codes and sentence splitter"
```

---

### Task 8: `Ct2NllbTranslator`

**Files:**
- Create: `src/services/mt/ct2_translator.py`
- Test: `tests/services/test_rt_ct2_translator.py`

**Interfaces:**
- Consumes: `to_nllb_code`, `UnsupportedLanguage`, `supported_languages` (Task 7); `split_sentences` (Task 7); `backend_device`, `default_compute_type`, `resolve_ct2_compute_type`, `require_gpu` (Task 4).
- Produces:
  - `MAX_SOURCE_PIECES = 400`; `UNK = "<unk>"`; `source_tokens(sp, text: str, src_code: str, vocab: set[str] | None = None) -> list[str]` (шматки поза словником → `<unk>`, як у HF-токенізатора); `chunk_tokens(pieces: list[str], limit: int = MAX_SOURCE_PIECES) -> list[list[str]]`; `load_vocabulary(model_dir: Path) -> set[str] | None`;
  - `class Ct2NllbTranslator(model_dir: Path, device: str = "cpu", compute_type: str = "int8", threads: int = 4, *, translator_factory=None, sp_factory=None)` з `translate(texts: list[str], source_lang: str, target_lang: str, num_beams: int = 4) -> list[str]`, `languages() -> list[str]`, атрибутами `device`, `compute_type`;
  - `Ct2NllbTranslator.from_settings(model_dir: Path, device: str, compute_type: str | None, threads: int) -> Ct2NllbTranslator`.

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_ct2_translator.py
import sys
import types

import pytest

from src.services.common.device import DeviceError
from src.services.mt import ct2_translator as ct
from src.services.mt.nllb_codes import UnsupportedLanguage


class FakeSP:
    def encode(self, text, out_type=str):
        return text.split()

    def decode(self, pieces):
        return " ".join(pieces)


class FakeResult:
    def __init__(self, hyp):
        self.hypotheses = [hyp]


class FakeTranslator:
    def __init__(self, path, **kw):
        self.path, self.kw, self.calls = path, kw, []

    def translate_batch(self, source, **kw):
        self.calls.append((source, kw))
        return [FakeResult([kw["target_prefix"][i][0], *[t.upper() for t in src[1:-1]]])
                for i, src in enumerate(source)]


@pytest.fixture
def model_dir(tmp_path):
    vocab = ["ukr_Cyrl", "pol_Latn", "eng_Latn", "</s>", "<unk>",
             "привіт", "світ", "Раз.", "Два.", "Три"]
    (tmp_path / "shared_vocabulary.txt").write_text("\n".join(vocab) + "\n")
    return tmp_path


@pytest.fixture
def translator(model_dir):
    return ct.Ct2NllbTranslator(model_dir, "cpu", "int8", 3,
                                translator_factory=FakeTranslator, sp_factory=lambda path: FakeSP())


def test_constructor_passes_backend_args(translator, model_dir):
    assert translator._translator.path == str(model_dir)
    assert translator._translator.kw == {"device": "cpu", "compute_type": "int8",
                                         "inter_threads": 1, "intra_threads": 3}


def test_source_tokens_and_target_prefix(translator):
    out = translator.translate(["привіт світ"], "uk", "en", num_beams=4)
    assert out == ["ПРИВІТ СВІТ"]
    source, kw = translator._translator.calls[0]
    assert source == [["ukr_Cyrl", "привіт", "світ", "</s>"]]
    assert kw["target_prefix"] == [["eng_Latn"]]
    assert kw["beam_size"] == 4
    assert kw["max_batch_size"] == 16 and kw["batch_type"] == "examples"
    assert kw["max_input_length"] == 512 and kw["max_decoding_length"] == 256
    assert kw["repetition_penalty"] == 1.1 and kw["no_repeat_ngram_size"] == 0


def test_multiple_texts_one_batch_regrouped(translator):
    out = translator.translate(["Раз. Два.", "Три"], "uk", "en")
    assert out == ["РАЗ. ДВА.", "ТРИ"]
    assert len(translator._translator.calls) == 1
    assert len(translator._translator.calls[0][0]) == 3


def test_empty_texts_skip_model(translator):
    assert translator.translate(["", "   "], "uk", "en") == ["", ""]
    assert translator._translator.calls == []


def test_unknown_iso_language(translator):
    with pytest.raises(UnsupportedLanguage):
        translator.translate(["x"], "xx", "en")


def test_flores_code_not_in_vocab(translator):
    with pytest.raises(UnsupportedLanguage, match="deu_Latn"):
        translator.translate(["x"], "deu_Latn", "en")


def test_vocabulary_json(tmp_path):
    (tmp_path / "shared_vocabulary.json").write_text('["eng_Latn", "ukr_Cyrl"]')
    assert ct.load_vocabulary(tmp_path) == {"eng_Latn", "ukr_Cyrl"}
    assert ct.load_vocabulary(tmp_path / "nope") is None


def test_chunk_tokens_prefers_commas():
    pieces = [f"w{i}" for i in range(900)]
    pieces[349] = "w349,"
    chunks = ct.chunk_tokens(pieces, limit=400)
    assert [len(c) for c in chunks] == [350, 400, 150]
    assert chunks[0][-1] == "w349,"
    assert sum(chunks, []) == pieces


def test_chunk_tokens_short():
    assert ct.chunk_tokens(["a", "b"], limit=400) == [["a", "b"]]


def test_source_tokens():
    assert ct.source_tokens(FakeSP(), "a b", "pol_Latn") == ["pol_Latn", "a", "b", "</s>"]


def test_source_tokens_maps_oov_to_unk():
    vocab = {"a", "pol_Latn", "</s>"}
    assert ct.source_tokens(FakeSP(), "a —", "pol_Latn", vocab) == ["pol_Latn", "a", "<unk>", "</s>"]


def test_translate_maps_oov_to_unk(translator):
    translator.translate(["привіт —"], "uk", "en")
    source, _ = translator._translator.calls[0]
    assert source == [["ukr_Cyrl", "привіт", "<unk>", "</s>"]]


def _fake_ct2(monkeypatch, gpus, supported):
    fake = types.SimpleNamespace(
        get_cuda_device_count=lambda: gpus,
        get_supported_compute_types=lambda device: set(supported),
        Translator=FakeTranslator,
    )
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    monkeypatch.setitem(sys.modules, "sentencepiece",
                        types.SimpleNamespace(SentencePieceProcessor=lambda model_file: FakeSP()))


def test_from_settings_falls_back_compute(monkeypatch, model_dir):
    _fake_ct2(monkeypatch, gpus=1, supported={"int8", "float32"})
    tr = ct.Ct2NllbTranslator.from_settings(model_dir, "cuda", None, 2)
    assert tr.compute_type == "int8"
    assert tr._translator.kw["device"] == "cuda"


def test_from_settings_requires_gpu(monkeypatch, model_dir):
    _fake_ct2(monkeypatch, gpus=0, supported={"int8"})
    with pytest.raises(DeviceError):
        ct.Ct2NllbTranslator.from_settings(model_dir, "cuda", None, 2)
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_ct2_translator.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати**

```python
# src/services/mt/ct2_translator.py
"""NLLB-200 on CTranslate2 without transformers/torch (spec §4.2)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from ..common.device import (backend_device, default_compute_type, require_gpu,
                             resolve_ct2_compute_type)
from .nllb_codes import UnsupportedLanguage, supported_languages, to_nllb_code
from .sentence_split import split_sentences

MAX_SOURCE_PIECES = 400
EOS = "</s>"
UNK = "<unk>"


def _pieces(sp, text: str, vocab: set[str] | None) -> list[str]:
    pieces = sp.encode(text, out_type=str)
    if vocab is None:
        return pieces
    # sentencepiece can emit pieces missing from the NLLB vocabulary (e.g. "—");
    # the HF tokenizer maps them to <unk>, so do the same explicitly.
    return [p if p in vocab else UNK for p in pieces]


def source_tokens(sp, text: str, src_code: str, vocab: set[str] | None = None) -> list[str]:
    """NLLB (non-legacy) source layout: [src_lang] + pieces + [</s>]."""
    return [src_code, *_pieces(sp, text, vocab), EOS]


def chunk_tokens(pieces: list[str], limit: int = MAX_SOURCE_PIECES) -> list[list[str]]:
    chunks: list[list[str]] = []
    start = 0
    while len(pieces) - start > limit:
        end = start + limit
        cut = next((i + 1 for i in range(end - 1, start, -1) if pieces[i].endswith(",")), end)
        chunks.append(pieces[start:cut])
        start = cut
    if start < len(pieces):
        chunks.append(pieces[start:])
    return chunks


def load_vocabulary(model_dir: Path) -> set[str] | None:
    model_dir = Path(model_dir)
    json_path = model_dir / "shared_vocabulary.json"
    txt_path = model_dir / "shared_vocabulary.txt"
    if json_path.is_file():
        return set(json.loads(json_path.read_text(encoding="utf-8")))
    if txt_path.is_file():
        return {line.rstrip("\n") for line in txt_path.read_text(encoding="utf-8").splitlines()}
    return None


class Ct2NllbTranslator:
    def __init__(self, model_dir: Path, device: str = "cpu", compute_type: str = "int8",
                 threads: int = 4, *, translator_factory: Callable | None = None,
                 sp_factory: Callable | None = None):
        self.model_dir = Path(model_dir)
        self.device = device
        self.compute_type = compute_type
        if translator_factory is None:
            import ctranslate2

            translator_factory = ctranslate2.Translator
        if sp_factory is None:
            import sentencepiece as spm

            def sp_factory(path: str):
                return spm.SentencePieceProcessor(model_file=path)

        self._translator = translator_factory(
            str(self.model_dir), device=backend_device(device), compute_type=compute_type,
            inter_threads=1, intra_threads=threads)
        self._sp = sp_factory(str(self.model_dir / "sentencepiece.bpe.model"))
        self._vocab = load_vocabulary(self.model_dir)

    @classmethod
    def from_settings(cls, model_dir: Path, device: str, compute_type: str | None,
                      threads: int) -> "Ct2NllbTranslator":
        import ctranslate2

        require_gpu(device, ctranslate2.get_cuda_device_count() if device != "cpu" else 0)
        requested = compute_type or default_compute_type("mt", device)
        supported = set(ctranslate2.get_supported_compute_types(backend_device(device)))
        resolved = resolve_ct2_compute_type(requested, supported)
        return cls(model_dir, device, resolved, threads, translator_factory=ctranslate2.Translator)

    def languages(self) -> list[str]:
        return supported_languages()

    def _lang_token(self, lang: str) -> str:
        code = to_nllb_code(lang)
        if self._vocab is not None and code not in self._vocab:
            raise UnsupportedLanguage(f"{code} is not in the model vocabulary")
        return code

    def translate(self, texts: list[str], source_lang: str, target_lang: str,
                  num_beams: int = 4) -> list[str]:
        src = self._lang_token(source_lang)
        tgt = self._lang_token(target_lang)
        batch: list[list[str]] = []
        owners: list[int] = []
        for index, text in enumerate(texts):
            for sentence in split_sentences(text):
                pieces = _pieces(self._sp, sentence, self._vocab)
                for chunk in chunk_tokens(pieces):
                    batch.append([src, *chunk, EOS])
                    owners.append(index)
        parts: list[list[str]] = [[] for _ in texts]
        if batch:
            results = self._translator.translate_batch(
                batch,
                target_prefix=[[tgt]] * len(batch),
                beam_size=num_beams,
                max_batch_size=16,
                batch_type="examples",
                max_input_length=512,
                max_decoding_length=256,
                repetition_penalty=1.1,
                no_repeat_ngram_size=0,
                return_scores=False,
            )
            for owner, result in zip(owners, results):
                hypothesis = list(result.hypotheses[0])
                if hypothesis and hypothesis[0] == tgt:
                    hypothesis = hypothesis[1:]
                parts[owner].append(self._sp.decode(hypothesis).strip())
        return [" ".join(p for p in chunks if p) for chunks in parts]
```

- [ ] **Step 4: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_ct2_translator.py -q`
Expected: PASS

- [ ] **Step 5: Перевірити, що рантайм MT не тягне transformers/torch**

Run: `nix develop .#bench-cpu -c python -c "import sys, src.services.mt.ct2_translator; assert 'transformers' not in sys.modules and 'torch' not in sys.modules; print('clean')"`
Expected: `clean`

- [ ] **Step 6: Commit**

```bash
git add src/services/mt/ct2_translator.py tests/services/test_rt_ct2_translator.py
git commit -m "feat(mt): add CTranslate2 NLLB translator with sentencepiece tokenization"
```

---

### Task 9: Offline model tools (pin, conversion, NLLB golden)

**Files:**
- Create: `scripts/model_tools/pin_model.py`, `scripts/model_tools/convert_nllb_ct2.py`, `scripts/model_tools/make_nllb_golden.py`
- Create (generated): `tests/fixtures/nllb_golden_tokens.json`
- Test: `tests/services/test_rt_model_tools.py`, `tests/services/test_rt_nllb_golden.py`

**Interfaces:**
- Consumes: `sha256_file`, `MANIFEST` (Task 5); `read_token_file` (Task 3); `source_tokens` (Task 8).
- Produces: `pin_model.format_filespecs(entries: list[tuple[str, int, str]]) -> str`; `convert_nllb_ct2.write_manifest(out_dir: Path, source: dict) -> dict`; CLI-скрипти (`python -m scripts.model_tools.<name>`); фікстура `[{lang, text, tokens}]`.

- [ ] **Step 1: Написати тест для чистих функцій**

```python
# tests/services/test_rt_model_tools.py
import hashlib
import json

from scripts.model_tools.convert_nllb_ct2 import write_manifest
from scripts.model_tools.pin_model import format_filespecs
from src.services.common.model_store import MANIFEST


def test_format_filespecs_sorted():
    text = format_filespecs([("b.bin", 2, "b" * 64), ("a.json", 1, "a" * 64)])
    assert text.splitlines() == [
        f'            FileSpec("a.json", 1, "{"a" * 64}"),',
        f'            FileSpec("b.bin", 2, "{"b" * 64}"),',
    ]


def test_write_manifest(tmp_path):
    (tmp_path / "model.bin").write_bytes(b"abc")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.txt").write_bytes(b"x")
    manifest = write_manifest(tmp_path, {"repo_id": "facebook/nllb-200-distilled-600M"})
    on_disk = json.loads((tmp_path / MANIFEST).read_text())
    assert on_disk == manifest
    assert manifest["files"]["model.bin"] == {"size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
    assert "sub/x.txt" in manifest["files"]
    assert MANIFEST not in manifest["files"]
    assert manifest["repo_id"] == "facebook/nllb-200-distilled-600M"
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_model_tools.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати `pin_model.py`**

```python
# scripts/model_tools/pin_model.py
"""Print FileSpec lines for src/services/common/model_registry.py.

Usage (any bench shell):
  python -m scripts.model_tools.pin_model REPO_ID REVISION 'pattern' ['pattern' ...] [--hf-token-file F]
LFS files use the sha256 from the Hub; small non-LFS files are downloaded and hashed.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
from pathlib import Path

from src.services.common.hf_env import read_token_file


def format_filespecs(entries: list[tuple[str, int, str]]) -> str:
    return "\n".join(f'            FileSpec("{path}", {size}, "{sha}"),'
                     for path, size, sha in sorted(entries))


def collect(repo_id: str, revision: str, patterns: list[str],
            token: str | None) -> list[tuple[str, int, str]]:
    from huggingface_hub import HfApi, hf_hub_download

    entries = []
    for item in HfApi(token=token).list_repo_tree(repo_id, revision=revision,
                                                  recursive=True, expand=True):
        path = getattr(item, "path", "")
        size = getattr(item, "size", None)
        if size is None or not any(fnmatch.fnmatch(path, p) for p in patterns):
            continue
        lfs = getattr(item, "lfs", None)
        if lfs is not None:
            sha = lfs.sha256
        else:
            local = hf_hub_download(repo_id, path, revision=revision, token=token)
            sha = hashlib.sha256(Path(local).read_bytes()).hexdigest()
        entries.append((path, size, sha))
    return entries


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repo_id")
    p.add_argument("revision")
    p.add_argument("patterns", nargs="+")
    p.add_argument("--hf-token-file")
    args = p.parse_args(argv)
    print(format_filespecs(collect(args.repo_id, args.revision, args.patterns,
                                   read_token_file(args.hf_token_file))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Реалізувати `convert_nllb_ct2.py`**

```python
# scripts/model_tools/convert_nllb_ct2.py
"""Convert pinned facebook/nllb-200-distilled-600M to CTranslate2 and write .rt-manifest.json.

Run ONLY in the model-tools shell (needs torch CPU + transformers; downloads ~2.5 GB):
  nix develop .#model-tools -c python -m scripts.model_tools.convert_nllb_ct2 --out DIR
The output dir can be used as RT_MODEL=<absolute DIR> (spec §3.1 gate).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.services.common.hf_env import read_token_file
from src.services.common.model_store import MANIFEST, sha256_file

MODEL = "facebook/nllb-200-distilled-600M"
REVISION = "f8d333a098d19b4fd9a8b18f94170487ad3f821d"
COPY_FILES = ["sentencepiece.bpe.model", "tokenizer.json", "tokenizer_config.json",
              "special_tokens_map.json"]


def write_manifest(out_dir: Path, source: dict) -> dict:
    out_dir = Path(out_dir)
    files = {}
    for path in sorted(p for p in out_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(out_dir).as_posix()
        if rel == MANIFEST:
            continue
        files[rel] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
    manifest = {**source, "files": files, "verified_at": int(time.time())}
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default=MODEL)
    p.add_argument("--revision", default=REVISION)
    p.add_argument("--out", required=True)
    p.add_argument("--quantization", default="int8")
    p.add_argument("--hf-token-file")
    args = p.parse_args(argv)

    import ctranslate2
    from ctranslate2.converters import TransformersConverter
    from huggingface_hub import snapshot_download

    local = snapshot_download(args.model, revision=args.revision,
                              token=read_token_file(args.hf_token_file))
    TransformersConverter(local, copy_files=COPY_FILES).convert(
        args.out, quantization=args.quantization, force=True)
    manifest = write_manifest(Path(args.out), {
        "alias": f"local:{Path(args.out).name}",
        "repo_id": args.model,
        "revision": args.revision,
        "quantization": args.quantization,
        "converter": f"ctranslate2 {ctranslate2.__version__}",
        "license": "CC-BY-NC-4.0",
    })
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Реалізувати `make_nllb_golden.py`**

```python
# scripts/model_tools/make_nllb_golden.py
"""Generate tests/fixtures/nllb_golden_tokens.json with the HF NLLB tokenizer (model-tools shell).

Downloads only tokenizer files (~22 MB) of the pinned facebook revision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.model_tools.convert_nllb_ct2 import MODEL, REVISION

SENTENCES = {
    "ukr_Cyrl": ["Привіт, як справи?", "Мʼясо коштує 120 гривень.",
                 "Це т. зв. «тестовий» приклад — з апострофом: п'ять."],
    "pol_Latn": ["Dzień dobry, jak się masz?", "Łódź jest dużym miastem w Polsce.",
                 "Cena wynosi 3,50 zł."],
    "eng_Latn": ["Hello world!", "It's 5 p.m. — let's go."],
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="tests/fixtures/nllb_golden_tokens.json")
    args = p.parse_args(argv)

    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer

    local = snapshot_download(MODEL, revision=REVISION, allow_patterns=[
        "sentencepiece.bpe.model", "tokenizer.json", "tokenizer_config.json",
        "special_tokens_map.json"])
    records = []
    for code, sentences in SENTENCES.items():
        tokenizer = AutoTokenizer.from_pretrained(local, src_lang=code)
        for text in sentences:
            records.append({"lang": code, "text": text,
                            "tokens": tokenizer.convert_ids_to_tokens(tokenizer.encode(text))})
    Path(args.out).write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
    print(f"wrote {len(records)} records to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Запустити тести чистих функцій**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_model_tools.py -q`
Expected: PASS

- [ ] **Step 7: Перевірити, що pin_model відтворює реєстр (мережа, лише метадані + малі файли)**

Run: `nix develop .#bench-cpu -c python -m scripts.model_tools.pin_model Systran/faster-whisper-small 536b0662742c02347bc0e980a01041f333bce120 model.bin config.json tokenizer.json vocabulary.txt`
Expected: 4 рядки `FileSpec(...)`, ідентичні блоку `whisper-small` у `src/services/common/model_registry.py`.

- [ ] **Step 8: Згенерувати golden-фікстуру**

Run: `nix develop .#model-tools -c python -m scripts.model_tools.make_nllb_golden`
Expected: `wrote 8 records to tests/fixtures/nllb_golden_tokens.json`; перший запис для `ukr_Cyrl` починається з `"ukr_Cyrl"` і закінчується `"</s>"`.
Якщо `transformers` 5.x змінив порядок (legacy: `[..., "</s>", "ukr_Cyrl"]`) — **зупинитись**: це означає, що `source_tokens` треба узгодити з фактичним порядком HF; зафіксувати знахідку в spec §4.2 і виправити `source_tokens` + тест `test_source_tokens`.

- [ ] **Step 9: Написати golden-тест**

```python
# tests/services/test_rt_nllb_golden.py
"""Ct2NllbTranslator tokenization must equal the HF NLLB tokenizer (spec §4.2)."""
import json
import os
from pathlib import Path

import pytest

from src.services.mt.ct2_translator import load_vocabulary, source_tokens

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "nllb_golden_tokens.json"


def _spm_path() -> Path:
    root = Path(os.environ.get("RT_MODEL_DIR") or Path.home() / ".cache" / "rt-speech" / "models")
    return root / "rt" / "nllb-200-distilled-600M-ct2-int8" / "sentencepiece.bpe.model"


@pytest.mark.skipif(not FIXTURE.exists() or not _spm_path().exists(),
                    reason="golden fixture or provisioned NLLB sentencepiece model missing")
def test_tokenization_matches_hf():
    import sentencepiece as spm

    sp = spm.SentencePieceProcessor(model_file=str(_spm_path()))
    vocab = load_vocabulary(_spm_path().parent)
    assert vocab is not None
    for record in json.loads(FIXTURE.read_text(encoding="utf-8")):
        assert source_tokens(sp, record["text"], record["lang"], vocab) == record["tokens"], \
            record["text"]
```

- [ ] **Step 10: Запустити golden-тест (після Task 18 він виконується, до того — skip)**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_nllb_golden.py -q -rs`
Expected: `1 skipped` (модель ще не провізіонована) — це нормально на цьому етапі.

- [ ] **Step 11: Commit**

```bash
git add scripts/model_tools tests/services/test_rt_model_tools.py tests/services/test_rt_nllb_golden.py tests/fixtures/nllb_golden_tokens.json
git commit -m "feat(models): add offline pin/convert tools and NLLB golden tokenization fixture"
```

---

## Phase C — ASR і TTS рушії

### Task 10: `WhisperEngine` (faster-whisper)

**Files:**
- Create: `src/services/asr/whisper_engine.py`
- Test: `tests/services/test_rt_whisper_engine.py`

**Interfaces:**
- Consumes: `backend_device`, `default_compute_type`, `resolve_ct2_compute_type`, `require_gpu` (Task 4).
- Produces: `SAMPLE_RATE = 16000`; `VadProfile(threshold, neg_threshold, min_speech_duration_ms, min_silence_duration_ms, speech_pad_ms, max_speech_duration_s)` з `as_dict() -> dict`; `STREAMING_VAD`, `FILE_VAD`; `FINAL_TEMPERATURES = (0.0, 0.2, 0.4)`; `Segment(start, end, text)`; `TranscriptionResult(text, language, segments, duration)`; `to_float32_mono(audio) -> np.ndarray`; `WhisperEngine(model_dir, device="cpu", compute_type="int8", threads=4, *, model_factory=None)` з `transcribe(audio, *, language=None, beam_size=1, vad=FILE_VAD, initial_prompt=None, partial=False) -> TranscriptionResult`; `WhisperEngine.from_settings(model_dir, device, compute_type: str | None, threads) -> WhisperEngine`.

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_whisper_engine.py
import sys
import types

import numpy as np
import pytest

from src.services.asr import whisper_engine as we
from src.services.common.device import DeviceError


class FakeSeg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class FakeModel:
    def __init__(self, path, **kw):
        self.path, self.kw, self.calls = path, kw, []

    def transcribe(self, audio, **kw):
        self.calls.append((audio, kw))
        return iter([FakeSeg(0.0, 1.0, " Привіт "), FakeSeg(1.0, 2.0, "світ")]), \
            types.SimpleNamespace(language="uk")


@pytest.fixture
def engine(tmp_path):
    return we.WhisperEngine(tmp_path, "cuda", "int8_float16", 4, model_factory=FakeModel)


def test_constructor_args(engine, tmp_path):
    assert engine._model.path == str(tmp_path)
    assert engine._model.kw == {"device": "cuda", "compute_type": "int8_float16",
                                "cpu_threads": 4, "num_workers": 1}


def test_final_transcribe_kwargs(engine):
    result = engine.transcribe(np.zeros(32000, dtype=np.float32), language="uk", beam_size=3)
    _, kw = engine._model.calls[0]
    assert kw["language"] == "uk" and kw["beam_size"] == 3
    assert kw["temperature"] == [0.0, 0.2, 0.4]
    assert kw["condition_on_previous_text"] is False
    assert kw["no_speech_threshold"] == 0.6
    assert kw["compression_ratio_threshold"] == 2.4
    assert kw["log_prob_threshold"] == -1.0
    assert kw["without_timestamps"] is False
    assert kw["vad_filter"] is True
    assert kw["vad_parameters"] == we.FILE_VAD.as_dict()
    assert "initial_prompt" not in kw
    assert result.text == "Привіт світ"
    assert result.language == "uk"
    assert result.duration == 2.0
    assert [s.text for s in result.segments] == ["Привіт", "світ"]


def test_partial_transcribe_kwargs(engine):
    engine.transcribe(np.zeros(16000, dtype=np.float32), language="pl", beam_size=5,
                      vad=we.STREAMING_VAD, initial_prompt="kontekst", partial=True)
    _, kw = engine._model.calls[0]
    assert kw["beam_size"] == 1 and kw["temperature"] == 0.0 and kw["without_timestamps"] is True
    assert kw["vad_parameters"] == we.STREAMING_VAD.as_dict()
    assert kw["initial_prompt"] == "kontekst"


def test_no_vad(engine):
    engine.transcribe(np.zeros(16000, dtype=np.float32), language="uk", vad=None)
    _, kw = engine._model.calls[0]
    assert kw["vad_filter"] is False and "vad_parameters" not in kw


def test_empty_audio_skips_model(engine):
    result = engine.transcribe(np.zeros(0, dtype=np.float32), language="uk")
    assert result.text == "" and engine._model.calls == []


def test_int16_audio_is_scaled(engine):
    engine.transcribe(np.full(1600, 16384, dtype=np.int16), language="uk")
    audio, _ = engine._model.calls[0]
    assert audio.dtype == np.float32
    assert np.allclose(audio, 0.5)


def test_stereo_rejected(engine):
    with pytest.raises(ValueError, match="mono"):
        engine.transcribe(np.zeros((1600, 2), dtype=np.float32), language="uk")


def test_vad_profiles():
    assert we.STREAMING_VAD.as_dict() == {
        "threshold": 0.5, "neg_threshold": 0.35, "min_speech_duration_ms": 250,
        "min_silence_duration_ms": 400, "speech_pad_ms": 200, "max_speech_duration_s": 12.0}
    assert we.FILE_VAD.as_dict() == {
        "threshold": 0.5, "min_speech_duration_ms": 0, "min_silence_duration_ms": 500,
        "speech_pad_ms": 300, "max_speech_duration_s": 30.0}


def _fake_modules(monkeypatch, gpus, supported):
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(
        get_cuda_device_count=lambda: gpus,
        get_supported_compute_types=lambda device: set(supported)))
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))


def test_from_settings_default_compute(monkeypatch, tmp_path):
    _fake_modules(monkeypatch, gpus=0, supported={"int8", "int8_float32", "float32"})
    engine = we.WhisperEngine.from_settings(tmp_path, "cpu", None, 2)
    assert engine.compute_type == "int8"
    assert engine._model.kw["device"] == "cpu"


def test_from_settings_requires_gpu(monkeypatch, tmp_path):
    _fake_modules(monkeypatch, gpus=0, supported={"int8"})
    with pytest.raises(DeviceError):
        we.WhisperEngine.from_settings(tmp_path, "cuda", None, 2)
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_whisper_engine.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати**

```python
# src/services/asr/whisper_engine.py
"""faster-whisper engine with spec §4.3 decoding and VAD profiles."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from ..common.device import (backend_device, default_compute_type, require_gpu,
                             resolve_ct2_compute_type)

SAMPLE_RATE = 16000
FINAL_TEMPERATURES = (0.0, 0.2, 0.4)


@dataclass(frozen=True)
class VadProfile:
    threshold: float
    neg_threshold: float | None
    min_speech_duration_ms: int
    min_silence_duration_ms: int
    speech_pad_ms: int
    max_speech_duration_s: float

    def as_dict(self) -> dict:
        data = asdict(self)
        if data["neg_threshold"] is None:
            del data["neg_threshold"]
        return data


# Streaming endpointing: min_silence_duration_ms is the finalization delay.
STREAMING_VAD = VadProfile(0.5, 0.35, 250, 400, 200, 12.0)
# File transcription (vad_filter=True).
FILE_VAD = VadProfile(0.5, None, 0, 500, 300, 30.0)


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    segments: list[Segment]
    duration: float


def to_float32_mono(audio) -> np.ndarray:
    arr = np.asarray(audio)
    if arr.ndim != 1:
        raise ValueError("audio must be mono 1-D PCM at 16 kHz")
    if arr.dtype == np.int16:
        return arr.astype(np.float32) / 32768.0
    return arr.astype(np.float32, copy=False)


class WhisperEngine:
    def __init__(self, model_dir: Path, device: str = "cpu", compute_type: str = "int8",
                 threads: int = 4, *, model_factory: Callable | None = None):
        if model_factory is None:
            from faster_whisper import WhisperModel

            model_factory = WhisperModel
        self.device = device
        self.compute_type = compute_type
        self._model = model_factory(str(model_dir), device=backend_device(device),
                                    compute_type=compute_type, cpu_threads=threads, num_workers=1)

    @classmethod
    def from_settings(cls, model_dir: Path, device: str, compute_type: str | None,
                      threads: int) -> "WhisperEngine":
        import ctranslate2

        require_gpu(device, ctranslate2.get_cuda_device_count() if device != "cpu" else 0)
        requested = compute_type or default_compute_type("asr", device)
        supported = set(ctranslate2.get_supported_compute_types(backend_device(device)))
        return cls(model_dir, device, resolve_ct2_compute_type(requested, supported), threads)

    def transcribe(self, audio, *, language: str | None = None, beam_size: int = 1,
                   vad: VadProfile | None = FILE_VAD, initial_prompt: str | None = None,
                   partial: bool = False) -> TranscriptionResult:
        samples = to_float32_mono(audio)
        duration = samples.size / SAMPLE_RATE
        if samples.size == 0:
            return TranscriptionResult("", language or "", [], 0.0)
        kwargs = {
            "language": language,
            "beam_size": 1 if partial else beam_size,
            "temperature": 0.0 if partial else list(FINAL_TEMPERATURES),
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.6,
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "without_timestamps": partial,
            "vad_filter": vad is not None,
        }
        if vad is not None:
            kwargs["vad_parameters"] = vad.as_dict()
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        segments, info = self._model.transcribe(samples, **kwargs)
        parsed = [Segment(float(s.start), float(s.end), s.text.strip()) for s in segments]
        text = " ".join(s.text for s in parsed if s.text)
        return TranscriptionResult(text, info.language, parsed, duration)
```

- [ ] **Step 4: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_whisper_engine.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/asr/whisper_engine.py tests/services/test_rt_whisper_engine.py
git commit -m "feat(asr): add faster-whisper engine with streaming/file VAD profiles"
```

---

### Task 11: Kokoro G2P і ONNX-синтезатор

**Files:**
- Create: `src/services/tts/g2p.py`, `src/services/tts/kokoro_onnx.py`
- Test: `tests/services/test_rt_kokoro_onnx.py`

**Interfaces:**
- Consumes: `DeviceError` (Task 4).
- Produces:
  - `g2p.py`: `UnknownVoice(ValueError)`; `G2PChunk(graphemes: str, phonemes: str)`; `VOICE_RE`; `lang_code_for_voice(voice: str) -> str` (`"a"|"b"`); `KokoroG2P(lang_code="a", *, pipeline_factory=None)` — callable `(text) -> Iterator[G2PChunk]`;
  - `kokoro_onnx.py`: `SAMPLE_RATE = 24000`; `MAX_PHONEMES = 510`; `ONNX_FILES`; `load_vocab(model_dir) -> dict[str, int]`; `phonemes_to_ids(phonemes, vocab) -> list[int]`; `list_voices(model_dir, suffix) -> list[str]`; `KokoroOnnxSynthesizer(model_dir, device="cpu", compute_type="float32", threads=4, *, session_factory=None, g2p_factory=None)` з `sample_rate`, `voices() -> list[str]`, `synthesize(text, voice="af_heart", speed=1.0) -> Iterator[np.ndarray]` (float32 mono @ 24 kHz). `session_factory(path: str, device: str, threads: int)`; `g2p_factory(lang_code: str) -> Callable[[str], Iterable[G2PChunk]]`.

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_kokoro_onnx.py
import json
import types

import numpy as np
import pytest

from src.services.common.device import DeviceError
from src.services.tts import kokoro_onnx as ko
from src.services.tts.g2p import G2PChunk, KokoroG2P, UnknownVoice, lang_code_for_voice

VOCAB = {"$": 0, "h": 1, "ə": 2, "l": 3, "O": 4, " ": 5}


@pytest.fixture
def model_dir(tmp_path):
    (tmp_path / "tokenizer.json").write_text(json.dumps({"model": {"vocab": VOCAB}}))
    voices = tmp_path / "voices"
    voices.mkdir()
    np.arange(510 * 256, dtype=np.float32).tofile(voices / "af_heart.bin")
    np.zeros(510 * 256, dtype=np.float32).tofile(voices / "bf_emma.bin")
    return tmp_path


class FakeSession:
    def __init__(self, path, device, threads):
        self.path, self.device, self.threads, self.feeds = path, device, threads, []

    def run(self, outputs, feeds):
        self.feeds.append(feeds)
        return [np.ones((1, 2400), dtype=np.float32)]


class FakeG2P:
    created = []

    def __init__(self, lang_code):
        FakeG2P.created.append(lang_code)

    def __call__(self, text):
        for word in text.split("|"):
            yield G2PChunk(word, word)


def make(model_dir, device="cpu", compute_type="float32"):
    FakeG2P.created = []
    return ko.KokoroOnnxSynthesizer(model_dir, device, compute_type, 2,
                                    session_factory=FakeSession, g2p_factory=FakeG2P)


def test_session_gets_file_device_threads(model_dir):
    synth = make(model_dir)
    assert synth._session.path == str(model_dir / "onnx/model.onnx")
    assert (synth._session.device, synth._session.threads) == ("cpu", 2)
    assert make(model_dir, "cpu", "int8")._session.path.endswith("onnx/model_quantized.onnx")
    assert make(model_dir, "cuda", "float16")._session.path.endswith("onnx/model_fp16.onnx")


@pytest.mark.parametrize("device,compute", [("cpu", "float16"), ("cuda", "int8"), ("cpu", "bf16")])
def test_invalid_device_compute(model_dir, device, compute):
    with pytest.raises(ValueError):
        make(model_dir, device, compute)


def test_rocm_rejected(model_dir):
    with pytest.raises(DeviceError):
        make(model_dir, "rocm", "float32")


def test_feeds_and_style_index(model_dir):
    synth = make(model_dir)
    chunks = list(synth.synthesize("həl?O", voice="af_heart", speed=1.2))
    assert len(chunks) == 1 and chunks[0].shape == (2400,) and chunks[0].dtype == np.float32
    feeds = synth._session.feeds[0]
    assert feeds["input_ids"].dtype == np.int64
    assert feeds["input_ids"].tolist() == [[0, 1, 2, 3, 4, 0]]  # "?" not in vocab -> dropped
    pack = np.arange(510 * 256, dtype=np.float32).reshape(-1, 1, 256)
    assert np.array_equal(feeds["style"], pack[3])  # len(ids) - 1
    assert feeds["speed"].tolist() == pytest.approx([1.2])


def test_chunks_stream_per_g2p_chunk(model_dir):
    synth = make(model_dir)
    assert len(list(synth.synthesize("hə|lO", voice="af_heart"))) == 2


def test_ids_truncated(model_dir):
    synth = make(model_dir)
    list(synth.synthesize("h" * 600, voice="af_heart"))
    assert len(synth._session.feeds[0]["input_ids"][0]) == ko.MAX_PHONEMES + 2


def test_british_voice_uses_lang_b(model_dir):
    synth = make(model_dir)
    list(synth.synthesize("hə", voice="bf_emma"))
    assert FakeG2P.created == ["b"]


def test_empty_text_yields_nothing(model_dir):
    synth = make(model_dir)
    assert list(synth.synthesize("   ", voice="af_heart")) == []
    assert synth._session.feeds == []


def test_unknown_voice(model_dir):
    with pytest.raises(UnknownVoice):
        list(make(model_dir).synthesize("hə", voice="am_adam"))


def test_voice_path_traversal_rejected(model_dir):
    with pytest.raises(UnknownVoice):
        list(make(model_dir).synthesize("hə", voice="../../etc/passwd"))


def test_voices_listed(model_dir):
    assert make(model_dir).voices() == ["af_heart", "bf_emma"]


def test_lang_code_for_voice():
    assert lang_code_for_voice("af_heart") == "a"
    assert lang_code_for_voice("bm_george") == "b"
    for bad in ["jf_alpha", "af", "../x", "AF_heart"]:
        with pytest.raises(UnknownVoice):
            lang_code_for_voice(bad)


def test_kokoro_g2p_skips_empty_phonemes():
    results = [types.SimpleNamespace(graphemes="Hi", phonemes="hˈI"),
               types.SimpleNamespace(graphemes="", phonemes="")]
    g2p = KokoroG2P("a", pipeline_factory=lambda lc: (lambda text: iter(results)))
    assert list(g2p("Hi")) == [G2PChunk("Hi", "hˈI")]
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_kokoro_onnx.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати `g2p.py`**

```python
# src/services/tts/g2p.py
"""Kokoro G2P via kokoro.KPipeline(model=False) — misaki phonemes, no audio (spec §4.4)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator

REPO_ID = "hexgrad/Kokoro-82M"
VOICE_RE = re.compile(r"^([ab])[fm]_[a-z0-9]+$")


class UnknownVoice(ValueError):
    """Voice is not available (only English a*/b* voices are supported)."""


@dataclass(frozen=True)
class G2PChunk:
    graphemes: str
    phonemes: str


def lang_code_for_voice(voice: str) -> str:
    match = VOICE_RE.match(voice)
    if not match:
        raise UnknownVoice(f"unsupported voice {voice!r}; expected English voices like af_heart")
    return match.group(1)


class KokoroG2P:
    def __init__(self, lang_code: str = "a", *, pipeline_factory: Callable | None = None):
        if lang_code not in ("a", "b"):
            raise ValueError(f"lang_code must be 'a' or 'b', got {lang_code!r}")
        if pipeline_factory is None:
            def pipeline_factory(code: str):
                from kokoro import KPipeline

                return KPipeline(lang_code=code, repo_id=REPO_ID, model=False)

        self.lang_code = lang_code
        self._pipeline = pipeline_factory(lang_code)

    def __call__(self, text: str) -> Iterator[G2PChunk]:
        for result in self._pipeline(text):
            if result.phonemes:
                yield G2PChunk(result.graphemes, result.phonemes)
```

- [ ] **Step 4: Реалізувати `kokoro_onnx.py`**

```python
# src/services/tts/kokoro_onnx.py
"""Kokoro-82M on onnxruntime (onnx-community/Kokoro-82M-v1.0-ONNX), spec §4.4."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from ..common.device import DeviceError
from .g2p import KokoroG2P, UnknownVoice, lang_code_for_voice

SAMPLE_RATE = 24000
MAX_PHONEMES = 510
ONNX_FILES = {
    "float32": "onnx/model.onnx",
    "float16": "onnx/model_fp16.onnx",
    "int8": "onnx/model_quantized.onnx",
}
_ALLOWED = {("cpu", "float32"), ("cpu", "int8"), ("cuda", "float32"), ("cuda", "float16")}


def load_vocab(model_dir: Path) -> dict[str, int]:
    data = json.loads((Path(model_dir) / "tokenizer.json").read_text(encoding="utf-8"))
    return dict(data["model"]["vocab"])


def phonemes_to_ids(phonemes: str, vocab: dict[str, int]) -> list[int]:
    return [vocab[ch] for ch in phonemes if ch in vocab]


def list_voices(model_dir: Path, suffix: str) -> list[str]:
    voices_dir = Path(model_dir) / "voices"
    return sorted(p.name[: -len(suffix)] for p in voices_dir.glob(f"*{suffix}") if p.is_file())


def _default_session_factory(path: str, device: str, threads: int):
    import onnxruntime as ort

    if device == "cuda":
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise DeviceError("RT_DEVICE=cuda but onnxruntime has no CUDAExecutionProvider")
        providers = [("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}),
                     "CPUExecutionProvider"]
    else:
        providers = ["CPUExecutionProvider"]
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    return ort.InferenceSession(path, sess_options=options, providers=providers)


class KokoroOnnxSynthesizer:
    sample_rate = SAMPLE_RATE

    def __init__(self, model_dir: Path, device: str = "cpu", compute_type: str = "float32",
                 threads: int = 4, *, session_factory: Callable | None = None,
                 g2p_factory: Callable | None = None):
        if device not in ("cpu", "cuda"):
            raise DeviceError(f"rt-tts supports cpu|cuda only (spec D2), got {device!r}")
        if (device, compute_type) not in _ALLOWED:
            raise ValueError(f"compute_type {compute_type!r} is not allowed on {device}")
        self.model_dir = Path(model_dir)
        self._vocab = load_vocab(self.model_dir)
        self._voice_names = set(list_voices(self.model_dir, ".bin"))
        self._packs: dict[str, np.ndarray] = {}
        self._g2p: dict[str, Callable] = {}
        self._g2p_factory = g2p_factory or KokoroG2P
        factory = session_factory or _default_session_factory
        self._session = factory(str(self.model_dir / ONNX_FILES[compute_type]), device, threads)

    def voices(self) -> list[str]:
        return sorted(self._voice_names)

    def _pack(self, voice: str) -> np.ndarray:
        if voice not in self._voice_names:
            raise UnknownVoice(f"unknown voice {voice!r}; available: {self.voices()}")
        lang_code_for_voice(voice)
        if voice not in self._packs:
            path = self.model_dir / "voices" / f"{voice}.bin"
            self._packs[voice] = np.fromfile(path, dtype=np.float32).reshape(-1, 1, 256)
        return self._packs[voice]

    def synthesize(self, text: str, voice: str = "af_heart",
                   speed: float = 1.0) -> Iterator[np.ndarray]:
        pack = self._pack(voice)
        if not text.strip():
            return
        lang = lang_code_for_voice(voice)
        if lang not in self._g2p:
            self._g2p[lang] = self._g2p_factory(lang)
        for chunk in self._g2p[lang](text):
            ids = phonemes_to_ids(chunk.phonemes, self._vocab)[:MAX_PHONEMES]
            if not ids:
                continue
            style = pack[min(len(ids) - 1, len(pack) - 1)]
            feeds = {
                "input_ids": np.array([[0, *ids, 0]], dtype=np.int64),
                "style": np.asarray(style, dtype=np.float32),
                "speed": np.array([speed], dtype=np.float32),
            }
            audio = self._session.run(None, feeds)[0]
            yield np.asarray(audio, dtype=np.float32).reshape(-1)
```

- [ ] **Step 5: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_kokoro_onnx.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/services/tts/g2p.py src/services/tts/kokoro_onnx.py tests/services/test_rt_kokoro_onnx.py
git commit -m "feat(tts): add Kokoro ONNX synthesizer with misaki G2P via KPipeline quiet mode"
```

---

### Task 12: Torch-fallback і фабрика синтезаторів

**Files:**
- Create: `src/services/tts/kokoro_torch.py`, `src/services/tts/engines.py`
- Test: `tests/services/test_rt_tts_engines.py`

**Interfaces:**
- Consumes: `UnknownVoice`, `lang_code_for_voice`, `REPO_ID` (Task 11); `list_voices`, `KokoroOnnxSynthesizer` (Task 11); `DeviceError`, `default_compute_type` (Task 4).
- Produces: `KokoroTorchSynthesizer(model_dir, device="cpu", threads=4, *, model_factory=None, pipeline_factory=None)` (той самий протокол: `sample_rate`, `voices()`, `synthesize()`); `engines.DEFAULT_TTS_ENGINE = "onnx"`; `engines.create_synthesizer(engine: str, model_dir: Path, device: str, compute_type: str | None, threads: int)`.

- [ ] **Step 1: Написати тест**

```python
# tests/services/test_rt_tts_engines.py
import types

import numpy as np
import pytest

from src.services.common.device import DeviceError
from src.services.tts import engines
from src.services.tts.g2p import UnknownVoice
from src.services.tts.kokoro_torch import KokoroTorchSynthesizer


@pytest.fixture
def torch_dir(tmp_path):
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "af_heart.pt").write_bytes(b"")
    return tmp_path


class FakeTensor:
    def __init__(self, arr):
        self.arr = arr

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.arr


def make_torch(torch_dir, calls):
    def model_factory(config_path, weights_path, device):
        calls["model"] = (config_path, weights_path, device)
        return "MODEL"

    def pipeline_factory(lang_code, model):
        calls["pipeline"] = (lang_code, model)

        def run(text, voice, speed):
            calls["run"] = (text, voice, speed)
            yield types.SimpleNamespace(audio=FakeTensor(np.ones(10, dtype=np.float64)))
            yield types.SimpleNamespace(audio=None)

        return run

    return KokoroTorchSynthesizer(torch_dir, "cpu", 2, model_factory=model_factory,
                                  pipeline_factory=pipeline_factory)


def test_torch_synth_uses_local_files(torch_dir):
    calls = {}
    synth = make_torch(torch_dir, calls)
    chunks = list(synth.synthesize("Hello", voice="af_heart", speed=0.9))
    assert calls["model"] == (str(torch_dir / "config.json"), str(torch_dir / "kokoro-v1_0.pth"), "cpu")
    assert calls["pipeline"] == ("a", "MODEL")
    assert calls["run"] == ("Hello", str(torch_dir / "voices" / "af_heart.pt"), 0.9)
    assert len(chunks) == 1 and chunks[0].dtype == np.float32
    assert synth.voices() == ["af_heart"] and synth.sample_rate == 24000


def test_torch_synth_unknown_voice(torch_dir):
    with pytest.raises(UnknownVoice):
        list(make_torch(torch_dir, {}).synthesize("Hello", voice="bf_emma"))


def test_torch_synth_rejects_rocm(torch_dir):
    with pytest.raises(DeviceError):
        KokoroTorchSynthesizer(torch_dir, "rocm", 2, model_factory=lambda *a: None,
                               pipeline_factory=lambda *a: None)


def test_create_synthesizer_dispatch(monkeypatch, tmp_path):
    created = {}
    monkeypatch.setattr(engines, "KokoroOnnxSynthesizer",
                        lambda *a: created.setdefault("onnx", a))
    monkeypatch.setattr(engines, "KokoroTorchSynthesizer",
                        lambda *a: created.setdefault("torch", a))
    engines.create_synthesizer("onnx", tmp_path, "cpu", None, 2)
    engines.create_synthesizer("torch", tmp_path, "cuda", None, 2)
    assert created["onnx"] == (tmp_path, "cpu", "float32", 2)
    assert created["torch"] == (tmp_path, "cuda", 2)
    assert engines.DEFAULT_TTS_ENGINE == "onnx"


def test_create_synthesizer_rejects(tmp_path):
    with pytest.raises(ValueError, match="RT_TTS_ENGINE"):
        engines.create_synthesizer("piper", tmp_path, "cpu", None, 2)
    with pytest.raises(DeviceError):
        engines.create_synthesizer("onnx", tmp_path, "rocm", None, 2)
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_tts_engines.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати**

```python
# src/services/tts/kokoro_torch.py
"""Fallback Kokoro engine on torch (RT_TTS_ENGINE=torch), local files only (spec §4.4)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from ..common.device import DeviceError
from .g2p import REPO_ID, UnknownVoice, lang_code_for_voice
from .kokoro_onnx import SAMPLE_RATE, list_voices


class KokoroTorchSynthesizer:
    sample_rate = SAMPLE_RATE

    def __init__(self, model_dir: Path, device: str = "cpu", threads: int = 4, *,
                 model_factory: Callable | None = None, pipeline_factory: Callable | None = None):
        if device not in ("cpu", "cuda"):
            raise DeviceError(f"rt-tts supports cpu|cuda only (spec D2), got {device!r}")
        self.model_dir = Path(model_dir)
        if model_factory is None:
            def model_factory(config_path: str, weights_path: str, dev: str):
                import torch
                from kokoro import KModel

                torch.set_num_threads(threads)
                return KModel(repo_id=REPO_ID, config=config_path, model=weights_path).to(dev).eval()
        if pipeline_factory is None:
            def pipeline_factory(lang_code: str, model):
                from kokoro import KPipeline

                return KPipeline(lang_code=lang_code, repo_id=REPO_ID, model=model)
        self._model = model_factory(str(self.model_dir / "config.json"),
                                    str(self.model_dir / "kokoro-v1_0.pth"), device)
        self._pipeline_factory = pipeline_factory
        self._pipelines: dict[str, Callable] = {}
        self._voice_names = set(list_voices(self.model_dir, ".pt"))

    def voices(self) -> list[str]:
        return sorted(self._voice_names)

    def synthesize(self, text: str, voice: str = "af_heart",
                   speed: float = 1.0) -> Iterator[np.ndarray]:
        if voice not in self._voice_names:
            raise UnknownVoice(f"unknown voice {voice!r}; available: {self.voices()}")
        if not text.strip():
            return
        lang = lang_code_for_voice(voice)
        if lang not in self._pipelines:
            self._pipelines[lang] = self._pipeline_factory(lang, self._model)
        voice_path = str(self.model_dir / "voices" / f"{voice}.pt")
        for result in self._pipelines[lang](text, voice=voice_path, speed=speed):
            audio = result.audio
            if audio is None:
                continue
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            yield np.asarray(audio, dtype=np.float32).reshape(-1)
```

```python
# src/services/tts/engines.py
"""RT_TTS_ENGINE dispatch (spec §4.4)."""
from __future__ import annotations

from pathlib import Path

from ..common.device import default_compute_type
from .kokoro_onnx import KokoroOnnxSynthesizer
from .kokoro_torch import KokoroTorchSynthesizer

DEFAULT_TTS_ENGINE = "onnx"


def create_synthesizer(engine: str, model_dir: Path, device: str, compute_type: str | None,
                       threads: int):
    if engine == "onnx":
        return KokoroOnnxSynthesizer(model_dir, device,
                                     compute_type or default_compute_type("tts", device), threads)
    if engine == "torch":
        return KokoroTorchSynthesizer(model_dir, device, threads)
    raise ValueError(f"RT_TTS_ENGINE must be onnx|torch, got {engine!r}")
```

- [ ] **Step 4: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_tts_engines.py -q`
Expected: PASS

- [ ] **Step 5: Повний прогін сервісних тестів**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_*.py -q` (лише тести цього плану: `test_svc_*` підпроєкту 1 потребують FastAPI, якого немає в bench-shell — їх ганяють у `nix develop`)
Expected: PASS (golden-тест — skipped)

- [ ] **Step 6: Commit**

```bash
git add src/services/tts/kokoro_torch.py src/services/tts/engines.py tests/services/test_rt_tts_engines.py
git commit -m "feat(tts): add torch fallback engine and RT_TTS_ENGINE factory"
```

---

### Task 12a: Інтеграція рушіїв у backend-и підпроєкту 1 (MT CT2 default, TTS ORT default, реєстр моделей)

Єдине місце, де рушії цього плану підключаються до FastAPI-сервісів підпроєкту 1 (overview §7 C2: SP5 володіє ORT-рушієм і CT2-бекендом; у SP1 дублюючих задач немає). Виконувати **після** SP1 Tasks 10, 13, 15 (є `src/services/{asr,mt,tts}/{settings,backend}.py`, `faster_whisper_backend.py`, `kokoro_backend.py`) і Tasks 2–12 цього плану. HTTP-шар SP1 не змінюється.

**Files:**
- Create: `src/services/mt/ctranslate2_backend.py`
- Modify: `src/services/mt/settings.py` (`MT_BACKENDS`, defaults), `src/services/mt/backend.py` (`make_backend`)
- Modify: `src/services/asr/settings.py` (defaults), `src/services/asr/faster_whisper_backend.py` (load через реєстр + `WhisperEngine`)
- Modify: `src/services/tts/settings.py` (`RT_TTS_ENGINE`, defaults), `src/services/tts/kokoro_backend.py` (делегування `engines.create_synthesizer`)
- Test: `tests/services/test_svc_sp5_integration.py`

**Interfaces:**
- Consumes: SP1 `MtBackend`/`AsrBackend`/`TtsBackend` Protocol-и (`load()`, `translate(texts, src_nllb, tgt_nllb, num_beams)`, `transcribe(audio, DecodeOptions) -> Transcription`, `voices()`, `prepare_voice(voice)`, `synthesize(text, voice, speed) -> Iterator[np.ndarray]`, `sample_rate`), `UnsupportedVoiceLanguage`; цього плану — `prepare_model`, `ModelSettings` (Task 5), `default_model`, `default_beams`, `default_compute_type`, `default_threads` (Task 4), `Ct2NllbTranslator` (Task 8), `WhisperEngine`, `FILE_VAD` (Task 10), `lang_code_for_voice`, `UnknownVoice` (Task 11), `create_synthesizer`, `DEFAULT_TTS_ENGINE` (Task 12).
- Produces: `RT_MT_BACKEND ∈ {ctranslate2, transformers, fake}`, default `ctranslate2`; `RT_TTS_BACKEND ∈ {kokoro, fake}` (без змін) + `RT_TTS_ENGINE ∈ {onnx, torch}` (default `onnx`) всередині `KokoroBackend`; defaults `RT_MODEL`/`RT_COMPUTE_TYPE`/beams = spec §4.8 (alias-и реєстру, device-залежно).

- [ ] **Step 1: Написати тест** `tests/services/test_svc_sp5_integration.py` (фейкові рушії, без моделей):
  - `test_mt_default_backend_is_ctranslate2` — `MtSettings.from_env({})` → `backend == "ctranslate2"`, `model == "nllb-200-distilled-600M-ct2-int8"`, `compute_type == "int8"`, `num_beams == 2`; з `{"RT_DEVICE": "cuda"}` → `int8_float16`, `num_beams == 4`.
  - `test_mt_ct2_backend_passes_nllb_codes` — `Ct2MtBackend(settings, translator_cls=Fake, prepare=lambda v, s: tmp_path)`; `load()`; `translate(["Привіт"], "ukr_Cyrl", "eng_Latn", 3)` → фейк отримав саме ці NLLB-коди (pass-through `to_nllb_code`) і `num_beams=3`.
  - `test_asr_defaults_by_device` — cpu: `whisper-small`/`int8`/`beam_size == 1`; cuda: `whisper-large-v3-turbo`/`int8_float16`/`beam_size == 3`; явні `RT_ASR_BEAM_SIZE`/`RT_MODEL` перекривають.
  - `test_asr_backend_uses_engine_and_file_vad` — `FasterWhisperBackend(settings, engine_cls=Fake, prepare=…)`; `transcribe(audio, DecodeOptions(language="uk", prompt="p", beam_size=3, vad_filter=True))` → фейк отримав `vad=FILE_VAD`, `initial_prompt="p"`, `beam_size=3`; `vad_filter=False, without_timestamps=True, beam_size=1` → `partial=True`, `vad=None`; результат — SP1 `Transcription`.
  - `test_tts_engine_selection` — `TtsSettings.from_env({})` → `engine == "onnx"`, `model == "kokoro-82m-v1.0-onnx"`; `{"RT_TTS_ENGINE": "torch"}` → `engine == "torch"`, `model == "kokoro-82m-v1.0-torch"`; `{"RT_TTS_ENGINE": "x"}` → `SettingsError`.
  - `test_tts_backend_delegates_to_synthesizer` — `KokoroBackend(settings, synth_factory=Fake, prepare=…)`; `prepare_voice("jf_alpha")` → `UnsupportedVoiceLanguage` (лише `a`/`b`, spec A1); `synthesize("Hi.", "af_heart", 1.0)` повертає чанки фейку; `sample_rate == 24000`; `voices()` — перетин каталогу SP1 з `synth.voices()`.
  - `test_runtime_closure_has_no_transformers` — у підпроцесі: `make_backend(MtSettings.from_env({}))` + `load()` з фейковими фабриками → `"transformers" not in sys.modules and "torch" not in sys.modules`.

Run: `nix develop -c pytest tests/services/test_svc_sp5_integration.py -q` → FAIL (`ModuleNotFoundError: ctranslate2_backend`).

- [ ] **Step 2: MT** — `src/services/mt/ctranslate2_backend.py`:

```python
"""SP1 MtBackend over Ct2NllbTranslator (CTranslate2 + sentencepiece; no transformers/torch)."""
from __future__ import annotations

from typing import Callable, List, Optional

from ..common.device import default_threads
from ..common.model_store import ModelSettings, prepare_model
from .settings import MtSettings


class Ct2MtBackend:
    name = "ctranslate2"

    def __init__(self, settings: MtSettings, *, translator_cls=None,
                 prepare: Callable = prepare_model) -> None:
        self._settings = settings
        self.model_id = settings.model
        self._translator_cls = translator_cls
        self._prepare = prepare
        self._translator: Optional[object] = None

    def load(self) -> None:
        if self._translator_cls is None:
            from .ct2_translator import Ct2NllbTranslator
            self._translator_cls = Ct2NllbTranslator
        path = self._prepare(self._settings.model, ModelSettings.from_env())
        self._translator = self._translator_cls.from_settings(
            path, self._settings.device, self._settings.compute_type, default_threads())

    def translate(self, texts: List[str], src: str, tgt: str, num_beams: int) -> List[str]:
        # SP1 passes NLLB codes; Ct2NllbTranslator accepts them as-is (to_nllb_code pass-through).
        return self._translator.translate(texts, src, tgt, num_beams=num_beams)
```
У `settings.py`: `MT_BACKENDS = ("ctranslate2", "transformers", "fake")`; `base_from_env(..., default_backend="ctranslate2", default_model=default_model("mt", device), compute_cpu=default_compute_type("mt", "cpu"), compute_gpu=default_compute_type("mt", "cuda"))`; `num_beams` default `default_beams("mt", device)`; `repetition_penalty` default `1.1` (spec §4.2). `make_backend`: гілка `"ctranslate2"` → `Ct2MtBackend(settings)` (lazy import модуля, як інші гілки). `transformers` лишається лише для dev/порівняння (у bundle його немає — SP2 spec §5.4).

- [ ] **Step 3: ASR** — у `settings.py`: `default_model=default_model("asr", device)`, `compute_gpu=default_compute_type("asr", "cuda")`, `beam_size` default `default_beams("asr", device)`. `FasterWhisperBackend.__init__(settings, *, engine_cls=None, prepare=prepare_model)`; `load()`: `path = prepare(settings.model, ModelSettings.from_env())` → `self._engine = (engine_cls or WhisperEngine).from_settings(path, settings.device, settings.compute_type, default_threads())`; `transcribe(audio, opts)`: `partial = opts.without_timestamps and opts.beam_size == 1 and not opts.vad_filter`; `r = self._engine.transcribe(audio, language=opts.language, beam_size=opts.beam_size, vad=FILE_VAD if opts.vad_filter else None, initial_prompt=opts.prompt, partial=partial)` → `Transcription(text=r.text, language=r.language or opts.language, duration=r.duration, segments=[Segment(s.start, s.end, s.text) for s in r.segments])`. Константу `VAD_PARAMETERS` (threshold 0.35 / pad 800) видалити — замінено профілем `FILE_VAD` (spec §4.3); серверний WS-endpointing лишається SP1 `Endpointer` (його пороги — `RT_ASR_*`, sweep — Task 19).

- [ ] **Step 4: TTS** — у `settings.py`: нове поле `engine: str` з `RT_TTS_ENGINE` (`onnx|torch`, default `DEFAULT_TTS_ENGINE`, інше → `SettingsError`); `default_model` = `kokoro-82m-v1.0-onnx` для `onnx`, `kokoro-82m-v1.0-torch` для `torch`; `compute_*` = `default_compute_type("tts", …)`; `RT_DEVICE=rocm` → `SettingsError` (spec D2). `KokoroBackend.__init__(settings, *, synth_factory=create_synthesizer, prepare=prepare_model)`; `load()`: `path = prepare(settings.model, ModelSettings.from_env())`, `self._synth = synth_factory(settings.engine, path, settings.device, settings.compute_type, default_threads())`, прогрів `prepare_voice(settings.voice)`; `sample_rate = 24000`; `voices()` — `[v for v in catalog() if v.id in set(self._synth.voices())]`; `prepare_voice(voice)`: `lang_code_for_voice(voice)` → `UnknownVoice` мапиться на `UnsupportedVoiceLanguage`; `synthesize(text, voice, speed)`: `yield from self._synth.synthesize(text, voice=voice, speed=speed)`. Прямі `KModel`/`KPipeline` з `kokoro_backend.py` прибрати — torch-шлях тепер `KokoroTorchSynthesizer` (Task 12).

- [ ] **Step 5: Прогнати**

Run: `nix develop -c pytest tests/services/test_svc_*.py -q`
Expected: PASS — нові тести і всі `test_svc_*` підпроєкту 1 (зокрема `test_svc_lazy_imports`: `onnxruntime`, `ctranslate2`, `huggingface_hub` не імпортуються до `load()`; якщо SP1-тести перевіряли старі defaults `small`/`facebook/nllb-200-distilled-600M`/`hexgrad/Kokoro-82M`/beam 5 — оновити очікування на alias-и реєстру, це очікувана зміна контракту defaults, spec §4.8).

- [ ] **Step 6: Commit**

```bash
git add src/services/mt src/services/asr src/services/tts tests/services/test_svc_sp5_integration.py
git commit -m "feat(services): wire CT2 MT backend, Whisper engine and Kokoro ORT engine into the API services"
```

---

## Phase D — Benchmark harness

### Task 13: Метрики і пороги

**Files:**
- Create: `scripts/bench/metrics.py`, `scripts/bench/thresholds.py`, `scripts/bench/thresholds.yml`
- Test: `tests/bench/test_rt_metrics.py`

**Interfaces:**
- Produces:
  - `metrics.normalize_text(text) -> str`; `percentile(values, q) -> float`; `latency_summary(values) -> dict` (`n`, `p50`, `p95`, `mean`); `rtf(processing_s, audio_s) -> float`; `wer(refs, hyps) -> float` (частка 0..1); `chrf(hyps, refs) -> float` (0..100);
  - `thresholds.Check(metric, op, limit, actual, passed)`; `lookup(result, dotted) -> Any`; `evaluate(result, rules) -> list[Check]`; `gate_passed(checks) -> bool`; `load_thresholds(path=DEFAULT_THRESHOLDS) -> dict[str, dict]`; `format_checks(checks) -> str`; `DEFAULT_THRESHOLDS: Path`.

- [ ] **Step 1: Написати тест**

```python
# tests/bench/test_rt_metrics.py
import math

import pytest

from scripts.bench import metrics as m
from scripts.bench import thresholds as th


def test_normalize_text():
    assert m.normalize_text("Привіт, Світ!") == "привіт світ"
    assert m.normalize_text("М’ясо — «добре»") == "м'ясо добре"
    assert m.normalize_text("  It's   5 p.m.  ") == "it's 5 p m"
    assert m.normalize_text("'quoted'") == "quoted"


def test_percentile_and_summary():
    assert m.percentile([1, 2, 3, 4], 50) == 2.5
    assert m.percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)
    assert math.isnan(m.percentile([], 50))
    s = m.latency_summary([0.1, 0.3])
    assert s["n"] == 2 and s["p50"] == pytest.approx(0.2) and s["mean"] == pytest.approx(0.2)


def test_rtf():
    assert m.rtf(1.0, 4.0) == 0.25
    with pytest.raises(ValueError):
        m.rtf(1.0, 0.0)


def test_wer():
    assert m.wer(["Привіт, світ!"], ["привіт світ"]) == 0.0
    assert m.wer(["a b c d"], ["a b x d"]) == pytest.approx(0.25)
    assert m.wer(["a b", ""], ["a b", "noise"]) == 0.0  # empty reference ignored
    assert math.isnan(m.wer([""], ["x"]))


def test_chrf():
    assert m.chrf(["The cat sat."], ["The cat sat."]) == pytest.approx(100.0)
    assert m.chrf(["dog"], ["The cat sat."]) < 30


RESULT = {"metrics": {"rtf": 0.08, "wer": {"uk": 0.13}, "chrf": {"uk-en": 60.0}},
          "resources": {"vram_peak_mib": None}}


def test_evaluate_pass_fail_missing():
    checks = th.evaluate(RESULT, {
        "metrics.rtf": {"max": 0.10},
        "metrics.wer.uk": {"max": 0.12},
        "metrics.chrf.uk-en": {"min": 52},
        "resources.vram_peak_mib": {"max": 1638},
        "metrics.nope": {"max": 1},
    })
    by_metric = {c.metric: c.passed for c in checks}
    assert by_metric == {"metrics.rtf": True, "metrics.wer.uk": False, "metrics.chrf.uk-en": True,
                         "resources.vram_peak_mib": False, "metrics.nope": False}
    assert th.gate_passed(checks) is False
    assert th.gate_passed([]) is False
    assert "FAIL" in th.format_checks(checks)


def test_thresholds_file_has_all_gates():
    gates = th.load_thresholds()
    assert set(gates) == {"asr-cuda", "asr-cpu", "mt-cuda", "mt-cpu", "tts-cpu", "tts-cuda",
                          "system-cyborg"}
    assert gates["system-cyborg"]["resources.device_used_peak_mib"] == {"max": 7577}
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/bench/test_rt_metrics.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати `metrics.py`**

```python
# scripts/bench/metrics.py
"""Benchmark metrics: RTF, latency percentiles, WER (jiwer), chrF (sacrebleu)."""
from __future__ import annotations

import math
import re

_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "‘": "'"})
_NON_WORD = re.compile(r"[^\w\s']", re.UNICODE)
_STRAY_QUOTE = re.compile(r"(?<!\w)'|'(?!\w)")


def normalize_text(text: str) -> str:
    lowered = text.translate(_APOSTROPHES).lower()
    cleaned = _NON_WORD.sub(" ", lowered).replace("_", " ")
    cleaned = _STRAY_QUOTE.sub(" ", cleaned)
    return " ".join(cleaned.split())


def percentile(values, q: float) -> float:
    data = sorted(values)
    if not data:
        return float("nan")
    k = (len(data) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(data[lo])
    return float(data[lo] + (data[hi] - data[lo]) * (k - lo))


def latency_summary(values) -> dict:
    values = list(values)
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "mean": sum(values) / len(values) if values else float("nan"),
    }


def rtf(processing_s: float, audio_s: float) -> float:
    if audio_s <= 0:
        raise ValueError("audio duration must be > 0 for RTF")
    return processing_s / audio_s


def wer(refs, hyps) -> float:
    import jiwer

    pairs = [(normalize_text(r), normalize_text(h)) for r, h in zip(refs, hyps, strict=True)]
    pairs = [(r, h) for r, h in pairs if r]
    if not pairs:
        return float("nan")
    return float(jiwer.wer([r for r, _ in pairs], [h for _, h in pairs]))


def chrf(hyps, refs) -> float:
    import sacrebleu

    return float(sacrebleu.corpus_chrf(list(hyps), [list(refs)]).score)
```

- [ ] **Step 4: Реалізувати `thresholds.py` і `thresholds.yml`**

```python
# scripts/bench/thresholds.py
"""Acceptance gates (spec §6.4): dotted metric paths with min/max limits."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLDS = Path(__file__).with_name("thresholds.yml")


@dataclass(frozen=True)
class Check:
    metric: str
    op: str
    limit: float
    actual: Any
    passed: bool


def lookup(result: dict, dotted: str) -> Any:
    current: Any = result
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _ok(actual: Any, op: str, limit: float) -> bool:
    if not isinstance(actual, (int, float)) or isinstance(actual, bool) or math.isnan(actual):
        return False
    return actual <= limit if op == "<=" else actual >= limit


def evaluate(result: dict, rules: dict[str, dict]) -> list[Check]:
    checks = []
    for metric, rule in rules.items():
        actual = lookup(result, metric)
        for op, key in (("<=", "max"), (">=", "min")):
            if key in rule:
                limit = float(rule[key])
                checks.append(Check(metric, op, limit, actual, _ok(actual, op, limit)))
    return checks


def gate_passed(checks: list[Check]) -> bool:
    return bool(checks) and all(c.passed for c in checks)


def format_checks(checks: list[Check]) -> str:
    return "\n".join(f"{'PASS' if c.passed else 'FAIL'}  {c.metric} = {c.actual} ({c.op} {c.limit})"
                     for c in checks)


def load_thresholds(path: Path = DEFAULT_THRESHOLDS) -> dict[str, dict]:
    import yaml

    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["gates"]
```

```yaml
# scripts/bench/thresholds.yml
# Acceptance thresholds (spec §6.4). Keys are dotted paths into a bench result JSON.
# WER is a fraction (0.12 = 12 %), chrF is 0..100, latency in seconds, memory in MiB.
gates:
  asr-cuda:
    metrics.rtf: {max: 0.10}
    metrics.latency_le10s.p95: {max: 0.6}
    metrics.wer.uk: {max: 0.12}
    metrics.wer.pl: {max: 0.10}
    resources.vram_peak_mib: {max: 1638}
  asr-cpu:
    metrics.rtf: {max: 0.35}
    metrics.latency_le10s.p95: {max: 2.0}
    metrics.wer.uk: {max: 0.25}
    metrics.wer.pl: {max: 0.20}
    resources.rss_peak_mib: {max: 1536}
  mt-cuda:
    metrics.chrf.uk-en: {min: 52}
    metrics.chrf.pl-en: {min: 48}
    metrics.latency.p95: {max: 0.150}
    resources.vram_peak_mib: {max: 1126}
  mt-cpu:
    metrics.chrf.uk-en: {min: 52}
    metrics.chrf.pl-en: {min: 48}
    metrics.latency.p95: {max: 0.700}
    resources.rss_peak_mib: {max: 1536}
  tts-cpu:
    metrics.rtf: {max: 0.35}
    metrics.first_chunk.p95: {max: 0.8}
    metrics.sanity_rate: {min: 1.0}
    metrics.roundtrip_wer: {max: 0.15}
  tts-cuda:
    metrics.rtf: {max: 0.05}
    metrics.first_chunk.p95: {max: 0.25}
    metrics.sanity_rate: {min: 1.0}
    resources.vram_peak_mib: {max: 921}
  system-cyborg:
    resources.device_used_peak_mib: {max: 7577}
    metrics.error_count: {max: 0}
```

- [ ] **Step 5: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/bench/test_rt_metrics.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/bench/metrics.py scripts/bench/thresholds.py scripts/bench/thresholds.yml tests/bench/test_rt_metrics.py
git commit -m "feat(bench): add metrics (RTF, latency, WER, chrF) and acceptance gates"
```

---

### Task 14: Семплер ресурсів (VRAM / RSS)

**Files:**
- Create: `scripts/bench/resources.py`
- Test: `tests/bench/test_rt_resources.py`

**Interfaces:**
- Produces: `MIB = 1024 * 1024`; `NullProbe`, `NvmlProbe(index=0)`, `RocmSysfsProbe(device_dir: Path | None = None)` — кожен з `sample(pid: int) -> tuple[float | None, float | None]` (process MiB, device-used MiB); `auto_probe(kind: str = "auto")`; `ResourceSampler(pid=None, probe=None, interval=0.05, rss_fn=None)` з `sample_once()`, `start()`, `stop() -> dict`, `summary() -> dict` (`vram_peak_mib`, `device_used_peak_mib`, `rss_peak_mib`).

- [ ] **Step 1: Написати тест**

```python
# tests/bench/test_rt_resources.py
from scripts.bench import resources as rs


class FakeProbe:
    def __init__(self, samples):
        self.samples = list(samples)

    def sample(self, pid):
        return self.samples.pop(0) if self.samples else (None, None)


def test_sample_once_tracks_peaks():
    rss = iter([100 * rs.MIB, 300 * rs.MIB, 200 * rs.MIB])
    sampler = rs.ResourceSampler(pid=1, probe=FakeProbe([(500.0, 3000.0), (900.0, 2500.0),
                                                         (None, 3500.0)]),
                                 rss_fn=lambda: next(rss))
    for _ in range(3):
        sampler.sample_once()
    assert sampler.summary() == {"vram_peak_mib": 900.0, "device_used_peak_mib": 3500.0,
                                 "rss_peak_mib": 300.0}


def test_null_probe_reports_none():
    sampler = rs.ResourceSampler(pid=1, probe=rs.NullProbe(), rss_fn=lambda: 50 * rs.MIB)
    sampler.sample_once()
    assert sampler.summary()["vram_peak_mib"] is None


def test_thread_start_stop():
    sampler = rs.ResourceSampler(probe=FakeProbe([(10.0, 20.0)] * 1000), interval=0.01,
                                 rss_fn=lambda: rs.MIB)
    sampler.start()
    summary = sampler.stop()
    assert summary["vram_peak_mib"] == 10.0 and summary["rss_peak_mib"] == 1.0


def test_rocm_sysfs_probe(tmp_path):
    (tmp_path / "mem_info_vram_used").write_text(str(100 * rs.MIB))
    (tmp_path / "mem_info_gtt_used").write_text(str(50 * rs.MIB))
    probe = rs.RocmSysfsProbe(device_dir=tmp_path)
    (tmp_path / "mem_info_gtt_used").write_text(str(250 * rs.MIB))
    assert probe.sample(123) == (200.0, 350.0)


def test_auto_probe_none():
    assert isinstance(rs.auto_probe("none"), rs.NullProbe)
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/bench/test_rt_resources.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати**

```python
# scripts/bench/resources.py
"""Peak VRAM / RSS sampling for one benchmark process (spec §6.2)."""
from __future__ import annotations

import glob
import os
import threading
from pathlib import Path
from typing import Callable

MIB = 1024 * 1024


class NullProbe:
    def sample(self, pid: int) -> tuple[float | None, float | None]:
        return None, None


class NvmlProbe:
    """Per-PID usedGpuMemory plus device-wide used memory via NVML."""

    def __init__(self, index: int = 0):
        import pynvml

        pynvml.nvmlInit()
        self._nvml = pynvml
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(index)

    def sample(self, pid: int) -> tuple[float | None, float | None]:
        nvml = self._nvml
        device_used = nvml.nvmlDeviceGetMemoryInfo(self._handle).used / MIB
        process = None
        for proc in nvml.nvmlDeviceGetComputeRunningProcesses(self._handle):
            if proc.pid == pid and proc.usedGpuMemory is not None:
                process = proc.usedGpuMemory / MIB
        return process, device_used


class RocmSysfsProbe:
    """amdgpu sysfs counters; process usage = (vram+gtt) delta from construction time."""

    def __init__(self, device_dir: Path | None = None):
        if device_dir is None:
            candidates = sorted(glob.glob("/sys/class/drm/card*/device/mem_info_vram_used"))
            if not candidates:
                raise RuntimeError("no amdgpu mem_info_vram_used in sysfs")
            device_dir = Path(candidates[0]).parent
        self._dir = Path(device_dir)
        self._baseline = self._total()

    def _read(self, name: str) -> int:
        path = self._dir / name
        return int(path.read_text().strip()) if path.exists() else 0

    def _total(self) -> int:
        return self._read("mem_info_vram_used") + self._read("mem_info_gtt_used")

    def sample(self, pid: int) -> tuple[float | None, float | None]:
        total = self._total()
        return max(0, total - self._baseline) / MIB, total / MIB


def auto_probe(kind: str = "auto"):
    if kind == "none":
        return NullProbe()
    if kind in ("auto", "nvml"):
        try:
            return NvmlProbe()
        except Exception:
            if kind == "nvml":
                raise
    if kind in ("auto", "rocm"):
        try:
            return RocmSysfsProbe()
        except Exception:
            if kind == "rocm":
                raise
    return NullProbe()


class ResourceSampler:
    def __init__(self, pid: int | None = None, probe=None, interval: float = 0.05,
                 rss_fn: Callable[[], int] | None = None):
        self.pid = pid or os.getpid()
        self.probe = probe or NullProbe()
        self.interval = interval
        if rss_fn is None:
            import psutil

            process = psutil.Process(self.pid)

            def rss_fn() -> int:
                return process.memory_info().rss

        self._rss_fn = rss_fn
        self.vram_peak: float | None = None
        self.device_peak: float | None = None
        self.rss_peak = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample_once(self) -> None:
        process, device = self.probe.sample(self.pid)
        if process is not None:
            self.vram_peak = max(self.vram_peak or 0.0, process)
        if device is not None:
            self.device_peak = max(self.device_peak or 0.0, device)
        self.rss_peak = max(self.rss_peak, self._rss_fn() / MIB)

    def _loop(self) -> None:
        self.sample_once()
        while not self._stop.wait(self.interval):
            self.sample_once()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self.sample_once()
        return self.summary()

    def summary(self) -> dict:
        def rnd(value):
            return None if value is None else round(value, 1)

        return {"vram_peak_mib": rnd(self.vram_peak), "device_used_peak_mib": rnd(self.device_peak),
                "rss_peak_mib": rnd(self.rss_peak)}
```

- [ ] **Step 4: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/bench/test_rt_resources.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/bench/resources.py tests/bench/test_rt_resources.py
git commit -m "feat(bench): add VRAM/RSS peak sampler (NVML, amdgpu sysfs)"
```

---

### Task 15: Дані (локальні набори, FLORES+, FLEURS)

**Files:**
- Create: `scripts/bench/datasets.py`, `scripts/bench/data/tts_sentences.txt`
- Test: `tests/bench/test_rt_datasets.py`

**Interfaces:**
- Consumes: `to_nllb_code` (Task 7).
- Produces: `AsrItem(id, lang, audio_path, text)`; `load_asr_dir(root: Path, langs: list[str], limit: int | None = None) -> list[AsrItem]`; `read_audio_16k(path) -> np.ndarray`; `load_mt_pair(root: Path, pair: str) -> tuple[list[str], list[str]]`; `load_sentences(path) -> list[str]`; `jsonl_texts(path, limit) -> list[str]`; `fleurs_table_to_dir(table, out_dir: Path, limit: int) -> int`; `fetch_flores(out_dir, token, limit=200)`; `fetch_fleurs(out_dir, langs, limit, token)`; константи `FLORES_REPO`, `FLORES_REVISION`, `FLEURS_REPO`, `FLEURS_REVISION`, `FLEURS_CONFIGS`, `TTS_SENTENCES`.

- [ ] **Step 1: Створити `scripts/bench/data/tts_sentences.txt`**

```text
# 20 English sentences, 5-25 words (spec §6.3)
Good morning, everyone, and welcome back.
Thank you for joining the meeting today.
The train to Warsaw leaves at half past seven.
Could you please repeat the last question more slowly?
We need to finish the report before Friday afternoon.
The weather in Kyiv is cold but sunny this week.
I think the second option is cheaper and faster.
Please send me the updated contract by email.
Our team has already tested the new version of the application.
The doctor said that the results look normal and there is nothing to worry about.
How much does a return ticket to Krakow cost?
Let me check the schedule and call you back in ten minutes.
The museum is closed on Mondays, but it opens early on Tuesday.
They are building a new bridge across the river near the city center.
If the delivery is late again, we will have to change the supplier.
She studied computer science in Lviv and now works for an international company.
The price includes breakfast, free parking and access to the swimming pool.
Unfortunately, the flight was cancelled because of the storm, so we stayed another night at the airport hotel.
Before you start the engine, make sure that all the doors are closed and everyone is wearing a seat belt.
According to the latest survey, most people prefer to work from home at least two days a week.
```

- [ ] **Step 2: Написати тест**

```python
# tests/bench/test_rt_datasets.py
import io
import json

import numpy as np
import pyarrow as pa
import pytest
import soundfile as sf

from scripts.bench import datasets as ds


def write_wav(path, seconds=0.5, sr=16000, channels=1):
    data = np.zeros((int(seconds * sr), channels), dtype=np.float32) if channels > 1 \
        else np.zeros(int(seconds * sr), dtype=np.float32)
    sf.write(path, data, sr)


def test_load_asr_dir(tmp_path):
    for lang in ("uk", "pl"):
        (tmp_path / lang).mkdir()
        for i in range(3):
            write_wav(tmp_path / lang / f"{i}.wav")
            (tmp_path / lang / f"{i}.txt").write_text(f" text {i}\n")
    items = ds.load_asr_dir(tmp_path, ["uk", "pl"], limit=2)
    assert [(i.lang, i.id, i.text) for i in items] == [
        ("uk", "0", "text 0"), ("uk", "1", "text 1"), ("pl", "0", "text 0"), ("pl", "1", "text 1")]


def test_load_asr_dir_missing_transcript(tmp_path):
    (tmp_path / "uk").mkdir()
    write_wav(tmp_path / "uk" / "a.wav")
    with pytest.raises(FileNotFoundError, match="a.txt"):
        ds.load_asr_dir(tmp_path, ["uk"])


def test_load_asr_dir_missing_lang(tmp_path):
    with pytest.raises(FileNotFoundError):
        ds.load_asr_dir(tmp_path, ["uk"])


def test_read_audio_16k(tmp_path):
    write_wav(tmp_path / "s.wav", channels=2)
    audio = ds.read_audio_16k(tmp_path / "s.wav")
    assert audio.ndim == 1 and audio.dtype == np.float32 and audio.size == 8000
    write_wav(tmp_path / "x.wav", sr=48000)
    with pytest.raises(ValueError, match="16000"):
        ds.read_audio_16k(tmp_path / "x.wav")


def test_load_mt_pair(tmp_path):
    (tmp_path / "flores_devtest.ukr_Cyrl.txt").write_text("а\nб\n")
    (tmp_path / "flores_devtest.eng_Latn.txt").write_text("a\nb\n")
    assert ds.load_mt_pair(tmp_path, "uk-en") == (["а", "б"], ["a", "b"])
    (tmp_path / "flores_devtest.pol_Latn.txt").write_text("x\n")
    with pytest.raises(ValueError, match="length"):
        ds.load_mt_pair(tmp_path, "pl-en")


def test_load_sentences_skips_comments(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("# c\n\nHello there.\n  Bye.  \n")
    assert ds.load_sentences(f) == ["Hello there.", "Bye."]
    assert len(ds.load_sentences(ds.TTS_SENTENCES)) == 20


def test_jsonl_texts(tmp_path):
    f = tmp_path / "x.jsonl"
    f.write_text("\n".join(json.dumps({"id": i, "text": f"t{i}"}) for i in range(5)))
    assert ds.jsonl_texts(f, 3) == ["t0", "t1", "t2"]


def test_fleurs_table_to_dir(tmp_path):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(1600, dtype=np.float32), 16000, format="WAV")
    audio = {"bytes": buf.getvalue(), "path": "a.wav"}
    table = pa.table({"id": [7, 7, 8], "audio": [audio, audio, audio],
                      "raw_transcription": ["Раз ", "Два", "Три"]})
    assert ds.fleurs_table_to_dir(table, tmp_path, limit=2) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "fleurs_0000.txt", "fleurs_0000.wav", "fleurs_0001.txt", "fleurs_0001.wav"]
    assert (tmp_path / "fleurs_0000.txt").read_text() == "Раз\n"
```

- [ ] **Step 3: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/bench/test_rt_datasets.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 4: Реалізувати**

```python
# scripts/bench/datasets.py
"""Benchmark datasets (spec §6.3).

Layout:
  bench-data/asr/<lang>/<id>.wav (16 kHz mono) + <id>.txt
  bench-data/mt/flores_devtest.<flores_code>.txt (aligned lines)
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.services.mt.nllb_codes import to_nllb_code

FLORES_REPO = "openlanguagedata/flores_plus"
FLORES_REVISION = "e707e62e762a9e2faca3933aa315f4665cc3cd8f"
FLORES_CODES = ("ukr_Cyrl", "pol_Latn", "eng_Latn")
FLEURS_REPO = "google/fleurs"
FLEURS_REVISION = "refs/convert/parquet"
FLEURS_CONFIGS = {"uk": "uk_ua", "pl": "pl_pl"}
TTS_SENTENCES = Path(__file__).with_name("data") / "tts_sentences.txt"


@dataclass(frozen=True)
class AsrItem:
    id: str
    lang: str
    audio_path: Path
    text: str


def load_asr_dir(root: Path, langs: list[str], limit: int | None = None) -> list[AsrItem]:
    items: list[AsrItem] = []
    for lang in langs:
        lang_dir = Path(root) / lang
        if not lang_dir.is_dir():
            raise FileNotFoundError(f"missing ASR data dir {lang_dir}")
        wavs = sorted(lang_dir.glob("*.wav"))[:limit] if limit else sorted(lang_dir.glob("*.wav"))
        for wav in wavs:
            txt = wav.with_suffix(".txt")
            if not txt.is_file():
                raise FileNotFoundError(f"missing transcript {txt}")
            items.append(AsrItem(wav.stem, lang, wav, txt.read_text(encoding="utf-8").strip()))
    if not items:
        raise ValueError(f"no ASR items under {root} for {langs}")
    return items


def read_audio_16k(path: Path) -> np.ndarray:
    import soundfile

    data, sample_rate = soundfile.read(str(path), dtype="float32", always_2d=False)
    if sample_rate != 16000:
        raise ValueError(f"{path}: expected 16000 Hz, got {sample_rate}")
    if data.ndim == 2:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32)


def load_mt_pair(root: Path, pair: str) -> tuple[list[str], list[str]]:
    src, tgt = pair.split("-")

    def read(code: str) -> list[str]:
        path = Path(root) / f"flores_devtest.{code}.txt"
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]

    sources, references = read(to_nllb_code(src)), read(to_nllb_code(tgt))
    if len(sources) != len(references):
        raise ValueError(f"{pair}: source/reference length differs "
                         f"({len(sources)} vs {len(references)})")
    return sources, references


def load_sentences(path: Path) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def jsonl_texts(path: Path, limit: int) -> list[str]:
    texts = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if len(texts) >= limit:
                break
            if line.strip():
                texts.append(json.loads(line)["text"].strip())
    return texts


def fleurs_table_to_dir(table, out_dir: Path, limit: int) -> int:
    import soundfile

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = table.slice(0, limit).select(["audio", "raw_transcription"]).to_pylist()
    for index, row in enumerate(rows):
        data, sample_rate = soundfile.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
        if sample_rate != 16000:
            raise ValueError(f"FLEURS row {index}: expected 16000 Hz, got {sample_rate}")
        stem = f"fleurs_{index:04d}"
        soundfile.write(str(out_dir / f"{stem}.wav"), data, 16000, subtype="PCM_16")
        (out_dir / f"{stem}.txt").write_text(row["raw_transcription"].strip() + "\n",
                                             encoding="utf-8")
    return len(rows)


def fetch_flores(out_dir: Path, token: str | None, limit: int = 200) -> None:
    from huggingface_hub import hf_hub_download

    target = Path(out_dir) / "mt"
    target.mkdir(parents=True, exist_ok=True)
    for code in FLORES_CODES:
        path = hf_hub_download(FLORES_REPO, f"devtest/{code}.jsonl", repo_type="dataset",
                               revision=FLORES_REVISION, token=token)
        texts = jsonl_texts(Path(path), limit)
        (target / f"flores_devtest.{code}.txt").write_text("\n".join(texts) + "\n", encoding="utf-8")


def fetch_fleurs(out_dir: Path, langs: list[str], limit: int, token: str | None) -> None:
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download

    for lang in langs:
        config = FLEURS_CONFIGS[lang]
        path = hf_hub_download(FLEURS_REPO, f"{config}/test/0000.parquet", repo_type="dataset",
                               revision=FLEURS_REVISION, token=token)
        table = pq.read_table(path, columns=["audio", "raw_transcription"])
        fleurs_table_to_dir(table, Path(out_dir) / "asr" / lang, limit)
```

- [ ] **Step 5: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/bench/test_rt_datasets.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/bench/datasets.py scripts/bench/data/tts_sentences.txt tests/bench/test_rt_datasets.py
git commit -m "feat(bench): add dataset loaders and FLORES+/FLEURS fetchers"
```

---

### Task 16: Раннери ASR / MT / TTS і CLI

**Files:**
- Create: `scripts/bench/common.py`, `scripts/bench/run_asr.py`, `scripts/bench/run_mt.py`, `scripts/bench/run_tts.py`, `scripts/bench/cli.py`, `scripts/bench/__main__.py`
- Test: `tests/bench/test_rt_runners.py`

**Interfaces:**
- Consumes: усе з Tasks 4, 5, 8, 10, 11, 12, 13, 14, 15.
- Produces:
  - `common.host_info() -> dict`; `common.settings_from_args(args) -> ModelSettings`; `common.resolve_service_model(args, service) -> tuple[Path, str, str | None, int]`; `common.write_json(result, out) -> None`; `common.default_sampler(kind) -> ResourceSampler`;
  - `run_asr.run(args, *, engine_factory=None, sampler_factory=None) -> dict`; `run_asr.asr_metrics(records, load_s) -> dict`;
  - `run_mt.run(args, *, translator_factory=None, sampler_factory=None) -> dict`;
  - `run_tts.run(args, *, synth_factory=None, asr_factory=None, sampler_factory=None) -> dict`; `run_tts.tts_sanity(audio, sample_rate, text) -> bool`; `run_tts.resample_linear(audio, src_sr, dst_sr) -> np.ndarray`;
  - `cli.build_parser() -> argparse.ArgumentParser`; `cli.main(argv=None) -> int` (підкоманди `asr`, `mt`, `tts`, `system`, `matrix`, `fetch-data`; `--gate` → exit 1, якщо gate не пройдено).
  - Формат результату: `{"kind", "host", "cpu_count", "python", "config": {...}, "metrics": {...}, "resources": {...}, "records"?: [...], "gate"?: {"name", "passed", "checks": [...]}}`.

- [ ] **Step 1: Написати тест**

```python
# tests/bench/test_rt_runners.py
import json

import numpy as np
import pytest
import soundfile as sf

from scripts.bench import cli, run_asr, run_mt, run_tts
from src.services.asr.whisper_engine import TranscriptionResult


@pytest.fixture(autouse=True)
def _hf_env_sandbox(monkeypatch):
    # prepare_model()/configure_hf_home() mutate HF_* env; monkeypatch restores them.
    for name in ("HF_HOME", "HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.setenv(name, "")


class NoSampler:
    def start(self):
        pass

    def stop(self):
        return {"vram_peak_mib": None, "device_used_peak_mib": None, "rss_peak_mib": 10.0}


@pytest.fixture
def own_model(tmp_path):
    d = tmp_path / "model"
    d.mkdir()
    return d


def parse(*argv):
    return cli.build_parser().parse_args(list(argv))


def common_args(tmp_path):
    return ["--model-dir", str(tmp_path / "models")]


def test_asr_runner(tmp_path, own_model):
    for lang in ("uk", "pl"):
        (tmp_path / "asr" / lang).mkdir(parents=True)
        sf.write(tmp_path / "asr" / lang / "a.wav", np.zeros(32000, dtype=np.float32), 16000)
        (tmp_path / "asr" / lang / "a.txt").write_text("раз два")

    class Engine:
        def transcribe(self, audio, *, language, beam_size, vad=None, **kw):
            return TranscriptionResult("раз два" if language == "uk" else "раз", language, [], 2.0)

    args = parse("asr", "--model", str(own_model), "--device", "cpu", "--threads", "2",
                 "--data", str(tmp_path / "asr"), "--out", str(tmp_path / "r.json"),
                 *common_args(tmp_path))
    result = run_asr.run(args, engine_factory=lambda *a: Engine(), sampler_factory=lambda k: NoSampler())
    assert result["kind"] == "asr"
    assert result["config"]["beam_size"] == 1 and result["config"]["vad"] == "file"
    assert result["metrics"]["wer"] == {"uk": 0.0, "pl": 0.5}
    assert result["metrics"]["latency_le10s"]["n"] == 2
    assert result["resources"]["rss_peak_mib"] == 10.0


def test_asr_metrics_rtf():
    records = [{"lang": "uk", "duration": 4.0, "seconds": 1.0, "ref": "a", "hyp": "a"},
               {"lang": "uk", "duration": 12.0, "seconds": 3.0, "ref": "b", "hyp": "b"}]
    metrics = run_asr.asr_metrics(records, 0.5)
    assert metrics["rtf"] == 0.25
    assert metrics["latency_le10s"]["n"] == 1


def test_mt_runner(tmp_path, own_model):
    mt = tmp_path / "mt"
    mt.mkdir()
    (mt / "flores_devtest.ukr_Cyrl.txt").write_text("Кіт сидить.\n")
    (mt / "flores_devtest.pol_Latn.txt").write_text("Kot siedzi.\n")
    (mt / "flores_devtest.eng_Latn.txt").write_text("The cat sits.\n")

    class Translator:
        def translate(self, texts, source_lang, target_lang, num_beams=4):
            return ["The cat sits." for _ in texts]

    args = parse("mt", "--model", str(own_model), "--data", str(mt), "--out", str(tmp_path / "r.json"),
                 *common_args(tmp_path))
    result = run_mt.run(args, translator_factory=lambda *a: Translator(),
                        sampler_factory=lambda k: NoSampler())
    assert result["metrics"]["chrf"]["uk-en"] == pytest.approx(100.0)
    assert result["metrics"]["chrf"]["pl-en"] == pytest.approx(100.0)
    assert result["config"]["num_beams"] == 2
    assert result["metrics"]["latency"]["n"] == 2


def test_tts_runner(tmp_path, own_model):
    sentences = tmp_path / "s.txt"
    sentences.write_text("Hello there my friend.\n")

    class Synth:
        sample_rate = 24000

        def synthesize(self, text, voice, speed):
            t = np.linspace(0, 1, 24000, dtype=np.float32)
            yield 0.3 * np.sin(2 * np.pi * 220 * t)
            yield 0.3 * np.sin(2 * np.pi * 220 * t)

    class Asr:
        def transcribe(self, audio, *, language, beam_size, **kw):
            assert audio.size == 32000
            return TranscriptionResult("hello there my friend", "en", [], 2.0)

    args = parse("tts", "--model", str(own_model), "--sentences", str(sentences),
                 "--roundtrip-asr", str(own_model), "--out", str(tmp_path / "r.json"),
                 *common_args(tmp_path))
    result = run_tts.run(args, synth_factory=lambda *a: Synth(), asr_factory=lambda *a: Asr(),
                         sampler_factory=lambda k: NoSampler())
    assert result["metrics"]["sanity_rate"] == 1.0
    assert result["metrics"]["roundtrip_wer"] == 0.0
    assert result["metrics"]["first_chunk"]["n"] == 1


def test_tts_sanity():
    sr = 24000
    tone = 0.3 * np.sin(np.linspace(0, 400, sr * 2)).astype(np.float32)
    assert run_tts.tts_sanity(tone, sr, "one two three four five") is True
    assert run_tts.tts_sanity(np.zeros(sr * 2, dtype=np.float32), sr, "one two three four five") is False
    assert run_tts.tts_sanity(np.full(sr * 2, np.nan, dtype=np.float32), sr, "a b c d e") is False
    assert run_tts.tts_sanity(tone[:100], sr, "one two three four five") is False


def test_resample_linear():
    out = run_tts.resample_linear(np.ones(24000, dtype=np.float32), 24000, 16000)
    assert out.size == 16000 and out.dtype == np.float32


def test_cli_gate_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.run_mt, "run", lambda args: {
        "kind": "mt", "metrics": {"chrf": {"uk-en": 40.0, "pl-en": 60.0}, "latency": {"p95": 0.1}},
        "resources": {"vram_peak_mib": 500.0, "rss_peak_mib": 900.0}})
    out = tmp_path / "r.json"
    assert cli.main(["mt", "--out", str(out), "--gate", "mt-cpu"]) == 1
    saved = json.loads(out.read_text())
    assert saved["gate"]["name"] == "mt-cpu" and saved["gate"]["passed"] is False
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/bench/test_rt_runners.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати `common.py`**

```python
# scripts/bench/common.py
"""Shared helpers for benchmark runners."""
from __future__ import annotations

import json
import os
import platform
import socket
from pathlib import Path

from src.services.common.device import default_model, default_threads, normalize_device
from src.services.common.model_store import ModelSettings, prepare_model

from .resources import ResourceSampler, auto_probe


def host_info() -> dict:
    return {"host": socket.gethostname(), "cpu_count": os.cpu_count(),
            "python": platform.python_version()}


def settings_from_args(args) -> ModelSettings:
    env_settings = ModelSettings.from_env()
    return ModelSettings(
        model_dir=Path(args.model_dir) if args.model_dir else env_settings.model_dir,
        download=not args.no_download,
        full_verify=False,
        token_file=args.hf_token_file or env_settings.token_file,
    )


def resolve_service_model(args, service: str) -> tuple[Path, str, str | None, int]:
    device = normalize_device(args.device)
    alias = args.model or default_model(service, device)
    path = prepare_model(alias, settings_from_args(args))
    return path, device, args.compute_type, args.threads or default_threads()


def default_sampler(kind: str) -> ResourceSampler:
    return ResourceSampler(probe=auto_probe(kind))


def write_json(result: dict, out: str | Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
```

- [ ] **Step 4: Реалізувати `run_asr.py`**

```python
# scripts/bench/run_asr.py
"""ASR benchmark: RTF, per-utterance latency, WER per language, peak VRAM/RSS."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter

import numpy as np

from src.services.asr.whisper_engine import FILE_VAD, STREAMING_VAD, WhisperEngine
from src.services.common.device import default_beams

from .common import default_sampler, host_info, resolve_service_model
from .datasets import load_asr_dir, read_audio_16k
from .metrics import latency_summary, rtf, wer

VAD_PROFILES = {"file": FILE_VAD, "streaming": STREAMING_VAD, "none": None}


def asr_metrics(records: list[dict], load_s: float) -> dict:
    langs = sorted({r["lang"] for r in records})
    return {
        "load_s": round(load_s, 3),
        "n": len(records),
        "rtf": rtf(sum(r["seconds"] for r in records), sum(r["duration"] for r in records)),
        "latency": latency_summary([r["seconds"] for r in records]),
        "latency_le10s": latency_summary([r["seconds"] for r in records if r["duration"] <= 10.0]),
        "wer": {lang: wer([r["ref"] for r in records if r["lang"] == lang],
                          [r["hyp"] for r in records if r["lang"] == lang]) for lang in langs},
    }


def run(args, *, engine_factory=None, sampler_factory=None) -> dict:
    path, device, compute_type, threads = resolve_service_model(args, "asr")
    beam = args.beam or default_beams("asr", device)
    vad = VAD_PROFILES[args.vad]
    if vad is not None and args.vad_silence_ms:
        vad = replace(vad, min_silence_duration_ms=args.vad_silence_ms)
    items = load_asr_dir(Path(args.data), args.langs.split(","), limit=args.limit)
    sampler = (sampler_factory or default_sampler)(args.gpu_probe)
    sampler.start()
    records = []
    try:
        started = perf_counter()
        engine = (engine_factory or WhisperEngine.from_settings)(path, device, compute_type, threads)
        load_s = perf_counter() - started
        engine.transcribe(np.zeros(16000, dtype=np.float32), language=items[0].lang,
                          beam_size=beam, vad=None)
        for item in items:
            audio = read_audio_16k(item.audio_path)
            t0 = perf_counter()
            result = engine.transcribe(audio, language=item.lang, beam_size=beam, vad=vad)
            records.append({"id": item.id, "lang": item.lang, "duration": audio.size / 16000,
                            "seconds": perf_counter() - t0, "ref": item.text, "hyp": result.text})
    finally:
        resources = sampler.stop()
    return {
        "kind": "asr", **host_info(),
        "config": {"model": args.model or path.name, "path": str(path), "device": device,
                   "compute_type": getattr(engine, "compute_type", compute_type),
                   "threads": threads, "beam_size": beam, "vad": args.vad,
                   "vad_parameters": vad.as_dict() if vad else None},
        "metrics": asr_metrics(records, load_s),
        "resources": resources,
        "records": records,
    }
```

- [ ] **Step 5: Реалізувати `run_mt.py`**

```python
# scripts/bench/run_mt.py
"""MT benchmark: chrF per pair on FLORES+ devtest, single-sentence latency, throughput."""
from __future__ import annotations

from pathlib import Path
from time import perf_counter

from src.services.common.device import default_beams
from src.services.mt.ct2_translator import Ct2NllbTranslator

from .common import default_sampler, host_info, resolve_service_model
from .datasets import load_mt_pair
from .metrics import chrf, latency_summary


def run(args, *, translator_factory=None, sampler_factory=None) -> dict:
    path, device, compute_type, threads = resolve_service_model(args, "mt")
    beams = args.beams or default_beams("mt", device)
    pairs = args.pairs.split(",")
    data = {pair: load_mt_pair(Path(args.data), pair) for pair in pairs}
    sampler = (sampler_factory or default_sampler)(args.gpu_probe)
    sampler.start()
    latencies: list[float] = []
    scores, throughput, samples = {}, {}, {}
    try:
        started = perf_counter()
        translator = (translator_factory or Ct2NllbTranslator.from_settings)(
            path, device, compute_type, threads)
        load_s = perf_counter() - started
        first_src, first_tgt = pairs[0].split("-")
        translator.translate([data[pairs[0]][0][0]], first_src, first_tgt, num_beams=beams)
        for pair, (sources, references) in data.items():
            src, tgt = pair.split("-")
            for sentence in sources[: args.latency_samples]:
                t0 = perf_counter()
                translator.translate([sentence], src, tgt, num_beams=beams)
                latencies.append(perf_counter() - t0)
            t0 = perf_counter()
            hypotheses = translator.translate(sources, src, tgt, num_beams=beams)
            elapsed = perf_counter() - t0
            scores[pair] = chrf(hypotheses, references)
            throughput[pair] = len(sources) / elapsed if elapsed > 0 else float("inf")
            samples[pair] = [{"src": s, "hyp": h, "ref": r}
                             for s, h, r in list(zip(sources, hypotheses, references))[:5]]
    finally:
        resources = sampler.stop()
    return {
        "kind": "mt", **host_info(),
        "config": {"model": args.model or path.name, "path": str(path), "device": device,
                   "compute_type": getattr(translator, "compute_type", compute_type),
                   "threads": threads, "num_beams": beams, "pairs": pairs},
        "metrics": {"load_s": round(load_s, 3), "chrf": scores,
                    "latency": latency_summary(latencies), "throughput_sps": throughput},
        "resources": resources,
        "samples": samples,
    }
```

- [ ] **Step 6: Реалізувати `run_tts.py`**

```python
# scripts/bench/run_tts.py
"""TTS benchmark: RTF, time-to-first-chunk, sanity, ASR round-trip WER."""
from __future__ import annotations

from pathlib import Path
from time import perf_counter

import numpy as np

from src.services.asr.whisper_engine import WhisperEngine
from src.services.common.device import default_model, default_threads, normalize_device
from src.services.common.model_store import prepare_model
from src.services.tts.engines import create_synthesizer

from .common import default_sampler, host_info, settings_from_args
from .datasets import load_sentences
from .metrics import latency_summary, rtf, wer


def tts_sanity(audio: np.ndarray, sample_rate: int, text: str) -> bool:
    if audio.size == 0 or not np.all(np.isfinite(audio)):
        return False
    if float(np.sqrt(np.mean(np.square(audio)))) <= 0.01:
        return False
    expected = max(len(text.split()) / 2.5, 0.5)
    duration = audio.size / sample_rate
    return 0.5 * expected <= duration <= 2.0 * expected


def resample_linear(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or audio.size == 0:
        return audio.astype(np.float32)
    n = int(round(audio.size * dst_sr / src_sr))
    old = np.linspace(0.0, 1.0, audio.size, endpoint=False)
    new = np.linspace(0.0, 1.0, n, endpoint=False)
    return np.interp(new, old, audio).astype(np.float32)


def run(args, *, synth_factory=None, asr_factory=None, sampler_factory=None) -> dict:
    device = normalize_device(args.device)
    threads = args.threads or default_threads()
    settings = settings_from_args(args)
    asr_path = None if args.roundtrip_asr == "none" else prepare_model(args.roundtrip_asr, settings)
    default_alias = "kokoro-82m-v1.0-torch" if args.engine == "torch" else default_model("tts", device)
    path = prepare_model(args.model or default_alias, settings)
    sentences = load_sentences(Path(args.sentences))
    sampler = (sampler_factory or default_sampler)(args.gpu_probe)
    sampler.start()
    records, audios = [], []
    try:
        started = perf_counter()
        synth = (synth_factory or create_synthesizer)(args.engine, path, device,
                                                      args.compute_type, threads)
        load_s = perf_counter() - started
        list(synth.synthesize("Warm up.", voice=args.voice, speed=args.speed))
        for text in sentences:
            t0 = perf_counter()
            first, chunks = None, []
            for chunk in synth.synthesize(text, voice=args.voice, speed=args.speed):
                if first is None:
                    first = perf_counter() - t0
                chunks.append(chunk)
            total = perf_counter() - t0
            audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
            records.append({"text": text, "seconds": total,
                            "first_chunk_s": first if first is not None else total,
                            "audio_s": audio.size / synth.sample_rate,
                            "sane": tts_sanity(audio, synth.sample_rate, text)})
            audios.append(audio)
    finally:
        resources = sampler.stop()
    sample_rate = synth.sample_rate
    del synth
    roundtrip = None
    if asr_path is not None:
        asr = (asr_factory or WhisperEngine.from_settings)(asr_path, "cpu", "int8", threads)
        hyps = [asr.transcribe(resample_linear(a, sample_rate, 16000), language="en",
                               beam_size=1).text for a in audios]
        roundtrip = wer(sentences, hyps)
    total_audio = sum(r["audio_s"] for r in records)
    return {
        "kind": "tts", **host_info(),
        "config": {"engine": args.engine, "model": args.model or default_alias, "path": str(path),
                   "device": device, "compute_type": args.compute_type, "threads": threads,
                   "voice": args.voice, "speed": args.speed, "roundtrip_asr": args.roundtrip_asr},
        "metrics": {
            "load_s": round(load_s, 3),
            "rtf": rtf(sum(r["seconds"] for r in records), total_audio) if total_audio > 0
            else float("inf"),
            "first_chunk": latency_summary([r["first_chunk_s"] for r in records]),
            "sanity_rate": sum(r["sane"] for r in records) / len(records),
            "roundtrip_wer": roundtrip,
        },
        "resources": resources,
        "records": records,
    }
```

- [ ] **Step 7: Реалізувати `cli.py` і `__main__.py`**

```python
# scripts/bench/cli.py
"""python -m scripts.bench {asr,mt,tts,system,matrix,fetch-data} ... (spec §6.2)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.services.common.hf_env import read_token_file
from src.services.tts.engines import DEFAULT_TTS_ENGINE

from . import run_asr, run_mt, run_tts
from .common import write_json
from .datasets import TTS_SENTENCES, fetch_fleurs, fetch_flores
from .thresholds import DEFAULT_THRESHOLDS, evaluate, format_checks, gate_passed, load_thresholds


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", help="registry alias or absolute model dir")
    p.add_argument("--model-dir", help="RT_MODEL_DIR override")
    p.add_argument("--device", default="cpu", choices=["cpu", "cuda", "rocm"])
    p.add_argument("--compute-type")
    p.add_argument("--threads", type=int)
    p.add_argument("--no-download", action="store_true")
    p.add_argument("--hf-token-file")
    p.add_argument("--gpu-probe", default="auto", choices=["auto", "nvml", "rocm", "none"])
    p.add_argument("--out", required=True)
    p.add_argument("--gate", help="gate name from thresholds.yml")
    p.add_argument("--thresholds", default=str(DEFAULT_THRESHOLDS))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.bench")
    sub = parser.add_subparsers(dest="cmd", required=True)

    asr = sub.add_parser("asr")
    _common(asr)
    asr.add_argument("--data", default="bench-data/asr")
    asr.add_argument("--langs", default="uk,pl")
    asr.add_argument("--beam", type=int)
    asr.add_argument("--vad", default="file", choices=["file", "streaming", "none"])
    asr.add_argument("--vad-silence-ms", type=int)
    asr.add_argument("--limit", type=int, help="max utterances per language")

    mt = sub.add_parser("mt")
    _common(mt)
    mt.add_argument("--data", default="bench-data/mt")
    mt.add_argument("--pairs", default="uk-en,pl-en")
    mt.add_argument("--beams", type=int)
    mt.add_argument("--latency-samples", type=int, default=100)

    tts = sub.add_parser("tts")
    _common(tts)
    tts.add_argument("--engine", default=DEFAULT_TTS_ENGINE, choices=["onnx", "torch"])
    tts.add_argument("--voice", default="af_heart")
    tts.add_argument("--speed", type=float, default=1.0)
    tts.add_argument("--sentences", default=str(TTS_SENTENCES))
    tts.add_argument("--roundtrip-asr", default="whisper-small",
                     help="ASR alias/path for round-trip WER, or 'none'")

    system = sub.add_parser("system")
    _common(system)
    system.add_argument("--asr-model", default="whisper-large-v3-turbo")
    system.add_argument("--mt-model", default="nllb-200-distilled-600M-ct2-int8")
    system.add_argument("--data-asr", default="bench-data/asr")
    system.add_argument("--data-mt", default="bench-data/mt")
    system.add_argument("--langs", default="uk,pl")
    system.add_argument("--pairs", default="uk-en,pl-en")
    system.add_argument("--duration", type=float, default=300.0)

    matrix = sub.add_parser("matrix")
    matrix.add_argument("--matrix", default=str(Path(__file__).with_name("matrix.yml")))
    matrix.add_argument("--profile", required=True)
    matrix.add_argument("--out-dir", required=True)
    matrix.add_argument("--thresholds", default=str(DEFAULT_THRESHOLDS))

    fetch = sub.add_parser("fetch-data")
    fetch.add_argument("--out", default="bench-data")
    fetch.add_argument("--hf-token-file")
    fetch.add_argument("--flores-limit", type=int, default=200)
    fetch.add_argument("--fleurs-limit", type=int, default=100)
    fetch.add_argument("--skip-flores", action="store_true")
    fetch.add_argument("--skip-fleurs", action="store_true")
    return parser


def _with_gate(result: dict, args) -> int:
    if not args.gate:
        write_json(result, args.out)
        return 0
    checks = evaluate(result, load_thresholds(Path(args.thresholds))[args.gate])
    passed = gate_passed(checks)
    result["gate"] = {"name": args.gate, "passed": passed,
                      "checks": [c.__dict__ for c in checks]}
    write_json(result, args.out)
    print(format_checks(checks))
    print(f"gate {args.gate}: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "asr":
        return _with_gate(run_asr.run(args), args)
    if args.cmd == "mt":
        return _with_gate(run_mt.run(args), args)
    if args.cmd == "tts":
        return _with_gate(run_tts.run(args), args)
    if args.cmd == "system":
        from . import system_load

        return _with_gate(system_load.run(args), args)
    if args.cmd == "matrix":
        from .matrix import run_matrix

        _, ok = run_matrix(Path(args.matrix), args.profile, Path(args.out_dir), Path(args.thresholds))
        return 0 if ok else 1
    if args.cmd == "fetch-data":
        token = read_token_file(args.hf_token_file)
        if not args.skip_flores:
            fetch_flores(Path(args.out), token, args.flores_limit)
        if not args.skip_fleurs:
            fetch_fleurs(Path(args.out), ["uk", "pl"], args.fleurs_limit, token)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

```python
# scripts/bench/__main__.py
import sys

from .cli import main

sys.exit(main())
```

- [ ] **Step 8: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/bench/test_rt_runners.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add scripts/bench/common.py scripts/bench/run_asr.py scripts/bench/run_mt.py scripts/bench/run_tts.py scripts/bench/cli.py scripts/bench/__main__.py tests/bench/test_rt_runners.py
git commit -m "feat(bench): add asr/mt/tts runners and bench CLI with gates"
```

---

### Task 17: Матриця кейсів і системне навантаження

**Files:**
- Create: `scripts/bench/matrix.py`, `scripts/bench/matrix.yml`, `scripts/bench/system_load.py`
- Test: `tests/bench/test_rt_matrix.py`

**Interfaces:**
- Consumes: `evaluate`, `gate_passed`, `load_thresholds` (Task 13); `ResourceSampler`, `auto_probe` (Task 14); `load_asr_dir`, `read_audio_16k`, `load_mt_pair` (Task 15); `host_info`, `settings_from_args` (Task 16); `WhisperEngine` (Task 10); `Ct2NllbTranslator` (Task 8); `prepare_model` (Task 5).
- Produces: `matrix.run_matrix(matrix_path: Path, profile: str, out_dir: Path, thresholds_path: Path, runner=default_runner) -> tuple[list[dict], bool]` (пише `out_dir/summary.md`); `matrix.default_runner(cmd: list[str], out_path: Path, env: dict[str, str]) -> int`; `system_load.run(args, *, probe=None, spawn=None) -> dict` (`metrics.requests`, `metrics.error_count`, `metrics.errors`, `resources.device_used_peak_mib`).

- [ ] **Step 1: Написати тест**

```python
# tests/bench/test_rt_matrix.py
import json

import pytest
import yaml

from scripts.bench import cli, system_load
from scripts.bench.matrix import run_matrix
from scripts.bench.thresholds import DEFAULT_THRESHOLDS

@pytest.fixture(autouse=True)
def _hf_env_sandbox(monkeypatch):
    # prepare_model()/configure_hf_home() mutate HF_* env; monkeypatch restores them.
    for name in ("HF_HOME", "HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.setenv(name, "")


MATRIX = {"profiles": {"t": [
    {"name": "good", "gate": "mt-cpu", "cmd": ["mt", "--device", "cpu"]},
    {"name": "info", "cmd": ["asr"], "env": {"X": 1}},
    {"name": "crash", "cmd": ["tts"]},
]}}


def fake_runner(cmd, out_path, env):
    name = out_path.stem
    if name == "crash":
        return 1
    result = {"kind": cmd[0],
              "metrics": {"chrf": {"uk-en": 60.0, "pl-en": 55.0}, "latency": {"p95": 0.3}, "rtf": 0.2},
              "resources": {"rss_peak_mib": 900.0, "vram_peak_mib": None}}
    assert env == ({"X": "1"} if name == "info" else {})
    out_path.write_text(json.dumps(result))
    return 0


def test_run_matrix(tmp_path):
    matrix_path = tmp_path / "m.yml"
    matrix_path.write_text(yaml.safe_dump(MATRIX))
    rows, ok = run_matrix(matrix_path, "t", tmp_path / "out", DEFAULT_THRESHOLDS, runner=fake_runner)
    status = {r["name"]: r["status"] for r in rows}
    assert status == {"good": "pass", "info": "done", "crash": "error"}
    assert ok is True  # crash has no gate
    summary = (tmp_path / "out" / "summary.md").read_text()
    assert "| good | mt-cpu | pass |" in summary


def test_run_matrix_gate_failure(tmp_path):
    matrix_path = tmp_path / "m.yml"
    matrix_path.write_text(yaml.safe_dump({"profiles": {"t": [
        {"name": "bad", "gate": "asr-cpu", "cmd": ["asr"]}]}}))
    _, ok = run_matrix(matrix_path, "t", tmp_path / "out", DEFAULT_THRESHOLDS, runner=fake_runner)
    assert ok is False


def test_repo_matrix_is_valid():
    from pathlib import Path

    data = yaml.safe_load(Path("scripts/bench/matrix.yml").read_text())
    gates = yaml.safe_load(DEFAULT_THRESHOLDS.read_text())["gates"]
    assert {"cyborg", "note17", "cpu-box"} <= set(data["profiles"])
    parser = cli.build_parser()
    for cases in data["profiles"].values():
        names = [c["name"] for c in cases]
        assert len(names) == len(set(names))
        for case in cases:
            assert case.get("gate") is None or case["gate"] in gates
            parser.parse_args([str(x) for x in case["cmd"]] + ["--out", "x.json"])


def test_system_load_run(tmp_path):
    class Probe:
        def __init__(self):
            self.values = iter([3000.0, 7000.0, 6500.0])

        def sample(self, pid):
            return None, next(self.values, 6000.0)

    model = tmp_path / "m"
    model.mkdir()
    args = cli.build_parser().parse_args([
        "system", "--asr-model", str(model), "--mt-model", str(model), "--duration", "0.1",
        "--model-dir", str(tmp_path / "models"), "--out", str(tmp_path / "s.json")])
    result = system_load.run(args, probe=Probe(), spawn=lambda jobs: [
        {"name": "asr", "requests": 3}, {"name": "mt", "requests": 9, "error": "boom"}])
    assert result["metrics"]["requests"] == {"asr": 3, "mt": 9}
    assert result["metrics"]["error_count"] == 1
    assert result["resources"]["device_used_peak_mib"] == 7000.0
```

- [ ] **Step 2: Запустити — має впасти**

Run: `nix develop .#bench-cpu -c pytest tests/bench/test_rt_matrix.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Реалізувати `matrix.py`**

```python
# scripts/bench/matrix.py
"""Run a profile of benchmark cases, each in a fresh subprocess (fresh CUDA context)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import yaml

from .thresholds import evaluate, gate_passed, load_thresholds, lookup

Runner = Callable[[list[str], Path, dict[str, str]], int]
KEY_METRICS = ["metrics.rtf", "metrics.latency.p95", "metrics.latency_le10s.p95", "metrics.wer",
               "metrics.chrf", "metrics.first_chunk.p95", "metrics.roundtrip_wer",
               "resources.vram_peak_mib", "resources.rss_peak_mib",
               "resources.device_used_peak_mib"]


def default_runner(cmd: list[str], out_path: Path, env: dict[str, str]) -> int:
    full = [sys.executable, "-m", "scripts.bench", *cmd, "--out", str(out_path)]
    return subprocess.run(full, env={**os.environ, **env}, timeout=4 * 3600).returncode


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items()) + "}"
    return str(value)


def _key_metrics(result: dict | None) -> str:
    if not result:
        return ""
    parts = []
    for key in KEY_METRICS:
        value = lookup(result, key)
        if value is not None:
            parts.append(f"{key.split('.', 1)[1]}={_fmt(value)}")
    return "; ".join(parts)


def run_matrix(matrix_path: Path, profile: str, out_dir: Path, thresholds_path: Path,
               runner: Runner = default_runner) -> tuple[list[dict], bool]:
    cases = yaml.safe_load(Path(matrix_path).read_text(encoding="utf-8"))["profiles"][profile]
    gates = load_thresholds(thresholds_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, ok = [], True
    for case in cases:
        out_path = out_dir / f"{case['name']}.json"
        env = {k: str(v) for k, v in (case.get("env") or {}).items()}
        code = runner([str(c) for c in case["cmd"]], out_path, env)
        result = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
        gate = case.get("gate")
        if result is None or code != 0:
            status = "error"
        elif gate:
            status = "pass" if gate_passed(evaluate(result, gates[gate])) else "fail"
        else:
            status = "done"
        if gate and status != "pass":
            ok = False
        rows.append({"name": case["name"], "gate": gate or "-", "status": status,
                     "metrics": _key_metrics(result)})
        print(f"[{status}] {case['name']}", flush=True)
    lines = [f"# Bench summary: profile `{profile}`", "",
             "| case | gate | status | key metrics |", "|---|---|---|---|"]
    lines += [f"| {r['name']} | {r['gate']} | {r['status']} | {r['metrics']} |" for r in rows]
    lines.append("")
    lines.append(f"Overall: {'PASS' if ok else 'FAIL'}")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows, ok
```

- [ ] **Step 4: Реалізувати `system_load.py`**

```python
# scripts/bench/system_load.py
"""Concurrent ASR + MT load on one GPU; device-wide peak VRAM (spec §6.4 system gate)."""
from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

from src.services.common.model_store import prepare_model

from .common import host_info, settings_from_args
from .resources import ResourceSampler, auto_probe


def asr_worker(model_path: str, data_dir: str, langs: str, duration: float) -> dict:
    from src.services.asr.whisper_engine import WhisperEngine

    from .datasets import load_asr_dir, read_audio_16k

    engine = WhisperEngine.from_settings(Path(model_path), "cuda", None, 4)
    clips = [(i.lang, read_audio_16k(i.audio_path))
             for i in load_asr_dir(Path(data_dir), langs.split(","), limit=20)]
    deadline, requests = time.monotonic() + duration, 0
    while time.monotonic() < deadline:
        for lang, audio in clips:
            engine.transcribe(audio, language=lang, beam_size=3)
            requests += 1
            if time.monotonic() >= deadline:
                break
    return {"name": "asr", "requests": requests}


def mt_worker(model_path: str, data_dir: str, pairs: str, duration: float) -> dict:
    from src.services.mt.ct2_translator import Ct2NllbTranslator

    from .datasets import load_mt_pair

    translator = Ct2NllbTranslator.from_settings(Path(model_path), "cuda", None, 4)
    work = [(pair.split("-"), load_mt_pair(Path(data_dir), pair)[0][:50])
            for pair in pairs.split(",")]
    deadline, requests = time.monotonic() + duration, 0
    while time.monotonic() < deadline:
        for (src, tgt), sentences in work:
            for sentence in sentences:
                translator.translate([sentence], src, tgt, num_beams=4)
                requests += 1
                if time.monotonic() >= deadline:
                    break
    return {"name": "mt", "requests": requests}


def _job(fn, fn_args, queue) -> None:
    try:
        queue.put(fn(*fn_args))
    except Exception as exc:  # report, do not crash the parent
        queue.put({"name": fn.__name__, "requests": 0, "error": repr(exc)})


def _spawn_processes(jobs) -> list[dict]:
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    procs = [ctx.Process(target=_job, args=(fn, fn_args, queue)) for fn, fn_args in jobs]
    for proc in procs:
        proc.start()
    reports = [queue.get(timeout=4 * 3600) for _ in procs]
    for proc in procs:
        proc.join()
    return reports


def run(args, *, probe=None, spawn=None) -> dict:
    settings = settings_from_args(args)
    asr_path = prepare_model(args.asr_model, settings)
    mt_path = prepare_model(args.mt_model, settings)
    sampler = ResourceSampler(probe=probe or auto_probe(args.gpu_probe), interval=0.1)
    sampler.start()
    try:
        reports = (spawn or _spawn_processes)([
            (asr_worker, (str(asr_path), args.data_asr, args.langs, args.duration)),
            (mt_worker, (str(mt_path), args.data_mt, args.pairs, args.duration)),
        ])
    finally:
        resources = sampler.stop()
    errors = [r["error"] for r in reports if r.get("error")]
    return {
        "kind": "system", **host_info(),
        "config": {"asr_model": args.asr_model, "mt_model": args.mt_model,
                   "duration_s": args.duration},
        "metrics": {"requests": {r["name"]: r["requests"] for r in reports},
                    "error_count": len(errors), "errors": errors},
        "resources": resources,
    }
```

- [ ] **Step 5: Створити `scripts/bench/matrix.yml`**

```yaml
# Benchmark matrix (spec §6.2). Each case runs in its own process:
#   python -m scripts.bench <cmd...> --out <out-dir>/<name>.json
# cyborg: run inside `nix develop .#bench-gpu` (has CUDA + CPU providers).
profiles:
  cyborg:
    - name: asr-turbo-cuda
      gate: asr-cuda
      cmd: [asr, --model, whisper-large-v3-turbo, --device, cuda, --compute-type, int8_float16, --beam, 3, --vad, file]
    - name: asr-medium-cuda
      cmd: [asr, --model, whisper-medium, --device, cuda, --compute-type, int8_float16, --beam, 3, --vad, file]
    - name: asr-small-cuda
      cmd: [asr, --model, whisper-small, --device, cuda, --compute-type, int8_float16, --beam, 3, --vad, file]
    - name: asr-small-cpu
      gate: asr-cpu
      cmd: [asr, --model, whisper-small, --device, cpu, --compute-type, int8, --threads, 4, --beam, 1, --vad, file]
    - name: asr-turbo-cuda-vad300
      cmd: [asr, --model, whisper-large-v3-turbo, --device, cuda, --compute-type, int8_float16, --beam, 3, --vad, streaming, --vad-silence-ms, 300]
    - name: asr-turbo-cuda-vad400
      cmd: [asr, --model, whisper-large-v3-turbo, --device, cuda, --compute-type, int8_float16, --beam, 3, --vad, streaming, --vad-silence-ms, 400]
    - name: asr-turbo-cuda-vad600
      cmd: [asr, --model, whisper-large-v3-turbo, --device, cuda, --compute-type, int8_float16, --beam, 3, --vad, streaming, --vad-silence-ms, 600]
    - name: mt-nllb-cuda
      gate: mt-cuda
      cmd: [mt, --model, nllb-200-distilled-600M-ct2-int8, --device, cuda, --compute-type, int8_float16, --beams, 4]
    - name: mt-nllb-cuda-capped-alloc
      env: {CT2_CUDA_CACHING_ALLOCATOR_CONFIG: "4,3,12,209715200"}
      cmd: [mt, --model, nllb-200-distilled-600M-ct2-int8, --device, cuda, --compute-type, int8_float16, --beams, 4]
    - name: mt-nllb-cpu
      gate: mt-cpu
      cmd: [mt, --model, nllb-200-distilled-600M-ct2-int8, --device, cpu, --compute-type, int8, --threads, 4, --beams, 2]
    - name: tts-onnx-cpu-fp32
      gate: tts-cpu
      cmd: [tts, --engine, onnx, --device, cpu, --compute-type, float32, --threads, 4]
    - name: tts-onnx-cpu-int8
      cmd: [tts, --engine, onnx, --device, cpu, --compute-type, int8, --threads, 4]
    - name: tts-torch-cpu
      cmd: [tts, --engine, torch, --device, cpu, --threads, 4]
    - name: tts-onnx-cuda-fp32
      gate: tts-cuda
      cmd: [tts, --engine, onnx, --device, cuda, --compute-type, float32]
    - name: tts-onnx-cuda-fp16
      cmd: [tts, --engine, onnx, --device, cuda, --compute-type, float16]
  note17:
    - name: asr-small-cpu4
      gate: asr-cpu
      cmd: [asr, --model, whisper-small, --device, cpu, --compute-type, int8, --threads, 4, --beam, 1, --vad, file]
    - name: asr-medium-cpu4
      cmd: [asr, --model, whisper-medium, --device, cpu, --compute-type, int8, --threads, 4, --beam, 1, --vad, file]
    - name: mt-nllb-cpu4
      gate: mt-cpu
      cmd: [mt, --model, nllb-200-distilled-600M-ct2-int8, --device, cpu, --compute-type, int8, --threads, 4, --beams, 2]
    - name: mt-nllb-cpu2
      cmd: [mt, --model, nllb-200-distilled-600M-ct2-int8, --device, cpu, --compute-type, int8, --threads, 2, --beams, 2]
    - name: tts-onnx-cpu4
      gate: tts-cpu
      cmd: [tts, --engine, onnx, --device, cpu, --compute-type, float32, --threads, 4]
    - name: tts-onnx-cpu2
      cmd: [tts, --engine, onnx, --device, cpu, --compute-type, float32, --threads, 2]
  cpu-box:
    - name: asr-small-cpu2
      cmd: [asr, --model, whisper-small, --device, cpu, --compute-type, int8, --threads, 2, --beam, 1, --vad, file, --limit, 30]
    - name: mt-nllb-cpu2
      cmd: [mt, --model, nllb-200-distilled-600M-ct2-int8, --device, cpu, --compute-type, int8, --threads, 2, --beams, 2, --latency-samples, 30]
    - name: tts-onnx-cpu2
      cmd: [tts, --engine, onnx, --device, cpu, --compute-type, float32, --threads, 2]
```

- [ ] **Step 6: Запустити — має пройти**

Run: `nix develop .#bench-cpu -c pytest tests/bench -q`
Expected: PASS (усі тести harness'у)

- [ ] **Step 7: Повний прогін нових тестів + lint**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_*.py tests/bench -q && nix develop -c pytest tests/services/test_svc_*.py -q`
Expected: PASS (golden — skipped)

- [ ] **Step 8: Commit**

```bash
git add scripts/bench/matrix.py scripts/bench/matrix.yml scripts/bench/system_load.py tests/bench/test_rt_matrix.py
git commit -m "feat(bench): add benchmark matrix runner, profiles and system load test"
```

---

## Phase E — Вимірювання на залізі і рішення

> Ці завдання не TDD: це виміри, що підтверджують або змінюють defaults зі spec. Кожне закінчується оновленням spec (цифри + рішення) і комітом. `bench-data/`, `bench-results/` не комітяться. Нижче `DATE=$(date +%F)`.

### Task 18: Провізіонування, дані й CPU-smoke на cyborg

**Files:**
- Modify: немає (лише локальні артефакти в `~/.cache/rt-speech/models`, `bench-data/`, `bench-results/`)

**Interfaces:**
- Consumes: `rt-models` CLI (Task 6), `python -m scripts.bench` (Tasks 15–16), golden-тест (Task 9).
- Produces: провізіоновані моделі в `~/.cache/rt-speech/models/rt/<alias>`; `bench-data/{asr,mt}`; `bench-results/cyborg/$DATE/smoke-*.json`.

- [ ] **Step 1: Завантажити всі моделі реєстру (~5.2 GB)**

Run: `nix develop .#bench-cpu -c python -m src.services.common.models_cli fetch whisper-small whisper-medium whisper-large-v3-turbo nllb-200-distilled-600M-ct2-int8 kokoro-82m-v1.0-onnx kokoro-82m-v1.0-torch`
Expected: 6 рядків `<alias>: ok /home/…/.cache/rt-speech/models/rt/<alias>`, exit 0.

- [ ] **Step 2: Повна перевірка цілісності**

Run: `nix develop .#bench-cpu -c python -m src.services.common.models_cli verify --full`
Expected: 6 рядків `<alias>: ok`, exit 0.

- [ ] **Step 3: Golden-токенізація NLLB тепер виконується**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_nllb_golden.py -q -rs`
Expected: `1 passed` (не skipped). Якщо FAIL — зупинитись: `source_tokens` не збігається з HF; виправити (Task 8) до продовження.

- [ ] **Step 4: Завантажити бенч-дані**

FLORES+ — gated (auto-approve): спершу прийняти умови на https://huggingface.co/datasets/openlanguagedata/flores_plus під своїм HF-акаунтом. Токен-файл має бути читабельним для поточного користувача (напр. `~/.cache/huggingface/token`).
Run: `nix develop .#bench-cpu -c python -m scripts.bench fetch-data --hf-token-file ~/.cache/huggingface/token --fleurs-limit 100 --flores-limit 200`
Expected: `bench-data/mt/flores_devtest.{ukr_Cyrl,pol_Latn,eng_Latn}.txt` по 200 рядків (`wc -l`), `bench-data/asr/uk/` і `bench-data/asr/pl/` по 100 пар `fleurs_NNNN.{wav,txt}` (FLEURS parquet ≈ 510 MB uk + 462 MB pl).
Якщо `KeyError: 'text'` у FLORES або `KeyError: 'audio'` у FLEURS — подивитись схему (`python -c "import pyarrow.parquet as pq; print(pq.read_schema('<path>'))"` / `head -1 <jsonl>`), виправити назви полів у `scripts/bench/datasets.py` і тест `test_fleurs_table_to_dir`, закомітити.

- [ ] **Step 5 (рекомендовано): Власні записи з мікрофона edge**

Для кожної мови ≥ 20 фраз 3–10 с у реальному середовищі:
```bash
mkdir -p bench-data/asr/uk bench-data/asr/pl
arecord -f S16_LE -r 16000 -c 1 -d 8 bench-data/asr/uk/own_001.wav
printf '%s\n' "Точний текст, який було сказано." > bench-data/asr/uk/own_001.txt
```
(Повторити з наступними номерами; `pl` аналогічно.)

- [ ] **Step 6: CPU-smoke трьох сервісів**

```bash
DATE=$(date +%F); OUT=bench-results/cyborg/$DATE
nix develop .#bench-cpu -c python -m scripts.bench mt --device cpu --threads 4 --out $OUT/smoke-mt-cpu.json --gate mt-cpu
nix develop .#bench-cpu -c python -m scripts.bench asr --device cpu --model whisper-small --threads 4 --limit 10 --out $OUT/smoke-asr-cpu.json
nix develop .#bench-cpu -c python -m scripts.bench tts --device cpu --threads 4 --out $OUT/smoke-tts-cpu.json
```
Expected: `gate mt-cpu: PASS`; у `smoke-mt-cpu.json` поле `samples.uk-en[*].hyp` — осмислені англійські речення (якщо там мовне сміття або chrF < 30 — помилка мовних кодів/токенізації, повернутись до Task 8); `smoke-asr-cpu.json` має `metrics.wer.uk` < 0.5; `smoke-tts-cpu.json` має `metrics.sanity_rate` = 1.0.

- [ ] **Step 7: Перевірити offline-режим (без мережі)**

Run: `nix develop .#bench-cpu -c unshare -rn python -m scripts.bench mt --device cpu --no-download --latency-samples 5 --out /tmp/rt-offline-mt.json`
Expected: завершується успішно (exit 0) без мережевого namespace — моделі беруться лише з `<RT_MODEL_DIR>`, `HF_HUB_OFFLINE=1`.

---

### Task 19: GPU-матриця на cyborg, системний тест і рішення за defaults

**Files:**
- Modify (лише якщо цього вимагає рішення): `src/services/tts/engines.py` (`DEFAULT_TTS_ENGINE`), `src/services/asr/whisper_engine.py` (`STREAMING_VAD`), `src/services/common/device.py` (`_DEFAULT_COMPUTE[("tts","cpu")]`, `_DEFAULT_MODEL[("asr","cuda")]`) і відповідні тести
- Modify: `docs/superpowers/specs/2026-10-06-model-optimization-design.md` (§4.6 cyborg, новий §11 "Виміряні результати")

**Interfaces:**
- Consumes: `matrix`, `system` підкоманди (Tasks 16–17), провізіоновані моделі (Task 18).
- Produces: `bench-results/cyborg/$DATE/summary.md`, `system.json`; оновлені defaults і spec.

- [ ] **Step 1: Перевірити CUDA у bench-gpu**

Run: `nix develop .#bench-gpu -c python -c "import ctranslate2, onnxruntime; print(ctranslate2.get_cuda_device_count(), sorted(ctranslate2.get_supported_compute_types('cuda')), onnxruntime.get_available_providers())"`
Expected: `1 [... 'int8_float16' ...] ['CUDAExecutionProvider', ..., 'CPUExecutionProvider']`.

- [ ] **Step 2: Переконатись, що llama не тримає модель (чисті виміри по сервісах)**

Run: `nvidia-smi --query-gpu=memory.used --format=csv,noheader`
Expected: < 500 MiB (llama завантажує модель ліниво; якщо більше — дочекатись простою або виміряти як є і зазначити в spec).

- [ ] **Step 3: Матриця `cyborg`**

Run: `DATE=$(date +%F); nix develop .#bench-gpu -c python -m scripts.bench matrix --profile cyborg --out-dir bench-results/cyborg/$DATE`
Expected: `bench-results/cyborg/$DATE/summary.md` з 15 рядками і `Overall: PASS`. Якщо `FAIL` — у JSON відповідного кейсу поле з `FAIL` у `gate.checks` (для матриці перерахувати: `python -m scripts.bench <cmd з matrix.yml> --gate <gate> --out /tmp/x.json`) і застосувати драбину spec §4.6.

- [ ] **Step 4: Завантажити модель llama і виміряти її пік**

```bash
curl -s -X POST "http://127.0.0.1:8080/models/load?model_name=Qwen3.5-4B.Q4_K_M.gguf"
curl -s http://127.0.0.1:8080/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"ping"}],"max_tokens":8}' > /dev/null
nvidia-smi --query-gpu=memory.used --format=csv,noheader
```
Expected: JSON-відповідь `/models/load` з `model_path`; `memory.used` ≈ 3.5–4.3 GiB (оцінка spec §4.6). Записати як `L`.

- [ ] **Step 5: Системний тест (llama завантажена + ASR + MT, 5 хв)**

Run: `DATE=$(date +%F); nix develop .#bench-gpu -c python -m scripts.bench system --duration 300 --out bench-results/cyborg/$DATE/system.json --gate system-cyborg`
Expected: `gate system-cyborg: PASS` (`resources.device_used_peak_mib` ≤ 7577, `metrics.error_count` = 0). Якщо FAIL — драбина spec §4.6: повторити з `--asr-model whisper-small`; якщо PASS — змінити default (Step 7d).

- [ ] **Step 6: Прийняти рішення за правилами spec (записати цифри)**

  a. **TTS engine:** порівняти `tts-onnx-cpu-fp32` і `tts-torch-cpu`: onnx лишається, якщо `rtf(onnx) ≤ rtf(torch)` і `roundtrip_wer(onnx) ≤ roundtrip_wer(torch) + 0.02`.
  b. **VAD silence:** серед `asr-turbo-cuda-vad{300,400,600}` обрати найменше `min_silence_duration_ms`, для якого `wer.uk` і `wer.pl` ≤ відповідних значень `vad600` + 0.01.
  c. **CT2 allocator cap:** вмикати (рекомендація для підпроєкту 2), якщо `mt-nllb-cuda-capped-alloc.resources.vram_peak_mib` ≤ `mt-nllb-cuda` − 100 і `latency.p95` гірший не більше ніж на 10 %.
  d. **TTS CPU precision:** `int8` стає default для cpu, якщо `rtf(tts-onnx-cpu-int8)` ≤ 0.8 × `rtf(fp32)` і `roundtrip_wer(int8)` ≤ `roundtrip_wer(fp32)` + 0.02.
  e. **ASR cuda model:** `whisper-large-v3-turbo` лишається, якщо `asr-turbo-cuda` і `system` пройшли; інакше — `whisper-small`.

- [ ] **Step 7: Застосувати рішення в коді (лише ті, що змінились)**

  a. Якщо TTS → torch: у `src/services/tts/engines.py` замінити `DEFAULT_TTS_ENGINE = "onnx"` на `DEFAULT_TTS_ENGINE = "torch"`; у `tests/services/test_rt_tts_engines.py` замінити `assert engines.DEFAULT_TTS_ENGINE == "onnx"` на `assert engines.DEFAULT_TTS_ENGINE == "torch"`.
  b. Якщо VAD silence ≠ 400 (напр. 300): у `src/services/asr/whisper_engine.py` замінити `STREAMING_VAD = VadProfile(0.5, 0.35, 250, 400, 200, 12.0)` на `STREAMING_VAD = VadProfile(0.5, 0.35, 250, 300, 200, 12.0)`; у `tests/services/test_rt_whisper_engine.py` у `test_vad_profiles` замінити `"min_silence_duration_ms": 400` на `"min_silence_duration_ms": 300`.
  c. Якщо TTS cpu → int8: у `src/services/common/device.py` замінити `("tts", "cpu"): "float32",` на `("tts", "cpu"): "int8",`; у `tests/services/test_rt_device.py` замінити `("tts", "cpu", "float32"),` на `("tts", "cpu", "int8"),`; у `tests/services/test_rt_tts_engines.py` замінити `(tmp_path, "cpu", "float32", 2)` на `(tmp_path, "cpu", "int8", 2)`.
  d. Якщо ASR cuda → small: у `src/services/common/device.py` замінити `("asr", "cuda"): "whisper-large-v3-turbo",` на `("asr", "cuda"): "whisper-small",`; у `tests/services/test_rt_device.py` замінити `("asr", "cuda", "whisper-large-v3-turbo"),` на `("asr", "cuda", "whisper-small"),`.

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_*.py tests/bench -q && nix develop -c pytest tests/services/test_svc_*.py -q`
Expected: PASS.

- [ ] **Step 8: Оновити spec**

У `docs/superpowers/specs/2026-10-06-model-optimization-design.md`:
- §4.6 таблиця cyborg: колонку "VRAM (оцінка)" перейменувати на "VRAM (виміряно $DATE)" і вписати `L` (Step 4), `asr-turbo-cuda.resources.vram_peak_mib`, `mt-nllb-cuda.resources.vram_peak_mib`, суму і `system.resources.device_used_peak_mib`;
- додати в кінець розділ `## 11. Виміряні результати` з копією таблиці `summary.md`, результатом `system` і рішеннями a–e (одним рядком кожне, з цифрами);
- якщо рішення змінили defaults — оновити §4.2/§4.3/§4.4/§4.8 відповідно;
- результат gate `system-cyborg` (PASS/FAIL, пік MiB, обрана ASR-модель) — це вхід для SP3 Task 10 Step 1 (розміщення `rt-mt-gpu` на cyborg; overview §7 C3).

- [ ] **Step 9: Commit**

```bash
git add docs/superpowers/specs/2026-10-06-model-optimization-design.md src/services tests/services
git commit -m "docs(models): record cyborg benchmark results and finalize defaults"
```

---

### Task 20: MT-гейти — джерело NLLB і втрата від квантизації

**Files:**
- Create: `scripts/model_tools/nllb_hf_reference.py`
- Modify: `docs/superpowers/specs/2026-10-06-model-optimization-design.md` (§11)

**Interfaces:**
- Consumes: `convert_nllb_ct2` (Task 9), `load_mt_pair`, `chrf` (Tasks 13, 15), `to_nllb_code` (Task 7).
- Produces: `bench-results/cyborg/$DATE/mt-{own,pinned}-cpu.json`, `mt-hf-fp32.json`; висновок gate'ів у spec.

- [ ] **Step 1: Створити `scripts/model_tools/nllb_hf_reference.py`**

```python
# scripts/model_tools/nllb_hf_reference.py
"""HF transformers fp32 NLLB reference chrF for the quantization gate (spec §6.4).

Run in the model-tools shell (torch CPU + transformers; downloads ~2.5 GB):
  nix develop .#model-tools -c python -m scripts.model_tools.nllb_hf_reference --out FILE
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.bench.datasets import load_mt_pair
from scripts.bench.metrics import chrf
from scripts.model_tools.convert_nllb_ct2 import MODEL, REVISION
from src.services.mt.nllb_codes import to_nllb_code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="bench-data/mt")
    p.add_argument("--pairs", default="uk-en,pl-en")
    p.add_argument("--beams", type=int, default=4)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    local = snapshot_download(MODEL, revision=REVISION)
    model = AutoModelForSeq2SeqLM.from_pretrained(local).eval()
    scores = {}
    for pair in args.pairs.split(","):
        src, tgt = pair.split("-")
        sources, references = load_mt_pair(Path(args.data), pair)
        tokenizer = AutoTokenizer.from_pretrained(local, src_lang=to_nllb_code(src))
        target_id = tokenizer.convert_tokens_to_ids(to_nllb_code(tgt))
        hypotheses = []
        for start in range(0, len(sources), args.batch_size):
            batch = tokenizer(sources[start:start + args.batch_size], return_tensors="pt",
                              padding=True, truncation=True, max_length=512)
            with torch.no_grad():
                generated = model.generate(**batch, forced_bos_token_id=target_id,
                                           num_beams=args.beams, max_new_tokens=256,
                                           repetition_penalty=1.1)
            hypotheses += tokenizer.batch_decode(generated, skip_special_tokens=True)
        scores[pair] = chrf(hypotheses, references)
        print(f"{pair}: chrF {scores[pair]:.2f}", flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"kind": "mt-hf-reference", "config": {"model": MODEL,
                                                                     "revision": REVISION,
                                                                     "num_beams": args.beams},
                               "metrics": {"chrf": scores}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Власна конвертація**

Run: `nix develop .#model-tools -c python -m scripts.model_tools.convert_nllb_ct2 --out $HOME/.cache/rt-speech/models/own/nllb-600m-int8-own`
Expected: JSON з `"quantization": "int8"`, `"converter": "ctranslate2 4.8.0"`; у теці є `model.bin` (~620 MB), `shared_vocabulary.json`, `sentencepiece.bpe.model`, `.rt-manifest.json`.
Якщо конвертер падає через несумісність із transformers 5.x — записати текст помилки в spec §11 як "source gate: NOT RUN (converter incompatible)", перейти до Step 5.

- [ ] **Step 3: Виміряти обидва джерела однаково (CPU, beam 4)**

```bash
DATE=$(date +%F); OUT=bench-results/cyborg/$DATE
nix develop .#bench-cpu -c python -m scripts.bench mt --device cpu --threads 8 --beams 4 --latency-samples 5 \
  --model $HOME/.cache/rt-speech/models/own/nllb-600m-int8-own --out $OUT/mt-own-cpu.json
nix develop .#bench-cpu -c python -m scripts.bench mt --device cpu --threads 8 --beams 4 --latency-samples 5 \
  --model nllb-200-distilled-600M-ct2-int8 --out $OUT/mt-pinned-cpu.json
```
Expected: обидва файли створені.

- [ ] **Step 4: Source gate (|ΔchrF| ≤ 0.5)**

Run: `DATE=$(date +%F); OUT=bench-results/cyborg/$DATE; nix develop .#bench-cpu -c python -c "import json,sys; a=json.load(open('$OUT/mt-own-cpu.json'))['metrics']['chrf']; b=json.load(open('$OUT/mt-pinned-cpu.json'))['metrics']['chrf']; d={k: round(abs(a[k]-b[k]),2) for k in a}; print(d, 'PASS' if max(d.values())<=0.5 else 'FAIL')"`
Expected: `PASS`. Якщо `FAIL` — default MT-джерело переходить на власну конвертацію: у spec §4.7 і §11 записати, що на хостах `RT_MODEL=<абсолютний шлях до own-конвертації>` (rsync теки разом з `.rt-manifest.json`), і підняти OQ5 (приватне HF-дзеркало) до власника.

- [ ] **Step 5: HF fp32 reference і quantization gate**

```bash
DATE=$(date +%F); OUT=bench-results/cyborg/$DATE
nix develop .#model-tools -c python -m scripts.model_tools.nllb_hf_reference --beams 4 --out $OUT/mt-hf-fp32.json
nix develop .#bench-cpu -c python -c "import json; h=json.load(open('$OUT/mt-hf-fp32.json'))['metrics']['chrf']; c=json.load(open('$OUT/mt-pinned-cpu.json'))['metrics']['chrf']; ok=all(c[k]>=h[k]-1.0 for k in h); print({k:(round(h[k],2),round(c[k],2)) for k in h}, 'PASS' if ok else 'FAIL')"
```
Expected: `PASS` (CT2 int8 втрачає ≤ 1.0 chrF). Якщо `FAIL` — записати в spec §11 і повторити `mt-pinned` з `--compute-type int8_float32` (CPU) / `float16` (GPU) для оцінки; рішення про compute type — за власником з цими цифрами.

- [ ] **Step 6: Записати результати в spec §11 і закомітити**

```bash
git add scripts/model_tools/nllb_hf_reference.py docs/superpowers/specs/2026-10-06-model-optimization-design.md
git commit -m "feat(models): add NLLB HF reference script and record MT source/quantization gates"
```

---

### Task 21: Виміри на SKTOP-NOTE17-NIXOS, SKTOP-NOTE27-NIXOS і CPU-боксі

**Files:**
- Modify: `docs/superpowers/specs/2026-10-06-model-optimization-design.md` (§4.6 NOTE17/NOTE27/CPU, §11)

**Interfaces:**
- Consumes: профілі `note17`, `cyborg`, `cpu-box` (Task 17); моделі з Task 18.
- Produces: виміряні таблиці бюджету для кожного хоста; рішення для NOTE27 за правилом `F = V − L − 0.6 GiB`.

- [ ] **Step 1: Синхронізувати репо, моделі й дані на NOTE17**

```bash
H=SKTOP-NOTE17-NIXOS
rsync -a --delete --exclude bench-results ./ $H:rt-bench/
ssh $H 'mkdir -p ~/.cache/rt-speech/models/rt'
rsync -a ~/.cache/rt-speech/models/rt/{whisper-small,whisper-medium,nllb-200-distilled-600M-ct2-int8,kokoro-82m-v1.0-onnx} $H:.cache/rt-speech/models/rt/
ssh $H 'cd rt-bench && nix develop .#bench-cpu -c python -m src.services.common.models_cli verify --full whisper-small whisper-medium nllb-200-distilled-600M-ct2-int8 kokoro-82m-v1.0-onnx'
```
Expected: 4 рядки `ok`.

- [ ] **Step 2: Матриця `note17` (llama у простої; зафіксувати `uptime`)**

Run: `H=SKTOP-NOTE17-NIXOS; ssh $H 'uptime; cd rt-bench && nix develop .#bench-cpu -c python -m scripts.bench matrix --profile note17 --out-dir bench-results/note17/'"$(date +%F)"`
Expected: `summary.md`, `Overall: PASS`. Потім `rsync -a SKTOP-NOTE17-NIXOS:rt-bench/bench-results/note17 bench-results/`.
Якщо `asr-small-cpu4` не проходить RTF — записати і запропонувати `RT_NUM_THREADS=6` для ASR (OQ4: конкуренція за CPU).

- [ ] **Step 3: NOTE27 — визначити `V`, `L`, RAM**

```bash
H=SKTOP-NOTE27-NIXOS
ssh $H 'nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv; free -m; curl -s http://127.0.0.1:8080/models'
```
Завантажити модель llama з переліку `/models` (`curl -s -X POST "http://127.0.0.1:8080/models/load?model_name=<name>"` + chat-запит як у Task 19 Step 4) і знову `nvidia-smi` → `L`.
Expected: відомі `V` (MiB), `L` (MiB), вільна RAM. Обчислити `F = V − L − 614` і обрати рядок таблиці spec §4.6 NOTE27.

- [ ] **Step 4: NOTE27 — виміри за обраним рядком**

- Якщо `F ≥ 1024` і на хості є `cache.nixos-cuda.org` (`ssh SKTOP-NOTE27-NIXOS nix config show substituters`): синхронізувати як у Step 1 (плюс `whisper-large-v3-turbo`) і запустити `matrix --profile cyborg` у `.#bench-gpu`.
- Інакше: `matrix --profile note17` у `.#bench-cpu` (лише CPU) і перевірити, що пікові RSS ASR+MT+TTS разом вміщаються у вільну RAM (OQ2).
Expected: `summary.md` для NOTE27; рядок у spec з виміряними значеннями.

- [ ] **Step 5 (опційно): CPU-бокс**

Run (sktop-nixos-top, після синхронізації як у Step 1): `ssh sktop-nixos-top 'cd rt-bench && nix develop .#bench-cpu -c python -m scripts.bench matrix --profile cpu-box --out-dir bench-results/cpu-box/'"$(date +%F)"`
Expected: `summary.md` (без gate'ів); записати RTF/латентність як підтвердження "batch-only".

- [ ] **Step 6: Оновити spec §4.6 (NOTE17, NOTE27, CPU) і §11; закомітити**

```bash
git add docs/superpowers/specs/2026-10-06-model-optimization-design.md
git commit -m "docs(models): record VRAM/CPU budgets measured on NOTE17, NOTE27 and CPU box"
```

---

### Task 22 (опційно, time-box 4 год на апаратну перевірку): ROCm-spike CTranslate2 на gfx900

Виконувати лише якщо власник хоче GPU-прискорення на NOTE17 (spec §3.3 R1). Результат — GO/NO-GO у spec, а не продакшн-код. Збірка CT2 з ROCm — штатна опція nixpkgs (`rocmSupport`, nixpkgs ≥ `7a0f122` після SP2 Task 0, overview §7 C1); ризик spike'у — **залізо** (gfx90c офіційно не підтримується ROCm → `HSA_OVERRIDE_GFX_VERSION=9.0.0`, відомі memory faults), не пакування.

**Files:**
- Create: `scripts/bench/nix/ctranslate2-rocm.nix`
- Modify: `docs/superpowers/specs/2026-10-06-model-optimization-design.md` (D1, §11)

**Interfaces:**
- Consumes: `python -m scripts.bench asr --device rocm --gpu-probe rocm` (Task 16), `RocmSysfsProbe` (Task 14).
- Produces: GO/NO-GO рішення для `rt-asr-rocm`/`rt-mt-rocm` (їх пакування вже є в SP2 `variant-pkgs.nix` — той самий override).

- [ ] **Step 1: Створити `scripts/bench/nix/ctranslate2-rocm.nix`**

```nix
# EXPERIMENTAL spike (spec §3.3 R1): CTranslate2 with ROCm for gfx900
# (Vega 8 iGPU under HSA_OVERRIDE_GFX_VERSION=9.0.0). Uses nixpkgs' own
# rocmSupport switch — the same override as SP2 nix/packages/variant-pkgs.nix.
# Not wired into the flake.
{ pkgs, gpuTargets ? [ "gfx900" ] }:
let
  ct2Rocm = pkgs.ctranslate2.override { rocmSupport = true; rocmGpuTargets = gpuTargets; };
  python = pkgs.python313.override {
    self = python;
    packageOverrides = self: super: {
      ctranslate2 = super.ctranslate2.override { ctranslate2-cpp = ct2Rocm; };
    };
  };
in
python.withPackages (ps: with ps; [
  ctranslate2 faster-whisper sentencepiece huggingface-hub numpy soundfile pyyaml psutil
  jiwer sacrebleu pytest
])
```

- [ ] **Step 2: Зібрати на NOTE17** (репо на хості — `git clone` з GitHub, як усюди; overview §4.5a)

Run: `ssh SKTOP-NOTE17-NIXOS 'cd rt-bench && git pull --ff-only && nix build --impure -o result-ct2-rocm --expr "let pkgs = import (builtins.getFlake (toString ./.)).inputs.nixpkgs { system = \"x86_64-linux\"; config.allowUnfree = true; }; in import ./scripts/bench/nix/ctranslate2-rocm.nix { inherit pkgs; }"'`
Expected: `result-ct2-rocm/bin/python` існує. Якщо збірка nixpkgs-пакета падає — NO-GO (Step 5) з текстом помилки (і та сама помилка позначить SP2 `rt-*-rocm` як `meta.broken`, SP2 Task 5 Step 5).

- [ ] **Step 3: Перевірити пристрій**

Run: `ssh SKTOP-NOTE17-NIXOS 'cd rt-bench && HSA_OVERRIDE_GFX_VERSION=9.0.0 ./result-ct2-rocm/bin/python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count(), sorted(ctranslate2.get_supported_compute_types(\"cuda\")))"'`
Expected: `1 [...]` (підтверджує припущення A5 про device-рядок `cuda`).

- [ ] **Step 4: Бенч і 30-хв soak**

```bash
ssh SKTOP-NOTE17-NIXOS 'cd rt-bench && export HSA_OVERRIDE_GFX_VERSION=9.0.0 PYTHONPATH=$PWD && \
  ./result-ct2-rocm/bin/python -m scripts.bench asr --device rocm --model whisper-small --compute-type int8_float16 --gpu-probe rocm --out bench-results/note17/rocm-asr-small.json && \
  timeout 1800 bash -c "while ./result-ct2-rocm/bin/python -m scripts.bench asr --device rocm --model whisper-small --compute-type int8_float16 --gpu-probe rocm --limit 20 --out /tmp/soak.json; do :; done"; echo exit=$?'
```
Expected: перший бенч успішний; soak завершується через `timeout` з `exit=124` (а не раніше з core dump / "Memory access fault").

- [ ] **Step 5: Рішення і запис**

GO, якщо `rtf(rocm-asr-small) ≤ 0.66 × rtf(asr-small-cpu4 з note17)` і soak без faults; інакше NO-GO. Записати в spec D1 і §11 (цифри, текст помилок).

```bash
git add scripts/bench/nix/ctranslate2-rocm.nix docs/superpowers/specs/2026-10-06-model-optimization-design.md
git commit -m "docs(models): record CTranslate2 HIP gfx900 spike outcome"
```

---

### Task 23: Checkpoint ліцензії NLLB (OQ1)

**Files:**
- Modify: `docs/superpowers/specs/2026-10-06-model-optimization-design.md` (§9 OQ1)

- [ ] **Step 1: Отримати рішення власника**

Поставити власнику питання: "Чи використовуватимуться `rt-mt`/Django engine у комерційних цілях? NLLB-200 — CC-BY-NC-4.0." Зафіксувати відповідь у spec §9 OQ1.

- [ ] **Step 2: Діяти за відповіддю**

- "Некомерційне" → OQ1 закрито, NLLB лишається.
- "Комерційне" → **не** реалізовувати в межах цього плану: відкрити новий spec `docs/superpowers/specs/<date>-mt-opus-marian-design.md` для `Ct2MarianTranslator` (`opus-mt-tc-big-zle-en` + `-zlw-en`, CC-BY-4.0; маршрутизація за `source_lang`, `source.spm`/`target.spm`, без мовних токенів), з тим самим harness'ом для порівняння chrF/VRAM.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-10-06-model-optimization-design.md
git commit -m "docs(models): record NLLB license decision"
```

---

### Task 24: Завершення

**Files:**
- Modify: `docs/superpowers/specs/2026-10-06-model-optimization-design.md` (рядок `Статус:`)
- Modify: `plans/todo.md` (секція `## [2026-10-06] Розподілений деплой …`)

- [ ] **Step 1: Фінальний прогін тестів**

Run: `nix develop .#bench-cpu -c pytest tests/services/test_rt_*.py tests/bench -q && nix develop -c pytest tests/services/test_svc_*.py -q`
Expected: PASS (golden — passed, бо моделі провізіоновані).

- [ ] **Step 2: Оновити статус spec**

Замінити рядок `Статус: **draft, делеговано** …` на `Статус: **реалізовано і виміряно** (див. §11); defaults підтверджені harness'ом.`

- [ ] **Step 3: Оновити `plans/todo.md`**

У пункті `- [ ] 5. Model optimization …` замінити `- [ ]` на `- [x]` і дописати в кінець рядка ` — реалізовано; результати: spec §11`. Додати під ним рядок:
```markdown
  - [ ] Підпроєкт 2: врахувати рішення §11 (allocator env `CT2_CUDA_CACHING_ALLOCATOR_CONFIG` через `extraEnv`/defaults); §4.5 (scoped cudaSupport-інстанс, cache.nixos-cuda.org, без torch-bin) уже в SP2 `variant-pkgs.nix`
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-10-06-model-optimization-design.md plans/todo.md
git commit -m "docs: close sub-project 5 (model optimization & VRAM budget)"
```
