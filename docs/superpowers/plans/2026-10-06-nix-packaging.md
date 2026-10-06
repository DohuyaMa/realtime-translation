# Nix packaging + NixOS module + edge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перетворити цей репо на git-flake, що віддає deploy-rs-сумісні bundle `rt-{asr,mt,tts}-{cpu,gpu,rocm}`, overlay `pkgs.rtSpeech`, NixOS-модуль `services.rtSpeech.*`, `lib.rtSpeech.envLines` і edge-only Home Manager модуль, а edge-клієнт перевести з AF_UNIX IPC на HTTP/WS.

**Architecture:** Один flake без flake-utils; увесь Nix-код у `nix/`; одна функція `nix/packages/default.nix { pkgs }` спільна для `packages`, `overlays.default`, NixOS- і HM-модулів (збірка завжди проти `pkgs` споживача). Сервіси — system-юніти в стилі `llama-service.nix` з `deployRsPath`/`EnvironmentFile`; edge (UI + capture + VAD + playback) ходить у сервіси через `httpx`/`websockets`, налаштування — env `RT_EDGE_*` > `config.yml` > дефолти.

**Tech Stack:** Nix flakes (nixpkgs-unstable, home-manager), `pkgs.testers.runNixOSTest`, `lib.fileset`, Python 3.13, httpx, websockets 16, PySide6, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-nix-packaging-design.md` (контракт — `docs/superpowers/specs/2026-10-06-distributed-speech-services-overview.md` §4).

## Global Constraints

- Nix-only: жодних `pip`, `venv`, `virtualenv`; усі залежності — з nixpkgs через flake.
- Git-flake: усе, що потрібно для збірки, **tracked у git**; нові файли `git add` **до** будь-якої `nix`-команди; жодних `/home/…`, `builtins.getEnv`, `builtins.currentSystem`, `builtins.currentTime`, `<nixpkgs>`, `--impure` у Nix-коді; моделі не в репо.
- Системи: тільки `x86_64-linux`.
- Імена/порти/шляхи: `rt-asr` 8091 `/var/lib/rt-asr`, `rt-mt` 8092 `/var/lib/rt-mt`, `rt-tts` 8093 `/var/lib/rt-tts`; deploy-rs symlink `<stateDir>/current`; bundle binary `$out/bin/rt-<svc>`; моделі `<stateDir>/models`, `HF_HOME=<stateDir>/models/hf`.
- Env сервісів: `RT_HOST`, `RT_PORT`, `RT_DEVICE` (`cpu|cuda|rocm`), `RT_MODEL`, `RT_COMPUTE_TYPE`, `RT_MODEL_DIR`, `RT_API_TOKEN_FILE`, `RT_LOG_LEVEL`, `RT_ASR_LANGUAGE`, `RT_ASR_BEAM_SIZE`, `RT_ASR_VAD`, `RT_MT_SOURCE_LANG`, `RT_MT_TARGET_LANG`, `RT_MT_NUM_BEAMS`, `RT_TTS_VOICE`, `RT_TTS_SPEED`, `RT_TTS_SAMPLE_RATE`.
- Env edge: `RT_EDGE_MODE`, `RT_EDGE_ASR_URL`, `RT_EDGE_MT_URL`, `RT_EDGE_TTS_URL`, `RT_EDGE_API_TOKEN_FILE`.
- Defaults: service `host = "127.0.0.1"`; `firewallInterfaces = [ "tailscale0" ]`; edge `mode = "transcribe"`, `asr = "http://127.0.0.1:8091"`.
- Секрети — лише шляхи-рядки (`nullOr str`), ніколи `path`/вміст у store.
- `rt-tts-rocm` не існує (spec D2: ORT ROCm/MIGraphX не перевірений; не через torch); `packages` не містить пакетів з `meta.broken = true`.
- GPU-варіанти (CUDA) будуються з **окремого scoped інстансу** nixpkgs споживача з `config.cudaSupport = true` і дефолтними `cudaCapabilities` (створюється всередині `nix/packages`, глобальний `pkgs` споживача не змінюється — infra R1); **ніколи** `torch → torch-bin` (SP5 F7/F8/D7). ROCm — `ctranslate2.override { rocmSupport = true; rocmGpuTargets = …; }` з nixpkgs (без власного `WITH_HIP` overrideAttrs). Substituter `cache.nixos-cuda.org` у `nixConfig` — лише підказка; на хостах його ставить інфра (SP3 Task 7a).
- nixpkgs цього репо запінений не старіше за ревізію інфри (`swarm-nix/system-conf/flake.lock`, nixpkgs `7a0f122…`), щоб локальні перевірки бачили ті самі опції (`ctranslate2.rocmSupport`) — Task 0.
- Інтерфейс для інфри (spec §15, R1–R7): overlay додає **лише** `rtSpeech` (+ `rtSpeech.lib` = `lib.rtSpeech`); GPU-override тільки всередині bundle; `envLines` завжди починається з `PYTHONUNBUFFERED, RT_HOST, RT_PORT, RT_DEVICE`; токен — `LoadCredential=api-token:<file>` + літеральний `RT_API_TOKEN_FILE=/run/credentials/rt-<svc>.service/api-token`; `openFirewall = false` ⇒ порт закритий; `/health` ≤ 10 с.
- HTTP API — рівно overview §4.3 (`/health`, `/status`, `/v1/audio/transcriptions`, `/v1/audio/stream`, `/v1/translate`, `/v1/audio/speech`).
- Комміти — **без** рядка `Co-Authored-By` (правило користувача в memory `feedback_no_coauthor`).
- Python-тести edge лежать у `tests/edge/` і називаються `test_edge_*.py` (уникнення конфлікту basename з легасі-тестами).

## Review Focus

1. Значення env з пробілом, `%` або `"` у `extraEnv`/моделі — юніт має отримати рівно це значення (Task 2: eval-тест на `quoteEnv`; Task 3: VM перевіряє `"hello world"`).
2. Endpoint URL з кінцевим `/` або з префіксом шляху (reverse proxy `http://h/speech/asr`) — запити мають іти на `<prefix>/v1/…`, а не губити префікс (Task 7: `test_base_url_path_prefix_preserved`).
3. `source_lang = auto`, а ASR не повернув `language`; або source == target — не можна слати в MT `"auto"`/зайвий запит (Task 9: `test_auto_without_detected_language_skips_mt`, `test_same_language_skips_mt_and_speaks_original`).
4. Token-файл з `\n` у кінці / порожній / відсутній — strip; порожній чи відсутній → без заголовка + warning, а не краш (Task 7: `test_bearer_token_from_file_is_stripped`, `test_missing_or_empty_token_file_means_no_header`).
5. `mode: translate` у `config.yml` без MT/TTS URL (без HM-assertion) — `start_pipeline()` має повернути `False` з поясненням, а не падати посеред пайплайна (Task 10: `test_start_pipeline_rejects_invalid_settings`). Також: TTS віддав не-PCM16 WAV → `ServiceError`, не виняток у playback-потоці (Task 7: `test_tts_rejects_non_pcm16_wav`).

## File map

| Файл | Відповідальність | Task |
|---|---|---|
| `flake.lock` | bump nixpkgs ≥ ревізії інфри (`ctranslate2.rocmSupport`) | 0 |
| `.gitignore` | анкеровані Python-патерни, `result*` | 1 |
| `flake.nix` | виходи flake | 1, 4, 12, 13, 15 |
| `nix/lib/default.nix` | `lib.rtSpeech` (`services`, `variantForDevice`, `envLines`, `mkEnvFile`, `healthCheckCommand`) | 1 |
| `nix/packages/en-core-web-sm.nix` | spaCy wheel для misaki | 1 |
| `nix/packages/edge-deps.nix` | список Python-deps edge (DRY для пакета/devShell/тестів) | 1 |
| `nix/devshell.nix` | devShell без pip/venv | 1 |
| `nix/nixos/virtual-sinks.nix` | перенесено з `nixosModules/` | 1 |
| `nix/checks/flake-purity.nix`, `nix/checks/env-lines.nix` | перевірки | 1 |
| `nix/nixos/rt-speech.nix` | `nixosModules.rt-speech` | 2 |
| `nix/checks/nixos-eval-gpu.nix` | eval GPU/ROCm/D2 | 2 |
| `nix/checks/fake-service.py`, `nix/checks/nixos-rt-speech.nix` | VM-тест | 3 |
| `nix/packages/{default,sources,deps,variant-pkgs,speech-bundle}.nix` | bundle (`variant-pkgs.nix`: набір пакетів на варіант — cpu / scoped CUDA-інстанс / ROCm override) | 4 |
| `nix/checks/overlay.nix` | overlay | 4 |
| `src/core/runtime.py`, `src/core/config.py`, `config/default.yml` | `EdgeSettings` | 6 |
| `src/clients/{__init__,base,asr,mt,tts}.py` | HTTP-клієнти | 7 |
| `src/clients/health.py` | `ServiceHealthMonitor` | 8 |
| `src/translation_system.py` | edge-пайплайн | 9 |
| `src/adapters/direct_adapter.py`, `src/adapters/__init__.py`, `src/controller/*.py`, `src/main.py` | адаптер/контролер | 10 |
| `src/ui/**` | UI | 11 |
| `nix/packages/edge.nix`, `nix/checks/edge-pytest.nix` | edge-пакет | 12 |
| `nix/home-manager/rt-translator.nix`, `nix/checks/hm-edge.nix` | HM edge | 13 |
| `src/clients/asr_stream.py` | WS (опційно) | 14 |
| видалення легасі | | 15 |
| `AGENT.md`, `CLAUDE.md`, `plans/todo.md` | документація | 16 |

## Залежність від підпроєкту 1

Порядок між планами — overview §6. Tasks 0–1 виконуються **першими, до підпроєкту 1** (підпроєкт 1 і 5 додають свої devShell-залежності вже в `nix/devshell.nix` / `nix/bench-shells.nix`). Tasks 0–3 і 6–13 від підпроєкту 1 **не** залежать. Task 4 (bundles) вимагає, щоб у git були `src/services/{asr,mt,tts}/__init__.py` і `__main__.py`; runtime-дефолти `rt-mt`/`rt-tts` (CT2-бекенд, ORT-рушій, реєстр моделей) з'являються лише після SP5 Task 12a — до того bundle `rt-mt` збирається, але без SP5 модель не завантажить (рантайм без `transformers`/`torch`). Task 15 видаляє `src/translate/` і `src/tts/` лише після того, як підпроєкт 1 переніс їхню логіку. `nix/devshell.nix` може паралельно редагувати і підпроєкт 1 — при конфлікті об'єднати списки пакетів.

---

### Task 0: Bump nixpkgs до ревізії інфри (≥ `7a0f122…`)

Навіщо: у поточному lock (`89570f2…`, `ctranslate2` 4.8.0) немає `ctranslate2.rocmSupport`; у nixpkgs інфри (`swarm-nix/system-conf/flake.lock` → nixpkgs `7a0f122f5090cf4c2ade2a13a0e229d4e19ba71f`, `ctranslate2` 4.8.2) він є (`rocmSupport ? config.rocmSupport`, `rocmGpuTargets`, hip-бібліотеки). Інфра однаково будує overlay проти **свого** nixpkgs (`inputs.nixpkgs.follows`), тож локальні перевірки мають дивитись на не старішу ревізію. Задача не залежить від інших і виконується першою (overview §6).

**Files:**
- Modify: `flake.lock`

- [ ] **Step 1: Зафіксувати ревізію інфри**

```bash
INFRA_REV=$(nix flake metadata --json ~/repos/swarm-nix/system-conf | jq -r '.locks.nodes.nixpkgs.locked.rev')
INFRA_TS=$(nix flake metadata --json ~/repos/swarm-nix/system-conf | jq -r '.locks.nodes.nixpkgs.locked.lastModified')
echo "$INFRA_REV $INFRA_TS"
```
Expected: `7a0f122f5090cf4c2ade2a13a0e229d4e19ba71f …` (або новіша, якщо інфру вже оновили).

- [ ] **Step 2: Оновити nixpkgs цього репо**

```bash
nix flake update nixpkgs
nix flake metadata --json | jq -r '.locks.nodes.nixpkgs.locked | "\(.rev) \(.lastModified)"'
```
Expected: `lastModified` ≥ `$INFRA_TS`. (Якщо unstable вже пішов далеко вперед і це ламає збірку — запінити рівно ревізію інфри: `nix flake lock --override-input nixpkgs github:NixOS/nixpkgs/$INFRA_REV` — це `github:`-ревізія в lock, а не `path:`, тож git-only правило overview §4.5a не порушується.)

- [ ] **Step 3: Перевірити потрібні опції**

```bash
nix eval --impure --expr 'let f = builtins.getFlake (toString ./.); p = import f.inputs.nixpkgs { system = "x86_64-linux"; }; in { ct2 = p.ctranslate2.version; rocm = (builtins.functionArgs p.ctranslate2.override) ? rocmSupport; ort = (builtins.functionArgs p.onnxruntime.override) ? cudaSupport; }'
```
Expected: `{ ct2 = "4.8.2"; ort = true; rocm = true; }` (версія — не нижча).

- [ ] **Step 4: Смоук поточного devShell і тестів**

```bash
nix develop -c python -c "import ctranslate2, faster_whisper; print(ctranslate2.__version__)"
nix develop -c pytest tests -q -x --co >/dev/null && echo collect-ok
```
Expected: версія CT2 друкується; `collect-ok`.

- [ ] **Step 5: Commit**

```bash
git add flake.lock
git commit -m "build(flake): bump nixpkgs to >= infra revision (ctranslate2 rocmSupport)"
```

---

### Task 1: Git-гігієна, каркас flake, `lib.rtSpeech.envLines`, devShell

**Files:**
- Modify: `.gitignore`, `flake.nix`, `flake.lock`
- Create: `nix/lib/default.nix`, `nix/packages/en-core-web-sm.nix`, `nix/packages/edge-deps.nix`, `nix/devshell.nix`, `nix/checks/flake-purity.nix`, `nix/checks/env-lines.nix`
- Move: `nixosModules/virtual-sinks.nix` → `nix/nixos/virtual-sinks.nix`
- Untrack: `result`

**Interfaces:**
- Produces: `rtLib = import ./nix/lib { inherit lib; }` з атрибутами `services.<svc>.{port,unit,stateDir}`, `variantForDevice`, `envLines :: service -> cfg -> [ "K=V" ]`, `mkEnvFile :: { pkgs, service, cfg } -> drv ($out/env)`, `healthCheckCommand :: service -> cfg -> str`. `cfg` — атрибути `services.rtSpeech.<svc>` (Task 2). `import ./nix/packages/en-core-web-sm.nix { python3Packages, fetchurl }`; `import ./nix/packages/edge-deps.nix` — функція `ps -> [ ... ]`.

- [ ] **Step 1: Анкерувати `.gitignore` і прибрати `result` з git**

У `.gitignore` замінити блок `# Python` (рядки з `build/` … `*.egg`) на:

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
/build/
/develop-eggs/
/dist/
/downloads/
/eggs/
.eggs/
/lib/
/lib64/
/sdist/
/var/
/wheels/
*.egg-info/
.installed.cfg
*.egg
```

Блок `# Virtual Environment` → `/venv/`, `/env/`, `/ENV/`; `data/` → `/data/`; рядок `result` → `result` і `result-*`.

```bash
git rm --cached result
git check-ignore -v nix/lib/default.nix || echo "OK: nix/lib not ignored"
```
Expected: `OK: nix/lib not ignored`.

- [ ] **Step 2: Написати падаючий тест `env-lines`**

`nix/checks/env-lines.nix`:

```nix
{ pkgs, rtLib }:
let
  lib = pkgs.lib;
  base = {
    host = "0.0.0.0";
    port = 8091;
    device = "cpu";
    modelDir = "/var/lib/rt-asr/models";
    logLevel = "info";
    model = null;
    computeType = null;
    rocmGfxVersion = "";
    apiTokenFile = null;
    extraEnv = { };
  };
  # beamSize/numBeams default to null: device-dependent defaults live in Python (SP5 §4.8).
  asr = base // { language = null; beamSize = null; vad = true; };
  mt = base // { port = 8092; modelDir = "/var/lib/rt-mt/models"; sourceLang = "uk"; targetLang = "en"; numBeams = null; };
  tts = (removeAttrs base [ "computeType" ]) // { port = 8093; modelDir = "/var/lib/rt-tts/models"; voice = "af_heart"; speed = 1.0; sampleRate = 24000; };
  common = port: dir: [
    "PYTHONUNBUFFERED=1"
    "RT_HOST=0.0.0.0"
    "RT_PORT=${toString port}"
    "RT_DEVICE=cpu"
    "RT_MODEL_DIR=${dir}"
    "HF_HOME=${dir}/hf"
    "RT_LOG_LEVEL=info"
  ];
  failsToEval = v: !(builtins.tryEval (builtins.deepSeq v true)).success;
  failures = lib.runTests {
    testAsrMinimal = {
      expr = rtLib.envLines "asr" asr;
      expected = common 8091 "/var/lib/rt-asr/models" ++ [ "RT_ASR_VAD=true" ];
    };
    testAsrFull = {
      expr = rtLib.envLines "asr" (asr // { model = "whisper-large-v3-turbo"; computeType = "int8_float16"; language = "uk"; beamSize = 5; vad = false; });
      expected = common 8091 "/var/lib/rt-asr/models" ++ [
        "RT_MODEL=whisper-large-v3-turbo"
        "RT_COMPUTE_TYPE=int8_float16"
        "RT_ASR_LANGUAGE=uk"
        "RT_ASR_BEAM_SIZE=5"
        "RT_ASR_VAD=false"
      ];
    };
    testMt = {
      expr = rtLib.envLines "mt" mt;
      expected = common 8092 "/var/lib/rt-mt/models" ++ [ "RT_MT_SOURCE_LANG=uk" "RT_MT_TARGET_LANG=en" ];
    };
    testMtBeamsWhenSet = {
      expr = builtins.filter (lib.hasPrefix "RT_MT_NUM_BEAMS") (rtLib.envLines "mt" (mt // { numBeams = 4; }));
      expected = [ "RT_MT_NUM_BEAMS=4" ];
    };
    testTtsHasNoComputeType = {
      expr = rtLib.envLines "tts" tts;
      expected = common 8093 "/var/lib/rt-tts/models" ++ [ "RT_TTS_VOICE=af_heart" "RT_TTS_SPEED=1.000000" "RT_TTS_SAMPLE_RATE=24000" ];
    };
    testRocmGfxOnlyOnRocm = {
      expr = builtins.filter (lib.hasPrefix "HSA_") (rtLib.envLines "mt" (mt // { device = "rocm"; rocmGfxVersion = "9.0.0"; }))
        ++ builtins.filter (lib.hasPrefix "HSA_") (rtLib.envLines "mt" (mt // { device = "cuda"; rocmGfxVersion = "9.0.0"; }));
      expected = [ "HSA_OVERRIDE_GFX_VERSION=9.0.0" ];
    };
    testExtraEnvSortedAndLast = {
      expr = lib.takeEnd 2 (rtLib.envLines "asr" (asr // { extraEnv = { ZED = "1"; ALPHA = "a b"; }; }));
      expected = [ "ALPHA=a b" "ZED=1" ];
    };
    testAlwaysHostPortDevice = {
      # infra R2: deploy-rs health check reads RT_PORT from the rendered env file
      expr = map (s: builtins.head (lib.splitString "=" s)) (lib.take 4 (rtLib.envLines "tts" tts));
      expected = [ "PYTHONUNBUFFERED" "RT_HOST" "RT_PORT" "RT_DEVICE" ];
    };
    testTokenPathLiteral = {
      # infra R3: literal credential path (systemd does not expand %d inside EnvironmentFile)
      expr = builtins.filter (lib.hasPrefix "RT_API_TOKEN_FILE") (rtLib.envLines "mt" (mt // { apiTokenFile = "/run/secrets/t"; }));
      expected = [ "RT_API_TOKEN_FILE=/run/credentials/rt-mt.service/api-token" ];
    };
    testNoTokenWithoutFile = {
      expr = builtins.any (lib.hasPrefix "RT_API_TOKEN_FILE") (rtLib.envLines "asr" asr);
      expected = false;
    };
    testNewlineRejected = { expr = failsToEval (rtLib.envLines "asr" (asr // { model = "a\nb"; })); expected = true; };
    testUnknownServiceRejected = { expr = failsToEval (rtLib.envLines "stt" asr); expected = true; };
    testVariantForDevice = { expr = rtLib.variantForDevice; expected = { cpu = "cpu"; cuda = "gpu"; rocm = "rocm"; }; };
    testHealthCheck = {
      expr = rtLib.healthCheckCommand "mt" mt;
      expected = "curl -sf http://127.0.0.1:8092/health > /dev/null";
    };
  };
in
pkgs.runCommand "rt-speech-env-lines-test" { } (
  if failures == [ ] then "touch $out"
  else ''
    echo "envLines test failures:"
    echo ${lib.escapeShellArg (builtins.toJSON failures)}
    exit 1
  ''
)
```

- [ ] **Step 3: `flake-purity` check**

`nix/checks/flake-purity.nix`:

```nix
{ pkgs, src }:
pkgs.runCommand "rt-flake-purity" { nativeBuildInputs = [ pkgs.gnugrep ]; } ''
  cd ${src}
  if grep -rnE '/home/|builtins\.getEnv|builtins\.currentSystem|builtins\.currentTime|<nixpkgs>|--impure' \
       flake.nix nix/ --include='*.nix' --exclude='flake-purity.nix'; then
    echo "machine-local or impure reference found in Nix sources (see above)"
    exit 1
  fi
  touch $out
''
```

- [ ] **Step 4: Спільні дрібні Nix-файли**

`nix/packages/en-core-web-sm.nix` (перенесено з `flake-global/parts/python/tts.nix`):

```nix
{ python3Packages, fetchurl }:
python3Packages.buildPythonPackage {
  pname = "en_core_web_sm";
  version = "3.8.0";
  format = "wheel";
  src = fetchurl {
    url = "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl";
    sha256 = "11gvl30zfa49rwkfnbm5yja7gwaxq37k8szdvvrvzm17nyfl4chr";
  };
  doCheck = false;
}
```

`nix/packages/edge-deps.nix`:

```nix
# Python dependencies of the edge app (UI + capture + VAD + playback + HTTP/WS clients).
# Single list shared by nix/packages/edge.nix, nix/devshell.nix and nix/checks/edge-pytest.nix.
ps: with ps; [
  pyside6
  sounddevice
  numpy
  scipy
  pysilero-vad
  pulsectl
  pyyaml
  python-dotenv
  loguru
  httpx
  websockets
]
```

`nix/devshell.nix`:

```nix
{ pkgs }:
let
  py = pkgs.python313;
  enCoreWebSm = pkgs.callPackage ./packages/en-core-web-sm.nix { python3Packages = py.pkgs; };
in
pkgs.mkShell {
  packages = [
    (py.withPackages (ps:
      (import ./packages/edge-deps.nix ps)
      ++ (with ps; [
        # speech services (cpu) — sub-projects 1/5 code runs from here in dev
        # (SP1 Task 1 / SP5 add their deps HERE, not in flake-global/)
        fastapi uvicorn pydantic python-multipart
        faster-whisper ctranslate2 sentencepiece huggingface-hub
        kokoro onnxruntime soundfile enCoreWebSm
        transformers   # SP1 legacy TransformersNllbBackend + slow tests; not in bundles
        # tests / tooling
        pytest pytest-asyncio pytest-timeout black flake8 mypy
      ])))
    pkgs.pipewire
    pkgs.pulseaudio
    pkgs.alsa-utils
    pkgs.espeak-ng
    pkgs.curl
    pkgs.jq
  ];
  shellHook = ''
    export HF_HOME="$HOME/.cache/huggingface"
    if command -v pactl >/dev/null && pactl info >/dev/null 2>&1; then
      pactl list sinks short | grep -q rt_virtual_input \
        || pactl load-module module-null-sink sink_name=rt_virtual_input sink_properties=device.description=RT-Virtual-Input >/dev/null || true
      pactl list sinks short | grep -q rt_virtual_output \
        || pactl load-module module-null-sink sink_name=rt_virtual_output sink_properties=device.description=RT-Virtual-Output >/dev/null || true
    fi
  '';
}
```

```bash
mkdir -p nix/nixos && git mv nixosModules/virtual-sinks.nix nix/nixos/virtual-sinks.nix
```

- [ ] **Step 5: `nix/lib/default.nix`**

```nix
# lib.rtSpeech — system-independent helpers shared by the NixOS module and the infra
# (swarm-nix renders per-host deploy-rs `env` files with mkEnvFile).
{ lib }:
let
  services = {
    asr = { port = 8091; unit = "rt-asr"; stateDir = "/var/lib/rt-asr"; };
    mt = { port = 8092; unit = "rt-mt"; stateDir = "/var/lib/rt-mt"; };
    tts = { port = 8093; unit = "rt-tts"; stateDir = "/var/lib/rt-tts"; };
  };

  boolStr = b: if b then "true" else "false";

  kv = name: value:
    let s = toString value; in
    if lib.hasInfix "\n" s
    then throw "rtSpeech.envLines: value of ${name} contains a newline"
    else "${name}=${s}";

  # Single source of truth: services.rtSpeech.<service> cfg -> raw "K=V" lines
  # (EnvironmentFile format). The NixOS module quotes them for Environment=.
  # Only paths, never secrets: the token itself arrives via the unit's LoadCredential,
  # RT_API_TOKEN_FILE is the literal credential path (no %d — EnvironmentFile content
  # is not specifier-expanded; infra R3).
  envLines = service: cfg:
    assert lib.assertMsg (services ? ${service}) "rtSpeech.envLines: unknown service '${service}'";
    [
      "PYTHONUNBUFFERED=1"
      (kv "RT_HOST" cfg.host)
      (kv "RT_PORT" cfg.port)
      (kv "RT_DEVICE" cfg.device)
      (kv "RT_MODEL_DIR" cfg.modelDir)
      (kv "HF_HOME" "${cfg.modelDir}/hf")
      (kv "RT_LOG_LEVEL" cfg.logLevel)
    ]
    ++ lib.optional (cfg.model != null) (kv "RT_MODEL" cfg.model)
    ++ lib.optional ((cfg.computeType or null) != null) (kv "RT_COMPUTE_TYPE" cfg.computeType)
    ++ lib.optional (cfg.device == "rocm" && cfg.rocmGfxVersion != "") (kv "HSA_OVERRIDE_GFX_VERSION" cfg.rocmGfxVersion)
    ++ lib.optionals (service == "asr") (
      lib.optional (cfg.language != null) (kv "RT_ASR_LANGUAGE" cfg.language)
      ++ lib.optional (cfg.beamSize != null) (kv "RT_ASR_BEAM_SIZE" cfg.beamSize)
      ++ [ (kv "RT_ASR_VAD" (boolStr cfg.vad)) ]
    )
    ++ lib.optionals (service == "mt") (
      [
        (kv "RT_MT_SOURCE_LANG" cfg.sourceLang)
        (kv "RT_MT_TARGET_LANG" cfg.targetLang)
      ]
      ++ lib.optional (cfg.numBeams != null) (kv "RT_MT_NUM_BEAMS" cfg.numBeams)
    )
    ++ lib.optionals (service == "tts") [
      (kv "RT_TTS_VOICE" cfg.voice)
      (kv "RT_TTS_SPEED" cfg.speed)
      (kv "RT_TTS_SAMPLE_RATE" cfg.sampleRate)
    ]
    ++ lib.optional ((cfg.apiTokenFile or null) != null)
      (kv "RT_API_TOKEN_FILE" "/run/credentials/${services.${service}.unit}.service/api-token")
    ++ lib.mapAttrsToList kv cfg.extraEnv;
in
{
  inherit services envLines;

  variantForDevice = { cpu = "cpu"; cuda = "gpu"; rocm = "rocm"; };

  mkEnvFile = { pkgs, service, cfg }:
    pkgs.writeTextDir "env" (lib.concatLines (envLines service cfg));

  healthCheckCommand = _service: cfg:
    "curl -sf http://127.0.0.1:${toString cfg.port}/health > /dev/null";
}
```

- [ ] **Step 6: Новий `flake.nix` (легасі-виходи тимчасово лишаються)**

```nix
{
  description = "Real-time speech translation: edge UI + distributed ASR/MT/TTS services";

  # HINT only: CUDA variants (rt-*-gpu) are substitutable from cache.nixos-cuda.org.
  # Untrusted users' flake nixConfig is ignored — CUDA hosts get it via system
  # nix.settings in the infra (SP3 Task 7a). Never relied upon for correctness.
  nixConfig = {
    extra-substituters = [ "https://cache.nixos-cuda.org" ];
    extra-trusted-public-keys = [ "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M=" ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, ... }:
    let
      lib = nixpkgs.lib;
      systems = [ "x86_64-linux" ];
      pkgsFor = system: import nixpkgs { inherit system; config.allowUnfree = true; };
      forAllSystems = f: lib.genAttrs systems (system: f (pkgsFor system));
      rtLib = import ./nix/lib { inherit lib; };
    in
    {
      lib.rtSpeech = rtLib;

      # LEGACY — replaced in Tasks 4/12/13, deleted in Task 15.
      packages = forAllSystems (pkgs: (import ./flake-global/prod/packages.nix { inherit pkgs lib; }).packages);
      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/translator-ui";
        };
      });
      homeManagerModules.rt-translator = import ./flake-global/home-manager-module.nix;

      nixosModules.virtual-sinks = import ./nix/nixos/virtual-sinks.nix;

      devShells = forAllSystems (pkgs: { default = import ./nix/devshell.nix { inherit pkgs; }; });

      checks = forAllSystems (pkgs: {
        flake-purity = import ./nix/checks/flake-purity.nix { inherit pkgs; src = self; };
        env-lines = import ./nix/checks/env-lines.nix { inherit pkgs rtLib; };
      });
    };
}
```

- [ ] **Step 7: Додати файли в git і прогнати перевірки**

```bash
git add .gitignore flake.nix nix/
nix flake lock            # прибирає flake-utils/systems з flake.lock
nix build .#checks.x86_64-linux.env-lines .#checks.x86_64-linux.flake-purity -L
nix flake check -L
nix develop -c python -c "import httpx, websockets, faster_whisper, kokoro; print('devshell ok')"
```
Expected: обидва checks зелені; `flake check` проходить; `devshell ok`. (Якщо `env-lines` червоний — JSON у лозі показує `expr`/`expected`; виправити `nix/lib/default.nix`, не тест.)

- [ ] **Step 8: Commit**

```bash
git add -A .gitignore flake.nix flake.lock nix/ nixosModules/
git commit -m "build(nix): single flake without flake-utils, lib.rtSpeech.envLines, purity checks"
```

---

### Task 2: `nixosModules.rt-speech` + eval-перевірка GPU/ROCm

**Files:**
- Create: `nix/nixos/rt-speech.nix`, `nix/checks/nixos-eval-gpu.nix`
- Modify: `flake.nix` (виходи `nixosModules.rt-speech`, `nixosModules.default`, `checks.nixos-eval-gpu`)

**Interfaces:**
- Consumes: `rtLib.services`, `rtLib.envLines`, `rtLib.variantForDevice` (Task 1); `import ../packages { inherit pkgs lib; }` — атрибути `<svc>.<variant>` (з'являться в Task 4; до того default `package` просто не обчислюється, бо всі тести задають `package`).
- Produces: опції `services.rtSpeech.<svc>.{enable, package, deployRsPath, host, port, device, model, modelDir, apiTokenFile, openFirewall, firewallInterfaces, logLevel, rocmGfxVersion, memoryDenyWriteExecute, extraEnv}`; asr: `computeType, language, beamSize, vad`; mt: `computeType, sourceLang, targetLang, numBeams`; tts: `voice, speed, sampleRate`. Юніти `systemd.services.rt-<svc>`, користувачі `rt-<svc>`.

- [ ] **Step 1: Написати падаючу eval-перевірку**

`nix/checks/nixos-eval-gpu.nix`:

```nix
{ pkgs, self, nixpkgs }:
let
  lib = pkgs.lib;
  fake = name: pkgs.writeShellScriptBin name "exit 0";
  evalWith = rtSpeech: (nixpkgs.lib.nixosSystem {
    modules = [
      self.nixosModules.rt-speech
      {
        nixpkgs.hostPlatform = "x86_64-linux";
        boot.isContainer = true;
        system.stateVersion = "26.05";
        services.rtSpeech = rtSpeech;
      }
    ];
  }).config;
  sc = config: unit: config.systemd.services.${unit}.serviceConfig;

  cpu = evalWith { asr = { enable = true; package = fake "rt-asr"; extraEnv.NOTE = ''say "hi" 100%''; apiTokenFile = "/run/secrets/rt"; }; };
  cuda = evalWith { asr = { enable = true; device = "cuda"; package = fake "rt-asr"; }; };
  rocm = evalWith { mt = { enable = true; device = "rocm"; rocmGfxVersion = "9.0.0"; package = fake "rt-mt"; }; };
  deployed = evalWith { mt = { enable = true; deployRsPath = "/var/lib/rt-mt/current"; }; };
  ttsCpuNoMdwe = evalWith { tts = { enable = true; package = fake "rt-tts"; }; };
  ttsRocm = evalWith { tts = { enable = true; device = "rocm"; }; };
  storeToken = evalWith { asr = { enable = true; package = fake "rt-asr"; apiTokenFile = "${builtins.storeDir}/x-token"; }; };
  firewall = evalWith { asr = { enable = true; package = fake "rt-asr"; host = "0.0.0.0"; openFirewall = true; }; };
  closed = evalWith { asr = { enable = true; package = fake "rt-asr"; host = "0.0.0.0"; }; };

  failedAssertions = config: map (a: a.message) (builtins.filter (a: !a.assertion) config.assertions);

  failures = lib.runTests {
    testCpuHardening = {
      expr = { inherit (sc cpu "rt-asr") User MemoryDenyWriteExecute PrivateDevices ProtectSystem StateDirectory DeviceAllow; };
      expected = { User = "rt-asr"; MemoryDenyWriteExecute = true; PrivateDevices = true; ProtectSystem = "strict"; StateDirectory = "rt-asr"; DeviceAllow = [ ]; };
    };
    testQuotedEnvironment = {
      expr = builtins.filter (lib.hasInfix "NOTE") (sc cpu "rt-asr").Environment;
      expected = [ ''"NOTE=say \"hi\" 100%%"'' ];
    };
    testTokenViaCredential = {
      expr = {
        inherit (sc cpu "rt-asr") LoadCredential;
        env = builtins.filter (lib.hasInfix "RT_API_TOKEN_FILE") (sc cpu "rt-asr").Environment;
      };
      expected = {
        LoadCredential = [ "api-token:/run/secrets/rt" ];
        env = [ ''"RT_API_TOKEN_FILE=/run/credentials/rt-asr.service/api-token"'' ];
      };
    };
    testCudaDevices = {
      expr = { inherit (sc cuda "rt-asr") DeviceAllow SupplementaryGroups MemoryDenyWriteExecute PrivateDevices; };
      expected = {
        DeviceAllow = [ "/dev/nvidiactl rw" "/dev/nvidia0 rw" "/dev/nvidia-modeset rw" "/dev/nvidia-uvm rw" "/dev/nvidia-uvm-tools rw" ];
        SupplementaryGroups = [ "video" "render" ];
        MemoryDenyWriteExecute = false;
        PrivateDevices = false;
      };
    };
    testRocmDevicesAndGfx = {
      expr = {
        inherit (sc rocm "rt-mt") DeviceAllow;
        gfx = builtins.elem ''"HSA_OVERRIDE_GFX_VERSION=9.0.0"'' (sc rocm "rt-mt").Environment;
      };
      expected = { DeviceAllow = [ "/dev/kfd rw" "char-drm rw" ]; gfx = true; };
    };
    testDeployRsPath = {
      expr = {
        inherit (sc deployed "rt-mt") ExecStart EnvironmentFile;
        cond = deployed.systemd.services.rt-mt.unitConfig.ConditionPathExists;
      };
      expected = {
        ExecStart = "/var/lib/rt-mt/current/bin/rt-mt";
        EnvironmentFile = [ "-/var/lib/rt-mt/current/env" ];
        cond = "/var/lib/rt-mt/current/bin/rt-mt";
      };
    };
    testTtsCpuAllowsJit = { expr = (sc ttsCpuNoMdwe "rt-tts").MemoryDenyWriteExecute; expected = false; };
    testTtsRocmRejected = {
      expr = (builtins.tryEval (builtins.deepSeq (sc ttsRocm "rt-tts").ExecStart true)).success;
      expected = false;
    };
    testStoreTokenRejected = {
      expr = failedAssertions storeToken;
      expected = [ "services.rtSpeech.asr.apiTokenFile must not point into the Nix store" ];
    };
    testFirewallOnlyOnTailscale = {
      expr = {
        iface = firewall.networking.firewall.interfaces.tailscale0.allowedTCPPorts;
        global = firewall.networking.firewall.allowedTCPPorts;
      };
      expected = { iface = [ 8091 ]; global = [ ]; };
    };
    testFirewallClosedByDefault = {
      # infra R5: openFirewall = false never opens the port, whatever host is
      expr = {
        global = closed.networking.firewall.allowedTCPPorts;
        ifaces = lib.concatMap (i: i.allowedTCPPorts or [ ]) (builtins.attrValues closed.networking.firewall.interfaces);
      };
      expected = { global = [ ]; ifaces = [ ]; };
    };
  };
in
pkgs.runCommand "rt-speech-nixos-eval-gpu" { } (
  if failures == [ ] then "touch $out"
  else ''
    echo "nixos module eval failures:"
    echo ${lib.escapeShellArg (builtins.toJSON failures)}
    exit 1
  ''
)
```

У `flake.nix` додати:

```nix
      nixosModules.rt-speech = import ./nix/nixos/rt-speech.nix;
      nixosModules.default = self.nixosModules.rt-speech;
```
і в `checks`:
```nix
        nixos-eval-gpu = import ./nix/checks/nixos-eval-gpu.nix { inherit pkgs self nixpkgs; };
```

- [ ] **Step 2: Переконатися, що падає**

```bash
git add nix/checks/nixos-eval-gpu.nix flake.nix
nix build .#checks.x86_64-linux.nixos-eval-gpu -L
```
Expected: FAIL — `path '…/nix/nixos/rt-speech.nix' does not exist`.

- [ ] **Step 3: Реалізувати модуль**

`nix/nixos/rt-speech.nix`:

```nix
# services.rtSpeech.{asr,mt,tts} — system units modeled on swarm-nix llama-service.nix.
# deploy-rs mode: deployRsPath = "/var/lib/rt-<svc>/current" (symlink managed by the
# infra's activation script), env overrides from "${deployRsPath}/env".
{ config, lib, pkgs, ... }:
let
  inherit (lib) mkOption mkEnableOption types;
  rtLib = import ../lib { inherit lib; };
  rtPkgs = import ../packages { inherit pkgs lib; };

  # Environment= needs quoting (spaces split assignments) and %-escaping (specifiers).
  quoteEnv = line: "\"" + lib.escape [ "\\" "\"" ] (lib.replaceStrings [ "%" ] [ "%%" ] line) + "\"";

  commonOptions = service: svc:
    let cfg = config.services.rtSpeech.${service}; in
    {
      enable = mkEnableOption "rt-${service} speech service";
      package = mkOption {
        type = types.package;
        default = rtPkgs.${service}.${rtLib.variantForDevice.${cfg.device}}
          or (throw "services.rtSpeech.${service}: no prebuilt bundle for device '${cfg.device}' (spec D2: rt-tts has no ROCm variant yet); set `package` or `deployRsPath`");
        defaultText = lib.literalExpression "rtSpeech.${service}.<variant matching device>";
        description = "Bundle providing bin/rt-${service}. Used only when deployRsPath is empty.";
      };
      deployRsPath = mkOption {
        type = types.str;
        default = "";
        example = "${svc.stateDir}/current";
        description = "deploy-rs managed bundle symlink. When set, ExecStart and EnvironmentFile use it and `package` is not built.";
      };
      host = mkOption { type = types.str; default = "127.0.0.1"; description = "Bind address (RT_HOST). Use 0.0.0.0 together with openFirewall for remote edges."; };
      port = mkOption { type = types.port; default = svc.port; description = "Listen port (RT_PORT)."; };
      device = mkOption { type = types.enum [ "cpu" "cuda" "rocm" ]; default = "cpu"; description = "Compute device (RT_DEVICE); also selects the bundle variant and GPU sandbox rules."; };
      model = mkOption { type = types.nullOr types.str; default = null; description = "Model id/path (RT_MODEL). null = service default."; };
      modelDir = mkOption { type = types.str; default = "${svc.stateDir}/models"; description = "Model cache (RT_MODEL_DIR; HF_HOME=<modelDir>/hf)."; };
      apiTokenFile = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "/run/secrets/rt-speech-token";
        description = "Bearer token file (outside the Nix store, may be root 0400). Passed via LoadCredential; RT_API_TOKEN_FILE=/run/credentials/rt-${service}.service/api-token.";
      };
      openFirewall = mkOption { type = types.bool; default = false; description = "Open port on firewallInterfaces only."; };
      firewallInterfaces = mkOption { type = types.listOf types.str; default = [ "tailscale0" ]; description = "Interfaces on which openFirewall opens the port."; };
      logLevel = mkOption { type = types.enum [ "debug" "info" "warning" "error" ]; default = "info"; description = "RT_LOG_LEVEL."; };
      rocmGfxVersion = mkOption { type = types.str; default = ""; example = "9.0.0"; description = "HSA_OVERRIDE_GFX_VERSION for device = rocm (empty = unset)."; };
      memoryDenyWriteExecute = mkOption {
        type = types.bool;
        default = cfg.device == "cpu" && service != "tts";
        defaultText = lib.literalExpression ''device == "cpu" && service != "tts"'';
        description = "systemd MemoryDenyWriteExecute. GPU runtimes and torch/oneDNN JIT need W+X memory.";
      };
      extraEnv = mkOption { type = types.attrsOf types.str; default = { }; description = "Extra environment, appended last."; };
    };

  computeTypeOption = mkOption { type = types.nullOr types.str; default = null; example = "int8_float16"; description = "RT_COMPUTE_TYPE. null = service default."; };

  specificOptions = {
    asr = {
      computeType = computeTypeOption;
      language = mkOption { type = types.nullOr types.str; default = null; description = "Default language (RT_ASR_LANGUAGE); null = auto-detect."; };
      beamSize = mkOption { type = types.nullOr types.ints.positive; default = null; description = "RT_ASR_BEAM_SIZE; null = service default (1 on cpu, 3 on cuda — sub-project 5)."; };
      vad = mkOption { type = types.bool; default = true; description = "RT_ASR_VAD."; };
    };
    mt = {
      computeType = computeTypeOption;
      sourceLang = mkOption { type = types.str; default = "uk"; description = "RT_MT_SOURCE_LANG (ISO 639-1)."; };
      targetLang = mkOption { type = types.str; default = "en"; description = "RT_MT_TARGET_LANG (ISO 639-1)."; };
      numBeams = mkOption { type = types.nullOr types.ints.positive; default = null; description = "RT_MT_NUM_BEAMS; null = service default (2 on cpu, 4 on cuda — sub-project 5)."; };
    };
    tts = {
      voice = mkOption { type = types.str; default = "af_heart"; description = "RT_TTS_VOICE."; };
      speed = mkOption { type = types.float; default = 1.0; description = "RT_TTS_SPEED."; };
      sampleRate = mkOption { type = types.ints.positive; default = 24000; description = "RT_TTS_SAMPLE_RATE."; };
    };
  };

  mkServiceConfig = service: svc:
    let
      cfg = config.services.rtSpeech.${service};
      unit = svc.unit;
      binRoot = if cfg.deployRsPath != "" then cfg.deployRsPath else cfg.package;
      isGpu = cfg.device != "cpu";
    in
    lib.mkIf cfg.enable {
      assertions = [{
        assertion = cfg.apiTokenFile == null || !(lib.hasPrefix builtins.storeDir cfg.apiTokenFile);
        message = "services.rtSpeech.${service}.apiTokenFile must not point into the Nix store";
      }];
      warnings = lib.optional (cfg.openFirewall && cfg.host == "127.0.0.1")
        "services.rtSpeech.${service}: openFirewall is set but host is 127.0.0.1, so nothing listens on ${lib.concatStringsSep ", " cfg.firewallInterfaces}";

      users.users.${unit} = { isSystemUser = true; group = unit; home = svc.stateDir; description = "rt-speech ${service} service"; };
      users.groups.${unit} = { };

      systemd.tmpfiles.rules = [ "d ${cfg.modelDir} 0750 ${unit} ${unit} - -" ];

      systemd.services.${unit} = {
        description = "rt-speech ${service} service";
        after = [ "network-online.target" ];
        wants = [ "network-online.target" ];
        wantedBy = [ "multi-user.target" ];
        unitConfig = {
          StartLimitIntervalSec = "5min";
          StartLimitBurst = 5;
        } // lib.optionalAttrs (cfg.deployRsPath != "") {
          # Before the first deploy-rs push the unit is skipped instead of restart-looping.
          ConditionPathExists = "${cfg.deployRsPath}/bin/rt-${service}";
        };
        serviceConfig = {
          Type = "simple";
          ExecStart = "${binRoot}/bin/rt-${service}";
          Environment = map quoteEnv (rtLib.envLines service cfg);
          # Later files override Environment= (re-rendered by the infra on every deploy-rs push).
          EnvironmentFile = lib.optional (cfg.deployRsPath != "") "-${cfg.deployRsPath}/env";
          LoadCredential = lib.optional (cfg.apiTokenFile != null) "api-token:${cfg.apiTokenFile}";

          User = unit;
          Group = unit;
          StateDirectory = unit;
          StateDirectoryMode = "0750";
          WorkingDirectory = svc.stateDir;
          ReadWritePaths = lib.optional (!(lib.hasPrefix "${svc.stateDir}/" cfg.modelDir)) cfg.modelDir;

          PrivateTmp = true;
          ProtectSystem = "strict";
          ProtectHome = true;
          NoNewPrivileges = true;
          ProtectKernelTunables = true;
          ProtectKernelModules = true;
          ProtectKernelLogs = true;
          ProtectControlGroups = true;
          ProtectClock = true;
          ProtectHostname = true;
          RestrictNamespaces = true;
          RestrictRealtime = true;
          RestrictSUIDSGID = true;
          LockPersonality = true;
          SystemCallArchitectures = "native";
          CapabilityBoundingSet = "";
          RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ];
          MemoryDenyWriteExecute = cfg.memoryDenyWriteExecute;
          PrivateDevices = !isGpu;
          SupplementaryGroups = lib.optionals isGpu [ "video" "render" ];
          DeviceAllow =
            lib.optionals (cfg.device == "cuda") [
              "/dev/nvidiactl rw"
              "/dev/nvidia0 rw"
              "/dev/nvidia-modeset rw"
              "/dev/nvidia-uvm rw"
              "/dev/nvidia-uvm-tools rw"
            ]
            ++ lib.optionals (cfg.device == "rocm") [ "/dev/kfd rw" "char-drm rw" ];

          LimitNOFILE = 65536; # no LimitNPROC: it counts threads of the user (CT2/torch pools)
          Restart = "on-failure";
          RestartSec = "5s";
          TimeoutStartSec = "60s";
          TimeoutStopSec = "15s";
          SyslogIdentifier = unit;
        };
      };

      networking.firewall.interfaces = lib.mkIf cfg.openFirewall
        (lib.genAttrs cfg.firewallInterfaces (_: { allowedTCPPorts = [ cfg.port ]; }));
    };
in
{
  options.services.rtSpeech = lib.mapAttrs
    (service: svc: commonOptions service svc // specificOptions.${service})
    rtLib.services;

  config = lib.mkMerge (lib.mapAttrsToList mkServiceConfig rtLib.services);
}
```

> Примітка: якщо Task 4 ще не виконано, файл `nix/packages/default.nix` не існує — створити тимчасовий `nix/packages/default.nix` з вмістом `{ pkgs, lib ? pkgs.lib }: { asr = { }; mt = { }; tts = { }; }` (Task 4 його перезапише). Тоді `ttsRocm` все одно дає `throw`, а явні `package` не чіпають цей файл.

- [ ] **Step 4: Прогнати перевірку**

```bash
git add nix/nixos/rt-speech.nix nix/packages/default.nix 2>/dev/null; git add nix/nixos/rt-speech.nix
nix build .#checks.x86_64-linux.nixos-eval-gpu -L
```
Expected: PASS (`rt-speech-nixos-eval-gpu` збудовано). При падінні JSON показує `expr`/`expected`.

- [ ] **Step 5: Commit**

```bash
git add flake.nix nix/
git commit -m "feat(nix): nixosModules.rt-speech with deployRsPath, LoadCredential token, GPU sandbox"
```

---

### Task 3: VM-тест модуля з fake-бекендом

**Files:**
- Create: `nix/checks/fake-service.py`, `nix/checks/nixos-rt-speech.nix`
- Modify: `flake.nix` (`checks.nixos-rt-speech`)

**Interfaces:**
- Consumes: `nixosModules.rt-speech` (Task 2).
- Produces: `checks.x86_64-linux.nixos-rt-speech`.

- [ ] **Step 1: Fake-сервіс (stdlib)**

`nix/checks/fake-service.py`:

```python
"""Stand-in for rt-asr/rt-mt/rt-tts in the NixOS VM test (stdlib only)."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_FILE = os.environ.get("RT_API_TOKEN_FILE")
TOKEN = open(TOKEN_FILE).read().strip() if TOKEN_FILE else None
SERVICE = os.path.basename(sys.argv[0])


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/status":
            env = {k: v for k, v in os.environ.items() if k.startswith("RT_")}
            return self._send(200, {"service": SERVICE, "ready": True, "env": env})
        if self.path.startswith("/v1/"):
            if TOKEN is not None and self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, {"pong": True})
        return self._send(404, {"error": "not found"})


ThreadingHTTPServer((os.environ.get("RT_HOST", "127.0.0.1"), int(os.environ["RT_PORT"])), Handler).serve_forever()
```

- [ ] **Step 2: VM-тест**

`nix/checks/nixos-rt-speech.nix`:

```nix
{ pkgs, self }:
let
  lib = pkgs.lib;
  fake = service: pkgs.writers.writePython3Bin "rt-${service}" { flakeIgnore = [ "E501" ]; }
    (builtins.readFile ./fake-service.py);
  fakes = lib.genAttrs [ "asr" "mt" "tts" ] fake;
  # What the infra's mkDeployProfiles would produce: bundle + per-host env file.
  mtDeployed = pkgs.symlinkJoin {
    name = "rt-mt-fake-deployed";
    paths = [ fakes.mt (pkgs.writeTextDir "env" "RT_MT_TARGET_LANG=de\n") ];
  };
in
pkgs.testers.runNixOSTest {
  name = "rt-speech";

  nodes.server = { ... }: {
    imports = [ self.nixosModules.rt-speech ];
    networking.firewall.enable = true;
    # root-only secret, like a sops-nix file (infra R3: one 0400 root file serves all system users)
    systemd.tmpfiles.rules = [ "f+ /run/secrets/rt-token 0400 root root - test-token" ];
    environment.systemPackages = [ pkgs.curl ];
    system.extraDependencies = [ mtDeployed ];

    services.rtSpeech.asr = {
      enable = true;
      package = fakes.asr;
      host = "0.0.0.0";
      openFirewall = true;
      firewallInterfaces = [ "eth1" ];
      apiTokenFile = "/run/secrets/rt-token";
      extraEnv.RT_FAKE_NOTE = "hello world";
    };
    services.rtSpeech.mt = {
      enable = true;
      deployRsPath = "/var/lib/rt-mt/current";
      host = "0.0.0.0";
      targetLang = "en";
    };
    services.rtSpeech.tts = {
      enable = true;
      package = fakes.tts;
      host = "0.0.0.0";
      openFirewall = true;
      firewallInterfaces = [ "eth9" ]; # not the test VLAN: must stay closed from the client
    };
  };

  nodes.client = { pkgs, ... }: {
    environment.systemPackages = [ pkgs.curl ];
  };

  testScript = ''
    start_all()
    server.wait_for_unit("multi-user.target")
    server.wait_for_unit("rt-asr.service")
    server.wait_for_unit("rt-tts.service")
    server.wait_for_open_port(8091)
    server.wait_for_open_port(8093)

    with subtest("static user and hardening"):
        server.succeed("systemctl show rt-asr -p User --value | grep -qx rt-asr")
        server.succeed("systemctl show rt-asr -p ProtectSystem --value | grep -qx strict")
        server.succeed("systemctl show rt-asr -p MemoryDenyWriteExecute --value | grep -qx yes")
        server.succeed("test \"$(stat -c %U /var/lib/rt-asr)\" = rt-asr")
        server.succeed("test -d /var/lib/rt-asr/models")

    with subtest("environment from the module, value with a space survives"):
        status = server.succeed("curl -sf http://127.0.0.1:8091/status")
        assert '"RT_PORT": "8091"' in status, status
        assert '"RT_FAKE_NOTE": "hello world"' in status, status
        assert '"HF_HOME": "/var/lib/rt-asr/models/hf"' in status, status
        assert '"RT_API_TOKEN_FILE": "/run/credentials/rt-asr.service/api-token"' in status, status

    with subtest("api token through LoadCredential"):
        server.fail("curl -sf http://127.0.0.1:8091/v1/ping")
        server.succeed("curl -sf -H 'Authorization: Bearer test-token' http://127.0.0.1:8091/v1/ping")
        server.succeed("curl -sf http://127.0.0.1:8091/health")
        server.succeed("test \"$(stat -c '%a %U' /run/secrets/rt-token)\" = '400 root'")

    with subtest("firewall opens only the configured interface"):
        client.wait_for_unit("multi-user.target")
        client.succeed("curl -sf --max-time 5 http://server:8091/health")
        client.fail("curl -sf --max-time 5 http://server:8093/health")

    with subtest("deploy-rs flow"):
        server.succeed("systemctl show rt-mt -p ConditionResult --value | grep -qx no")
        server.succeed("systemctl show rt-mt -p NRestarts --value | grep -qx 0")
        server.succeed("ln -sfn ${mtDeployed} /var/lib/rt-mt/current")
        server.succeed("systemctl restart rt-mt")
        server.wait_for_open_port(8092)
        status = server.succeed("curl -sf http://127.0.0.1:8092/status")
        assert '"RT_MT_TARGET_LANG": "de"' in status, status
  '';
}
```

У `flake.nix` → `checks`:
```nix
        nixos-rt-speech = import ./nix/checks/nixos-rt-speech.nix { inherit pkgs self; };
```

- [ ] **Step 3: Прогнати**

```bash
git add nix/checks/fake-service.py nix/checks/nixos-rt-speech.nix flake.nix
nix build .#checks.x86_64-linux.nixos-rt-speech -L
```
Expected: PASS (усі `subtest` зелені). Типові причини падіння: `ConditionResult` порожній, якщо юніт ще не намагався стартувати — перевірити, що `wait_for_unit("multi-user.target")` стоїть першим; інтерактивний дебаг — `nix run .#checks.x86_64-linux.nixos-rt-speech.driverInteractive`.

- [ ] **Step 4: Commit**

```bash
git commit -m "test(nix): NixOS VM test for rt-speech module (firewall, credential, deploy-rs flow)"
```

---

### Task 4: Speech bundles (cpu), overlay, `packages.rt-*`

**Передумова:** `git ls-files src/services/asr/__main__.py src/services/mt/__main__.py src/services/tts/__main__.py` повертає три рядки (підпроєкт 1). Якщо ні — **зупинитися** і повідомити: Task 4–5 заблоковані підпроєктом 1; продовжити з Task 6.

**Files:**
- Create: `nix/packages/sources.nix`, `nix/packages/deps.nix`, `nix/packages/variant-pkgs.nix`, `nix/packages/speech-bundle.nix`, `nix/checks/overlay.nix`
- Create/Replace: `nix/packages/default.nix`
- Modify: `flake.nix`

**Interfaces:**
- Consumes: `rtLib.services` (Task 1), `nix/packages/en-core-web-sm.nix` (Task 1).
- Produces: `import ./nix/packages { pkgs }` → `{ asr = { cpu; gpu; rocm; }; mt = { cpu; gpu; rocm; }; tts = { cpu; gpu; }; mkBundle; }`; кожен bundle: `$out/bin/rt-<svc>`, `$out/share/rt-speech/info.json`, `passthru.{service,variant,port,pythonEnv}`, `meta.mainProgram`. Flake: `packages.x86_64-linux.rt-<svc>-<variant>`, `overlays.default` (`pkgs.rtSpeech`).

- [ ] **Step 1: Падаюча перевірка overlay**

`nix/checks/overlay.nix`:

```nix
{ pkgs, self }:
let
  p = pkgs.extend self.overlays.default;
  info = drv: { inherit (drv.passthru) service variant port; main = drv.meta.mainProgram; };
in
assert info p.rtSpeech.asr.cpu == { service = "asr"; variant = "cpu"; port = 8091; main = "rt-asr"; };
assert info p.rtSpeech.mt.gpu == { service = "mt"; variant = "gpu"; port = 8092; main = "rt-mt"; };
assert info p.rtSpeech.tts.cpu == { service = "tts"; variant = "cpu"; port = 8093; main = "rt-tts"; };
assert !(p.rtSpeech.tts ? rocm);
assert p.rtSpeech.asr.cpu.drvPath == self.packages.x86_64-linux.rt-asr-cpu.drvPath;
# infra R1: overlay adds only `rtSpeech`; GPU overrides stay scoped (llama builds byte-identical)
assert builtins.attrNames (self.overlays.default p pkgs) == [ "rtSpeech" ];
assert p.python313.outPath == pkgs.python313.outPath;
assert p.python313Packages.ctranslate2.outPath == pkgs.python313Packages.ctranslate2.outPath;
assert p.ctranslate2.outPath == pkgs.ctranslate2.outPath;
assert builtins.elem "RT_PORT=8092" (p.rtSpeech.lib.envLines "mt" {
  host = "127.0.0.1"; port = 8092; device = "cpu"; modelDir = "/var/lib/rt-mt/models"; logLevel = "info";
  model = null; computeType = null; rocmGfxVersion = ""; apiTokenFile = null; extraEnv = { };
  sourceLang = "uk"; targetLang = "en"; numBeams = null;
});
pkgs.runCommand "rt-speech-overlay-check" { } "touch $out"
```

У `flake.nix` у `let` додати:

```nix
      rtPackagesFor = pkgs: import ./nix/packages { inherit pkgs; };
      flattenRt = rt: lib.filterAttrs (_: drv: !(drv.meta.broken or false)) (lib.listToAttrs (lib.concatMap
        (service: lib.mapAttrsToList (variant: drv: lib.nameValuePair "rt-${service}-${variant}" drv) rt.${service})
        [ "asr" "mt" "tts" ]));
```
виходи:
```nix
      overlays.default = final: _prev: { rtSpeech = import ./nix/packages { pkgs = final; }; };
      packages = forAllSystems (pkgs:
        (import ./flake-global/prod/packages.nix { inherit pkgs lib; }).packages   # LEGACY until Task 12
        // flattenRt (rtPackagesFor pkgs));
```
checks:
```nix
        overlay = import ./nix/checks/overlay.nix { inherit pkgs self; };
        rt-asr-cpu = (rtPackagesFor pkgs).asr.cpu;
        rt-mt-cpu = (rtPackagesFor pkgs).mt.cpu;
        rt-tts-cpu = (rtPackagesFor pkgs).tts.cpu;
```

```bash
git add nix/checks/overlay.nix flake.nix
nix build .#checks.x86_64-linux.overlay -L
```
Expected: FAIL (`attribute 'cpu' missing` або відсутній `speech-bundle.nix`).

- [ ] **Step 2: `sources.nix`, `deps.nix`, `variant-pkgs.nix`**

`nix/packages/sources.nix`:

```nix
# Source subsets (lib.fileset) so UI edits never rebuild/redeploy the speech services.
{ lib }:
let
  root = ../..;
in
{
  speechSrc = lib.fileset.toSource {
    inherit root;
    # Sub-project 1: add shared modules here if src/services imports them (import-check catches misses).
    fileset = lib.fileset.unions [ (root + "/src/__init__.py") (root + "/src/services") ];
  };
  edgeSrc = lib.fileset.toSource {
    inherit root;
    fileset = lib.fileset.unions [
      (root + "/pyproject.toml")
      (lib.fileset.difference (root + "/src") (lib.fileset.maybeMissing (root + "/src/services")))
    ];
  };
}
```

`nix/packages/deps.nix`:

```nix
# Python dependencies per speech service. Owned jointly with sub-projects 1, 4, 5:
# when src/services/<svc> imports something new, add it here (bundle import-check
# only catches module-level imports; heavy libs are imported lazily — keep this list
# in sync with SP1 §4.1/§10, SP4 (httpx worker client) and SP5 spec §4.5.4).
# `ps` comes from the variant's python (variant-pkgs.nix): CUDA/ROCm builds of
# ctranslate2/onnxruntime are already selected there — no per-package overrides here.
{ pkgs, service }:
ps:
let
  enCoreWebSm = pkgs.callPackage ./en-core-web-sm.nix { python3Packages = ps; };
  common = with ps; [
    fastapi uvicorn websockets pydantic python-multipart numpy loguru
    soundfile scipy        # SP1 common/audio.py (decode_audio_file, resample_poly)
    huggingface-hub        # SP5 model_store (rt-models fetch/verify)
    httpx                  # SP4 Django worker (runtime dependency, C5)
  ];
  perService = {
    asr = with ps; [ faster-whisper pysilero-vad ];          # SP1 server-side VAD
    mt = with ps; [ ctranslate2 sentencepiece ];             # SP5: NO transformers/torch
    tts = with ps; [ onnxruntime kokoro enCoreWebSm ];       # SP5 T4: ORT engine + misaki G2P via kokoro
  };
in
common ++ perService.${service}
```

`nix/packages/variant-pkgs.nix`:

```nix
# Package set per bundle variant (spec §5.5). The consumer's pkgs is NEVER modified
# (infra R1: llama drvPaths byte-identical) — GPU sets are created here, scoped to
# the bundle, from the consumer's own nixpkgs source (pkgs.path).
#  cpu  — consumer pkgs as is (everything from cache.nixos.org).
#  gpu  — separate instance with global cudaSupport and DEFAULT cudaCapabilities, so
#         ctranslate2/onnxruntime/torch hashes match https://cache.nixos-cuda.org
#         (SP5 F7). Never torch -> torch-bin (SP5 F8/D7). Fresh config on purpose:
#         re-feeding the evaluated pkgs.config would also leak unrelated settings.
#  rocm — consumer pkgs; C++ ctranslate2 via nixpkgs' own rocmSupport switch
#         (nixpkgs >= infra rev, Task 0), python ctranslate2/faster-whisper follow it
#         through a scoped packageOverrides. Experimental for HARDWARE reasons only
#         (gfx90c not officially supported -> HSA_OVERRIDE_GFX_VERSION).
{ pkgs, lib, variant, rocmGpuTargets }:
if variant == "cpu" then { inherit pkgs; python = pkgs.python313; }
else if variant == "gpu" then
  let
    cudaPkgs = import pkgs.path {
      system = pkgs.stdenv.hostPlatform.system;
      config = { allowUnfree = true; cudaSupport = true; };
    };
  in
  { pkgs = cudaPkgs; python = cudaPkgs.python313; }
else if variant == "rocm" then
  let
    ct2Rocm = pkgs.ctranslate2.override { rocmSupport = true; inherit rocmGpuTargets; };
    python = pkgs.python313.override {
      self = python;
      packageOverrides = _: super: {
        ctranslate2 = super.ctranslate2.override { ctranslate2-cpp = ct2Rocm; };
      };
    };
  in
  { inherit pkgs python; }
else throw "rtSpeech: unknown variant '${variant}'"
```

- [ ] **Step 3: `speech-bundle.nix` і `default.nix`**

`nix/packages/speech-bundle.nix`:

```nix
{ pkgs, lib }:
{ service, variant, rocmGpuTargets ? [ "gfx900" ] }:
let
  rtLib = import ../lib { inherit lib; };
  svc = rtLib.services.${service} or (throw "rtSpeech.mkBundle: unknown service '${service}'");
  sources = import ./sources.nix { inherit lib; };
  # cpu: consumer pkgs; gpu: scoped CUDA instance (cache.nixos-cuda.org); rocm: CT2 rocmSupport.
  vp = import ./variant-pkgs.nix { inherit pkgs lib variant rocmGpuTargets; };

  pythonEnv = vp.python.withPackages (import ./deps.nix { pkgs = vp.pkgs; inherit service; });
  module = "src.services.${service}";
  bin = "rt-${service}";
  wrapperArgs = [
    "--add-flags" "-m ${module}"
    "--set" "PYTHONPATH" "${sources.speechSrc}"
    "--set" "PYTHONNOUSERSITE" "1"
  ]
  ++ lib.optionals (variant == "gpu") [ "--prefix" "LD_LIBRARY_PATH" ":" "/run/opengl-driver/lib" ]
  ++ lib.optionals (service == "tts") [ "--prefix" "PATH" ":" "${pkgs.espeak-ng}/bin" ];
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "rt-${service}-${variant}";
  version = "0.1.0";
  dontUnpack = true;
  nativeBuildInputs = [ pkgs.makeWrapper ];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/share/rt-speech
    makeWrapper ${pythonEnv}/bin/python $out/bin/${bin} ${lib.escapeShellArgs wrapperArgs}
    # SP5 model provisioning CLI in every service bundle (spec §5.3; SP5 spec §4.5.5):
    # `rt-models fetch <alias>` as the service user before/after deploy. Module appears with SP5 Task 6.
    makeWrapper ${pythonEnv}/bin/python $out/bin/rt-models \
      --add-flags "-m src.services.common.models_cli" \
      --set PYTHONPATH ${sources.speechSrc} --set PYTHONNOUSERSITE 1
    echo '{"service": "${service}", "variant": "${variant}", "port": ${toString svc.port}}' > $out/share/rt-speech/info.json
    runHook postInstall
  '';

  # Same idea as llama-service's import validation: a missing dependency fails the build, not the deploy.
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    HOME=$TMPDIR PYTHONPATH=${sources.speechSrc} ${pythonEnv}/bin/python -c "import ${module}"
    runHook postInstallCheck
  '';

  passthru = { inherit service variant pythonEnv; inherit (svc) port; };
  meta = {
    description = "rt-speech ${service} service bundle (${variant})";
    mainProgram = bin;
    platforms = [ "x86_64-linux" ];
  };
}
```

`nix/packages/default.nix` (повна заміна; `edge` додасть Task 12):

```nix
{ pkgs, lib ? pkgs.lib }:
let
  mkBundle = import ./speech-bundle.nix { inherit pkgs lib; };
  variants = service: names: lib.genAttrs names (variant: mkBundle { inherit service variant; });
in
{
  asr = variants "asr" [ "cpu" "gpu" "rocm" ];
  mt = variants "mt" [ "cpu" "gpu" "rocm" ];
  tts = variants "tts" [ "cpu" "gpu" ]; # no rocm: ORT ROCm (MIGraphX EP) unverified (spec D2)
  inherit mkBundle;
  lib = import ../lib { inherit lib; }; # pkgs.rtSpeech.lib.envLines (infra R1, registry sees only { pkgs, lib })
}
```

- [ ] **Step 4: Збірка cpu і перевірки**

```bash
git add nix/packages/ flake.nix
nix build .#rt-asr-cpu .#rt-mt-cpu .#rt-tts-cpu -L
cat result/share/rt-speech/info.json
nix build .#checks.x86_64-linux.overlay -L
nix flake check -L
```
Expected: усі три bundle зібрані (лише наш код і `en_core_web_sm` будуються локально, решта — з `cache.nixos.org`); `info.json` = `{"service": "asr", "variant": "cpu", "port": 8091}`; overlay і `flake check` зелені.

- [ ] **Step 5: Smoke-запуск (контракт /health з підпроєкту 1)**

```bash
d=$(mktemp -d)
RT_PORT=18091 RT_MODEL_DIR=$d HF_HOME=$d/hf ./result/bin/rt-asr & pid=$!
ok=0; for i in $(seq 1 10); do curl -sf http://127.0.0.1:18091/health && { ok=1; break; }; sleep 1; done
kill $pid; test $ok = 1 && echo "health within ${i}s"
```
Expected: `{"status":"ok"}` і `health within Ns` з N ≤ 10 (infra R4: deploy-rs health check; важкі імпорти torch/ctranslate2 мають бути лінивими — це вимога до підпроєкту 1; якщо > 10 с — завести баг на підпроєкт 1, не «лікувати» тут). Повторити для `rt-mt` (порт 18092) і `rt-tts` (18093).

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(nix): rt-{asr,mt,tts} bundles with bin/rt-<svc>, overlays.default (pkgs.rtSpeech)"
```

---

### Task 5: GPU/ROCm варіанти — інстанціювання, CUDA-кеш, ROCm-збірка

**Files:**
- Modify (лише за потреби): `nix/packages/variant-pkgs.nix`, `nix/packages/speech-bundle.nix`
- Modify: `docs/superpowers/specs/2026-10-06-nix-packaging-design.md` (розділ «Open questions» — записати результати)

**Interfaces:**
- Consumes: Task 4 (`variant-pkgs.nix`), Task 0 (nixpkgs з `ctranslate2.rocmSupport`).
- Produces: підтверджені `drvPath` для `rt-{asr,mt}-{gpu,rocm}`, `rt-tts-gpu`; запис про покриття `cache.nixos-cuda.org` і ROCm-збірку.

- [ ] **Step 1: Інстанціювання всіх GPU-варіантів**

```bash
for p in rt-asr-gpu rt-mt-gpu rt-tts-gpu rt-asr-rocm rt-mt-rocm; do
  printf '%s ' $p; nix eval --raw .#packages.x86_64-linux.$p.drvPath; echo
done
nix eval .#packages.x86_64-linux --apply 'ps: builtins.hasAttr "rt-tts-rocm" ps'
```
Expected: п'ять `/nix/store/…drv`; останній рядок `false`.

- [ ] **Step 2: Жодного torch-bin і жодного впливу на глобальний pkgs**

```bash
nix-store -qR $(nix eval --raw .#packages.x86_64-linux.rt-tts-gpu.drvPath) | grep -E 'torch-bin' || echo "no torch-bin"
nix build .#checks.x86_64-linux.overlay -L
```
Expected: `no torch-bin`; overlay-check зелений (глобальні `python313`/`ctranslate2` незмінні — infra R1; CUDA-інстанс живе лише всередині bundle).

- [ ] **Step 3: Покриття `cache.nixos-cuda.org` (SP5 F7)**

```bash
for p in rt-asr-gpu rt-mt-gpu rt-tts-gpu; do
  env=$(nix eval --raw .#packages.x86_64-linux.$p.passthru.pythonEnv.drvPath)
  for d in $(nix-store -qR "$env" | grep -E 'ctranslate2|onnxruntime|torch-2|libnvshmem|nccl|cudnn' | grep '\.drv$'); do
    out=$(nix derivation show "$d" | jq -r '.[].outputs.out.path')
    nix path-info --store https://cache.nixos-cuda.org "$out" >/dev/null 2>&1 && echo "HIT  $p $out" || echo "MISS $p $out"
  done
done | sort -u
```
Expected: важкі CUDA-пакети (`ctranslate2`, `onnxruntime`, `torch`, `libnvshmem`, `nccl`) — `HIT` (дефолтні `cudaCapabilities`, без override). Записати результат у spec §13 п.1 (дата + nixpkgs rev з `flake.lock`). `MISS` на важкому пакеті → не «лікувати» override'ами (вони лише гарантують промах кешу): записати, GPU-хости тимчасово деплоять `-cpu`-варіант відповідного сервісу; перевірити повторно після наступного bump nixpkgs.

- [ ] **Step 4: (опційно) реальна збірка `rt-asr-gpu` з CUDA-кешем**

Потребує `cache.nixos-cuda.org` у системних `nix.settings` машини (SP3 Task 7a; для звичайного, не trusted користувача `nixConfig`/`--option` ігноруються).
```bash
nix config show substituters | grep -q nixos-cuda || echo "WARN: cache.nixos-cuda.org not configured — expect source builds"
nix build .#rt-asr-gpu -L --max-jobs 4
nix-store -q --references $(nix eval --raw .#packages.x86_64-linux.rt-asr-gpu.passthru.pythonEnv.outPath) | grep -c ctranslate2
```
Expected: збірка успішна (лише наш код і дрібні Python-похідні будуються локально), `ctranslate2` у замиканні ≥ 1. Пропустити, якщо немає GPU-хоста — не блокує план.

- [ ] **Step 5: ROCm-збірка (без time-box, бо це штатна опція nixpkgs)**

```bash
nix build .#rt-mt-rocm -L --max-jobs 4 2>&1 | tee /tmp/rt-mt-rocm.log | tail -40
```
- Успіх → записати в spec §13 п.3 «збирається (nixpkgs `rocmSupport`, `rocmGpuTargets = [ "gfx900" ]`); runtime — експериментально, перевірка на SKTOP-NOTE17-NIXOS у SP5 Task 22 / SP3 T3».
- Падіння збірки nixpkgs-пакета (не наш код) → у `nix/packages/speech-bundle.nix` додати в `meta` рядок `broken = variant == "rocm";` (з коментарем-посиланням на лог) (тоді `flattenRt` прибирає `rt-asr-rocm`/`rt-mt-rocm` з `packages`, `nix flake check` не падає), записати лог у spec §13 п.3 і перевірити знову після наступного bump nixpkgs. Власний `WITH_HIP` overrideAttrs **не** писати.
- Нагадування: ROCm-варіанти лишаються «experimental» з апаратних причин (gfx90c офіційно не підтримується ROCm → `rocmGfxVersion = "9.0.0"` → `HSA_OVERRIDE_GFX_VERSION`), не через пакування.

```bash
nix flake check -L
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add nix/packages/ docs/superpowers/specs/2026-10-06-nix-packaging-design.md
git commit -m "build(nix): verify GPU/ROCm bundle variants, record CUDA cache and ROCm findings"
```

---

### Task 6: `EdgeSettings` (runtime.py) і дефолти конфігу

**Files:**
- Rewrite: `src/core/runtime.py`
- Modify: `src/core/config.py`, `config/default.yml`
- Test: `tests/edge/test_edge_runtime_config.py`, `tests/edge/test_edge_config.py`

**Interfaces:**
- Produces:
  - `src.core.runtime`: `MODES = ("transcribe", "translate")`, `ASR_TRANSPORTS = ("http", "ws")`, `class EdgeSettingsError(ValueError)`, `@dataclass(frozen=True) class Timeouts(connect_s=3.0, asr_s=30.0, mt_s=15.0, tts_s=30.0)`, `@dataclass(frozen=True) class EdgeSettings(mode="transcribe", asr_url="http://127.0.0.1:8091", mt_url=None, tts_url=None, api_token_file=None, asr_transport="http", timeouts=Timeouts(), managed=frozenset())` з методами `validate() -> None`, `active_services() -> tuple[str, ...]`; `resolve_edge_settings(services_cfg: Mapping, environ: Mapping) -> EdgeSettings`; `get_runtime_config() -> EdgeSettings`; `reset_runtime_config() -> None`.
  - `ConfigManager.get_services_config() -> dict` (deep copy секції `services`).

- [ ] **Step 1: Падаючі тести**

`tests/edge/test_edge_runtime_config.py`:

```python
import pytest

from src.core.runtime import EdgeSettings, EdgeSettingsError, Timeouts, resolve_edge_settings


def test_defaults_are_transcribe_with_local_asr():
    s = resolve_edge_settings({}, {})
    assert s.mode == "transcribe"
    assert s.asr_url == "http://127.0.0.1:8091"
    assert s.mt_url is None and s.tts_url is None
    assert s.asr_transport == "http"
    assert s.timeouts == Timeouts()
    assert s.managed == frozenset()
    s.validate()


def test_config_values_used_when_env_absent():
    cfg = {
        "mode": "translate",
        "asr": {"url": "http://gpu:8091", "transport": "ws"},
        "mt": {"url": "http://gpu:8092"},
        "tts": {"url": "http://gpu:8093"},
        "api_token_file": "/run/user/1000/rt-token",
        "timeouts": {"connect_s": 1, "asr_s": 10, "mt_s": 5, "tts_s": 20},
    }
    s = resolve_edge_settings(cfg, {})
    assert (s.mode, s.asr_url, s.mt_url, s.tts_url) == (
        "translate", "http://gpu:8091", "http://gpu:8092", "http://gpu:8093")
    assert s.asr_transport == "ws"
    assert s.api_token_file == "/run/user/1000/rt-token"
    assert s.timeouts == Timeouts(connect_s=1.0, asr_s=10.0, mt_s=5.0, tts_s=20.0)
    s.validate()


def test_env_overrides_config_and_marks_managed():
    env = {
        "RT_EDGE_MODE": "translate",
        "RT_EDGE_ASR_URL": "http://a:1",
        "RT_EDGE_MT_URL": "http://m:2",
        "RT_EDGE_TTS_URL": "http://t:3",
        "RT_EDGE_API_TOKEN_FILE": "/tok",
    }
    s = resolve_edge_settings({"mode": "transcribe", "asr": {"url": "http://cfg:9"}}, env)
    assert (s.mode, s.asr_url, s.mt_url, s.tts_url, s.api_token_file) == (
        "translate", "http://a:1", "http://m:2", "http://t:3", "/tok")
    assert s.managed == frozenset({"mode", "asr_url", "mt_url", "tts_url", "api_token_file"})


def test_empty_env_value_does_not_override():
    s = resolve_edge_settings({"asr": {"url": "http://cfg:9"}}, {"RT_EDGE_ASR_URL": ""})
    assert s.asr_url == "http://cfg:9"
    assert "asr_url" not in s.managed


def test_blank_optional_values_become_none():
    s = resolve_edge_settings({"mt": {"url": ""}, "tts": {"url": None}, "api_token_file": ""}, {})
    assert s.mt_url is None and s.tts_url is None and s.api_token_file is None


def test_trailing_slash_stripped():
    s = resolve_edge_settings({"asr": {"url": "http://gpu:8091/"}}, {"RT_EDGE_MT_URL": "http://gpu:8092/"})
    assert s.asr_url == "http://gpu:8091"
    assert s.mt_url == "http://gpu:8092"


@pytest.mark.parametrize("missing,word", [("mt_url", "mt"), ("tts_url", "tts")])
def test_translate_requires_mt_and_tts(missing, word):
    kwargs = dict(mode="translate", mt_url="http://m", tts_url="http://t")
    kwargs[missing] = None
    with pytest.raises(EdgeSettingsError, match=word):
        EdgeSettings(**kwargs).validate()


def test_invalid_mode_transport_and_empty_asr_rejected():
    with pytest.raises(EdgeSettingsError):
        EdgeSettings(mode="dub").validate()
    with pytest.raises(EdgeSettingsError):
        EdgeSettings(asr_transport="grpc").validate()
    with pytest.raises(EdgeSettingsError):
        EdgeSettings(asr_url="").validate()


def test_active_services():
    assert EdgeSettings().active_services() == ("asr",)
    assert EdgeSettings(mode="translate", mt_url="x", tts_url="y").active_services() == ("asr", "mt", "tts")
```

`tests/edge/test_edge_config.py`:

```python
from src.core.config import ConfigManager


def test_services_defaults(tmp_path):
    cm = ConfigManager(str(tmp_path))
    svc = cm.get_services_config()
    assert svc["mode"] == "transcribe"
    assert svc["asr"] == {"url": "http://127.0.0.1:8091", "transport": "http"}
    assert svc["mt"] == {"url": None}
    assert svc["tts"] == {"url": None}
    assert svc["api_token_file"] is None
    assert cm.get("wyoming") is None
    assert cm.get("models") is None
    assert cm.get("speech.tts_voice") == "af_heart"


def test_get_services_config_returns_copy(tmp_path):
    cm = ConfigManager(str(tmp_path))
    cm.get_services_config()["asr"]["url"] = "http://mutated"
    assert cm.get_services_config()["asr"]["url"] == "http://127.0.0.1:8091"
    assert ConfigManager(str(tmp_path / "other")).get_services_config()["asr"]["url"] == "http://127.0.0.1:8091"


def test_legacy_keys_in_file_are_tolerated(tmp_path):
    (tmp_path / "config.yml").write_text("wyoming: {use_wyoming: true}\nservices: {mode: translate}\n")
    cm = ConfigManager(str(tmp_path))
    assert cm.get_services_config()["mode"] == "translate"
    assert cm.get_services_config()["asr"]["url"] == "http://127.0.0.1:8091"
```

```bash
mkdir -p tests/edge && git add tests/edge
nix develop -c pytest tests/edge -q
```
Expected: FAIL (`ImportError: cannot import name 'EdgeSettings'`).

- [ ] **Step 2: Переписати `src/core/runtime.py`**

```python
"""Edge runtime settings: which remote speech services to use and how to reach them.

Precedence: RT_EDGE_* environment (Home Manager) > config.yml `services` > defaults.
Values that come from the environment are reported in `EdgeSettings.managed` so the UI
can show them read-only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

MODES = ("transcribe", "translate")
ASR_TRANSPORTS = ("http", "ws")
DEFAULT_ASR_URL = "http://127.0.0.1:8091"

ENV_MODE = "RT_EDGE_MODE"
ENV_ASR_URL = "RT_EDGE_ASR_URL"
ENV_MT_URL = "RT_EDGE_MT_URL"
ENV_TTS_URL = "RT_EDGE_TTS_URL"
ENV_API_TOKEN_FILE = "RT_EDGE_API_TOKEN_FILE"


class EdgeSettingsError(ValueError):
    """The edge configuration cannot run the requested mode."""


@dataclass(frozen=True)
class Timeouts:
    connect_s: float = 3.0
    asr_s: float = 30.0
    mt_s: float = 15.0
    tts_s: float = 30.0


@dataclass(frozen=True)
class EdgeSettings:
    mode: str = "transcribe"
    asr_url: str = DEFAULT_ASR_URL
    mt_url: Optional[str] = None
    tts_url: Optional[str] = None
    api_token_file: Optional[str] = None
    asr_transport: str = "http"
    timeouts: Timeouts = field(default_factory=Timeouts)
    managed: frozenset = frozenset()

    def validate(self) -> None:
        if self.mode not in MODES:
            raise EdgeSettingsError(f"unknown mode {self.mode!r}; expected one of {MODES}")
        if self.asr_transport not in ASR_TRANSPORTS:
            raise EdgeSettingsError(f"unknown asr transport {self.asr_transport!r}; expected one of {ASR_TRANSPORTS}")
        if not self.asr_url:
            raise EdgeSettingsError("asr endpoint URL is empty")
        if self.mode == "translate":
            missing = [name for name, url in (("mt", self.mt_url), ("tts", self.tts_url)) if not url]
            if missing:
                raise EdgeSettingsError(
                    f"mode 'translate' requires {' and '.join(missing)} endpoint URL(s)")

    def active_services(self) -> tuple:
        return ("asr",) if self.mode == "transcribe" else ("asr", "mt", "tts")


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _url(value: Any) -> Optional[str]:
    text = _clean(value)
    return text.rstrip("/") if text else None


def resolve_edge_settings(services_cfg: Optional[Mapping[str, Any]], environ: Mapping[str, str]) -> EdgeSettings:
    cfg = dict(services_cfg or {})
    managed = set()

    def pick(env_key: str, name: str, cfg_value: Any) -> Any:
        if _clean(environ.get(env_key)):
            managed.add(name)
            return environ[env_key]
        return cfg_value

    asr_cfg = cfg.get("asr") or {}
    timeouts_cfg = cfg.get("timeouts") or {}
    defaults = Timeouts()
    return EdgeSettings(
        mode=str(pick(ENV_MODE, "mode", cfg.get("mode") or "transcribe")).strip(),
        asr_url=_url(pick(ENV_ASR_URL, "asr_url", asr_cfg.get("url"))) or DEFAULT_ASR_URL,
        mt_url=_url(pick(ENV_MT_URL, "mt_url", (cfg.get("mt") or {}).get("url"))),
        tts_url=_url(pick(ENV_TTS_URL, "tts_url", (cfg.get("tts") or {}).get("url"))),
        api_token_file=_clean(pick(ENV_API_TOKEN_FILE, "api_token_file", cfg.get("api_token_file"))),
        asr_transport=str(asr_cfg.get("transport") or "http").strip(),
        timeouts=Timeouts(
            connect_s=float(timeouts_cfg.get("connect_s", defaults.connect_s)),
            asr_s=float(timeouts_cfg.get("asr_s", defaults.asr_s)),
            mt_s=float(timeouts_cfg.get("mt_s", defaults.mt_s)),
            tts_s=float(timeouts_cfg.get("tts_s", defaults.tts_s)),
        ),
        managed=frozenset(managed),
    )


_runtime_config: Optional[EdgeSettings] = None


def get_runtime_config() -> EdgeSettings:
    global _runtime_config
    if _runtime_config is None:
        from .config import get_config_manager
        _runtime_config = resolve_edge_settings(get_config_manager().get_services_config(), os.environ)
    return _runtime_config


def reset_runtime_config() -> None:
    global _runtime_config
    _runtime_config = None
```

- [ ] **Step 3: `src/core/config.py`**

1. Додати `import copy` угорі.
2. Замінити весь словник `self._default_config = {...}` на:

```python
        self._default_config = {
            'audio': {
                'sample_rate': 16000,
                'chunk_size': 1024,
                'use_virtual_devices': True,
            },
            'translation': {
                'source_lang': 'auto',
                'target_lang': 'en',
            },
            'speech': {
                'tts_voice': 'af_heart',
                'tts_speed': 1.0,
            },
            'services': {
                'mode': 'transcribe',
                'asr': {'url': 'http://127.0.0.1:8091', 'transport': 'http'},
                'mt': {'url': None},
                'tts': {'url': None},
                'api_token_file': None,
                'timeouts': {'connect_s': 3.0, 'asr_s': 30.0, 'mt_s': 15.0, 'tts_s': 30.0},
            },
            'ui': {
                'minimize_to_tray': True,
                'start_minimized': False,
            },
        }
```

3. У `_load_config` обидва `return self._default_config.copy()` → `return copy.deepcopy(self._default_config)`, а `config = self._merge_config(self._default_config, file_config)` → `config = self._merge_config(copy.deepcopy(self._default_config), file_config)`. У `reset_to_defaults` теж `copy.deepcopy`.
4. У `get_translation_config` прибрати ключ `'whisper_model'`.
5. Додати метод:

```python
    def get_services_config(self) -> Dict[str, Any]:
        """Remote speech service settings (`services` section), as an independent copy."""
        return copy.deepcopy(self.get('services', {}))
```

(`get_wyoming_config`/`set_wyoming_config` поки лишаються — їх викликає UI; видаляються в Task 11.)

- [ ] **Step 4: `config/default.yml`** — повна заміна:

```yaml
# Real-Time Translator — edge configuration (~/.config/real-time-translator/config.yml)
# Speech services (ASR/MT/TTS) run remotely (NixOS services.rtSpeech.*).
# Values set via RT_EDGE_* environment (Home Manager rt-translator module) win over this file.

services:
  mode: transcribe            # transcribe (ASR only) | translate (ASR -> MT -> TTS)
  asr:
    url: "http://127.0.0.1:8091"
    transport: http           # http (POST per VAD segment) | ws (streaming, partials)
  mt:
    url: null                 # e.g. "http://gpu-host:8092" (required for translate)
  tts:
    url: null                 # e.g. "http://gpu-host:8093" (required for translate)
  api_token_file: null        # file with the bearer token (never the token itself)
  timeouts:
    connect_s: 3
    asr_s: 30
    mt_s: 15
    tts_s: 30

audio:
  sample_rate: 16000
  chunk_size: 1024
  use_virtual_devices: true

translation:                  # sent with every request (services are stateless)
  source_lang: "auto"         # auto | uk | pl | ...
  target_lang: "en"

speech:                       # sent with every TTS request
  tts_voice: "af_heart"
  tts_speed: 1.0

ui:
  minimize_to_tray: false
  start_minimized: false
  always_on_top: false
  show_audio_levels: true
  update_interval_ms: 100
  theme: "system"

logging:
  level: "INFO"
```

- [ ] **Step 5: Тести зелені**

```bash
nix develop -c pytest tests/edge -q
```
Expected: PASS (усі тести обох файлів).

- [ ] **Step 6: Commit**

```bash
git add src/core/runtime.py src/core/config.py config/default.yml tests/edge/
git commit -m "feat(edge): EdgeSettings with RT_EDGE_* > config.yml precedence, services config defaults"
```

---

### Task 7: HTTP-клієнти ASR/MT/TTS

**Files:**
- Create: `src/clients/__init__.py`, `src/clients/base.py`, `src/clients/asr.py`, `src/clients/mt.py`, `src/clients/tts.py`
- Test: `tests/edge/test_edge_clients.py`

**Interfaces:**
- Consumes: `EdgeSettings`, `Timeouts` (Task 6).
- Produces:
  - `src.clients.base`: `class ServiceError(Exception)` з атрибутами `service: str`, `kind: str` (`"unreachable" | "timeout" | "auth" | "http"`), `status_code: Optional[int]`; `read_token(token_file: Optional[str]) -> Optional[str]`; `class ServiceClient(base_url, *, token_file=None, timeout_s=30.0, connect_timeout_s=3.0, transport=None)` з `service: str`, `base_url: str`, `health() -> bool`, `status() -> Optional[dict]`, `close()`, protected `_request(method, path, **kw) -> httpx.Response`, `_json(resp) -> dict`.
  - `src.clients.asr`: `@dataclass(frozen=True) AsrResult(text: str, language: Optional[str] = None, segments: list = [])`, `encode_wav(audio_int16, sample_rate) -> bytes`, `AsrClient.transcribe(audio_int16, sample_rate=16000, language=None, prompt=None) -> AsrResult`.
  - `src.clients.mt`: `MtClient.translate(texts: list[str], source_lang: str, target_lang: str) -> list[str]`.
  - `src.clients.tts`: `decode_wav(data: bytes) -> tuple[np.ndarray, int]`, `TtsClient.synthesize(text, voice=None, speed=None) -> tuple[np.ndarray, int]`.
  - `src.clients`: `@dataclass SpeechClients(asr, mt=None, tts=None)` з `by_name() -> dict[str, ServiceClient]`, `close()`; `build_clients(settings: EdgeSettings, transport=None) -> SpeechClients`.

- [ ] **Step 1: Падаючі тести**

`tests/edge/test_edge_clients.py`:

```python
import io
import json
import wave

import httpx
import numpy as np
import pytest

from src.clients import SpeechClients, build_clients
from src.clients.asr import AsrClient, AsrResult, encode_wav
from src.clients.base import ServiceClient, ServiceError, read_token
from src.clients.mt import MtClient
from src.clients.tts import TtsClient
from src.core.runtime import EdgeSettings


def mock(handler):
    return httpx.MockTransport(handler)


def wav_bytes(frames: bytes, rate=24000, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def test_asr_posts_wav_multipart_with_language_and_prompt():
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), auth=req.headers.get("authorization"), body=req.content)
        return httpx.Response(200, json={"text": " привіт ", "language": "uk",
                                         "segments": [{"start": 0.0, "end": 1.0, "text": "привіт"}]})

    r = AsrClient("http://asr:8091", transport=mock(handler)).transcribe(
        np.zeros(1600, dtype=np.int16), language="uk", prompt="контекст")
    assert r == AsrResult(text="привіт", language="uk", segments=[{"start": 0.0, "end": 1.0, "text": "привіт"}])
    assert seen["url"] == "http://asr:8091/v1/audio/transcriptions"
    assert seen["auth"] is None
    body = seen["body"]
    assert b'name="language"' in body and b"uk" in body
    assert b'name="prompt"' in body and "контекст".encode() in body
    assert b'name="response_format"' in body and b"verbose_json" in body
    assert b"RIFF" in body and b"WAVE" in body


def test_asr_omits_language_and_prompt_when_none():
    def handler(req):
        assert b'name="language"' not in req.content
        assert b'name="prompt"' not in req.content
        return httpx.Response(200, json={"text": "hi"})

    assert AsrClient("http://asr", transport=mock(handler)).transcribe(np.ones(10, dtype=np.int16)).text == "hi"


def test_empty_audio_is_not_sent():
    def handler(req):
        raise AssertionError("must not be called")

    assert AsrClient("http://asr", transport=mock(handler)).transcribe(np.zeros(0, dtype=np.int16)) == AsrResult(text="")


def test_encode_wav_roundtrip():
    data = encode_wav(np.array([0, 1000, -1000], dtype=np.int16), 16000)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 3)


@pytest.mark.parametrize("base", ["http://proxy/speech/asr", "http://proxy/speech/asr/"])
def test_base_url_path_prefix_preserved(base):
    def handler(req):
        assert str(req.url) == "http://proxy/speech/asr/health"
        return httpx.Response(200, json={"status": "ok"})

    assert ServiceClient(base, transport=mock(handler)).health() is True


def test_bearer_token_from_file_is_stripped(tmp_path):
    tok = tmp_path / "token"
    tok.write_text("s3cret\n")

    def handler(req):
        assert req.headers["authorization"] == "Bearer s3cret"
        return httpx.Response(200, json={"translations": ["hello"], "source_lang": "uk", "target_lang": "en"})

    client = MtClient("http://mt", token_file=str(tok), transport=mock(handler))
    assert client.translate(["привіт"], "uk", "en") == ["hello"]


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_missing_or_empty_token_file_means_no_header(tmp_path, content):
    path = tmp_path / "token"
    if content is not None:
        path.write_text(content)
    assert read_token(str(path)) is None
    assert read_token(None) is None


def test_mt_sends_contract_payload():
    def handler(req):
        assert req.url.path == "/v1/translate"
        assert json.loads(req.content) == {"text": ["a", "b"], "source_lang": "uk", "target_lang": "en"}
        return httpx.Response(200, json={"translations": ["A", "B"]})

    assert MtClient("http://mt", transport=mock(handler)).translate(["a", "b"], "uk", "en") == ["A", "B"]


def test_mt_empty_input_makes_no_request():
    def handler(req):
        raise AssertionError("must not be called")

    assert MtClient("http://mt", transport=mock(handler)).translate([], "uk", "en") == []


def test_mt_rejects_mismatched_translation_count():
    client = MtClient("http://mt", transport=mock(lambda req: httpx.Response(200, json={"translations": []})))
    with pytest.raises(ServiceError) as e:
        client.translate(["a"], "uk", "en")
    assert e.value.kind == "http"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (503, "http"), (500, "http")])
def test_http_errors_are_classified(status, kind):
    client = MtClient("http://mt", transport=mock(lambda req: httpx.Response(status, text="nope")))
    with pytest.raises(ServiceError) as e:
        client.translate(["a"], "uk", "en")
    assert (e.value.kind, e.value.status_code, e.value.service) == (kind, status, "mt")


def test_unreachable_and_timeout_are_classified():
    def refuse(req):
        raise httpx.ConnectError("refused", request=req)

    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    for handler, kind in ((refuse, "unreachable"), (slow, "timeout")):
        with pytest.raises(ServiceError) as e:
            AsrClient("http://asr", transport=mock(handler)).transcribe(np.ones(5, dtype=np.int16))
        assert e.value.kind == kind


def test_invalid_json_is_http_error():
    client = AsrClient("http://asr", transport=mock(lambda req: httpx.Response(200, text="<html>")))
    with pytest.raises(ServiceError) as e:
        client.transcribe(np.ones(5, dtype=np.int16))
    assert e.value.kind == "http"


def test_tts_returns_float_audio_and_rate():
    pcm = np.array([0, 16384, -16384], dtype="<i2").tobytes()

    def handler(req):
        assert req.url.path == "/v1/audio/speech"
        assert json.loads(req.content) == {"input": "hello", "response_format": "wav", "voice": "af_heart", "speed": 1.25}
        return httpx.Response(200, content=wav_bytes(pcm), headers={"content-type": "audio/wav"})

    audio, rate = TtsClient("http://tts", transport=mock(handler)).synthesize("hello", voice="af_heart", speed=1.25)
    assert rate == 24000 and audio.dtype == np.float32
    np.testing.assert_allclose(audio, [0.0, 0.5, -0.5])


def test_tts_rejects_non_pcm16_wav():
    client = TtsClient("http://tts", transport=mock(lambda req: httpx.Response(200, content=wav_bytes(b"\x00" * 12, width=4))))
    with pytest.raises(ServiceError) as e:
        client.synthesize("x")
    assert e.value.kind == "http"


def test_status_returns_dict_or_none():
    ok = ServiceClient("http://s", transport=mock(lambda r: httpx.Response(200, json={"ready": True})))
    assert ok.status() == {"ready": True}
    down = ServiceClient("http://s", transport=mock(lambda r: httpx.Response(500)))
    assert down.status() is None
    assert down.health() is False


def test_build_clients_follows_mode():
    t = mock(lambda r: httpx.Response(200, json={}))
    transcribe = build_clients(EdgeSettings(), transport=t)
    assert isinstance(transcribe, SpeechClients)
    assert transcribe.mt is None and transcribe.tts is None
    assert set(transcribe.by_name()) == {"asr"}
    translate = build_clients(EdgeSettings(mode="translate", mt_url="http://m", tts_url="http://t"), transport=t)
    assert set(translate.by_name()) == {"asr", "mt", "tts"}
    assert translate.mt.base_url == "http://m"
```

```bash
git add tests/edge/test_edge_clients.py
nix develop -c pytest tests/edge/test_edge_clients.py -q
```
Expected: FAIL (`ModuleNotFoundError: No module named 'src.clients'`).

- [ ] **Step 2: `src/clients/base.py`**

```python
"""Shared HTTP plumbing for the remote speech services (overview §4.3)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import httpx
from loguru import logger


class ServiceError(Exception):
    """A request to a speech service failed. kind: unreachable | timeout | auth | http."""

    def __init__(self, service: str, kind: str, message: str, status_code: Optional[int] = None):
        super().__init__(f"{service}: {kind}: {message}")
        self.service = service
        self.kind = kind
        self.status_code = status_code


def read_token(token_file: Optional[str]) -> Optional[str]:
    if not token_file:
        return None
    try:
        token = Path(token_file).read_text().strip()
    except OSError as e:
        logger.warning("API token file {} unreadable ({}); sending requests without Authorization", token_file, e)
        return None
    if not token:
        logger.warning("API token file {} is empty; sending requests without Authorization", token_file)
        return None
    return token


class ServiceClient:
    service = "service"

    def __init__(self, base_url: str, *, token_file: Optional[str] = None, timeout_s: float = 30.0,
                 connect_timeout_s: float = 3.0, transport: Optional[httpx.BaseTransport] = None):
        self.base_url = base_url.rstrip("/")
        headers = {}
        token = read_token(token_file)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=self.base_url + "/",
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            transport=transport,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise ServiceError(self.service, "timeout", str(e)) from e
        except httpx.TransportError as e:
            raise ServiceError(self.service, "unreachable", str(e)) from e
        if resp.status_code in (401, 403):
            raise ServiceError(self.service, "auth", f"HTTP {resp.status_code}", resp.status_code)
        if resp.status_code >= 400:
            raise ServiceError(self.service, "http", f"HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)
        return resp

    def _json(self, resp: httpx.Response) -> dict:
        try:
            data = resp.json()
        except ValueError as e:
            raise ServiceError(self.service, "http", f"invalid JSON: {resp.text[:200]!r}") from e
        if not isinstance(data, dict):
            raise ServiceError(self.service, "http", f"unexpected JSON payload: {data!r:.200}")
        return data

    def health(self) -> bool:
        try:
            self._request("GET", "/health")
        except ServiceError:
            return False
        return True

    def status(self) -> Optional[dict]:
        try:
            return self._json(self._request("GET", "/status"))
        except ServiceError:
            return None

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 3: `asr.py`, `mt.py`, `tts.py`, `__init__.py`**

`src/clients/asr.py`:

```python
from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .base import ServiceClient


@dataclass(frozen=True)
class AsrResult:
    text: str
    language: Optional[str] = None
    segments: list = field(default_factory=list)


def encode_wav(audio_int16: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(np.asarray(audio_int16, dtype="<i2").tobytes())
    return buf.getvalue()


class AsrClient(ServiceClient):
    service = "asr"

    def transcribe(self, audio_int16: np.ndarray, sample_rate: int = 16000,
                   language: Optional[str] = None, prompt: Optional[str] = None) -> AsrResult:
        if len(audio_int16) == 0:
            return AsrResult(text="")
        data = {"response_format": "verbose_json"}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        files = {"file": ("chunk.wav", encode_wav(audio_int16, sample_rate), "audio/wav")}
        payload = self._json(self._request("POST", "/v1/audio/transcriptions", data=data, files=files))
        return AsrResult(
            text=str(payload.get("text") or "").strip(),
            language=payload.get("language"),
            segments=list(payload.get("segments") or []),
        )
```

`src/clients/mt.py`:

```python
from __future__ import annotations

from typing import List

from .base import ServiceClient, ServiceError


class MtClient(ServiceClient):
    service = "mt"

    def translate(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        if not texts:
            return []
        payload = self._json(self._request("POST", "/v1/translate", json={
            "text": list(texts), "source_lang": source_lang, "target_lang": target_lang,
        }))
        out = payload.get("translations")
        if not isinstance(out, list) or len(out) != len(texts):
            raise ServiceError(self.service, "http", f"unexpected translations payload: {payload!r:.200}")
        return [str(t) for t in out]
```

`src/clients/tts.py`:

```python
from __future__ import annotations

import io
import wave
from typing import Optional, Tuple

import numpy as np

from .base import ServiceClient, ServiceError


def decode_wav(data: bytes) -> Tuple[np.ndarray, int]:
    """PCM16 WAV -> mono float32 in [-1, 1] and its sample rate."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"expected PCM16 WAV, got sample width {w.getsampwidth()} bytes")
        channels = w.getnchannels()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.float32)
    return audio, rate


class TtsClient(ServiceClient):
    service = "tts"

    def synthesize(self, text: str, voice: Optional[str] = None, speed: Optional[float] = None) -> Tuple[np.ndarray, int]:
        body = {"input": text, "response_format": "wav"}
        if voice:
            body["voice"] = voice
        if speed is not None:
            body["speed"] = float(speed)
        resp = self._request("POST", "/v1/audio/speech", json=body)
        try:
            return decode_wav(resp.content)
        except (wave.Error, ValueError, EOFError) as e:
            raise ServiceError(self.service, "http", f"invalid WAV from TTS: {e}") from e
```

`src/clients/__init__.py`:

```python
"""HTTP/WS clients for the remote speech services (rt-asr, rt-mt, rt-tts)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import httpx

from ..core.runtime import EdgeSettings
from .asr import AsrClient
from .base import ServiceClient, ServiceError
from .mt import MtClient
from .tts import TtsClient

__all__ = ["AsrClient", "MtClient", "TtsClient", "ServiceClient", "ServiceError", "SpeechClients", "build_clients"]


@dataclass
class SpeechClients:
    asr: AsrClient
    mt: Optional[MtClient] = None
    tts: Optional[TtsClient] = None

    def by_name(self) -> Dict[str, ServiceClient]:
        return {name: c for name, c in (("asr", self.asr), ("mt", self.mt), ("tts", self.tts)) if c is not None}

    def close(self) -> None:
        for client in self.by_name().values():
            client.close()


def build_clients(settings: EdgeSettings, transport: Optional[httpx.BaseTransport] = None) -> SpeechClients:
    t = settings.timeouts
    common = dict(token_file=settings.api_token_file, connect_timeout_s=t.connect_s, transport=transport)
    asr = AsrClient(settings.asr_url, timeout_s=t.asr_s, **common)
    if settings.mode != "translate":
        return SpeechClients(asr=asr)
    return SpeechClients(
        asr=asr,
        mt=MtClient(settings.mt_url, timeout_s=t.mt_s, **common),
        tts=TtsClient(settings.tts_url, timeout_s=t.tts_s, **common),
    )
```

- [ ] **Step 4: Тести зелені**

```bash
git add src/clients/
nix develop -c pytest tests/edge -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(edge): httpx clients for rt-asr/rt-mt/rt-tts with token, timeouts, error kinds"
```

---

### Task 8: `ServiceHealthMonitor`

**Files:**
- Create: `src/clients/health.py`
- Test: `tests/edge/test_edge_health.py`

**Interfaces:**
- Consumes: об'єкти з `status() -> Optional[dict]` і `health() -> bool` (Task 7).
- Produces: `ServiceHealthMonitor(clients: Mapping[str, client], interval_s=5.0)` з `poll_once() -> dict`, `snapshot() -> dict`, `start()`, `stop()`. Елемент snapshot: `{"connected": bool, "ready": bool, "model": Optional[str], "device": Optional[str], "error": Optional[str]}` (`error` ∈ `None`, `"no-status"`, `"unreachable"`).

- [ ] **Step 1: Падаючі тести**

`tests/edge/test_edge_health.py`:

```python
import time

from src.clients.health import ServiceHealthMonitor

DOWN = {"connected": False, "ready": False, "model": None, "device": None, "error": "unreachable"}


class FakeClient:
    def __init__(self, status=None, healthy=False):
        self._status = status
        self._healthy = healthy
        self.calls = 0

    def status(self):
        self.calls += 1
        return self._status

    def health(self):
        return self._healthy


def test_poll_once_reports_status_fields():
    m = ServiceHealthMonitor({"asr": FakeClient({"ready": True, "device": "cuda", "model": {"id": "large-v3", "loaded": True}})})
    assert m.poll_once()["asr"] == {"connected": True, "ready": True, "model": "large-v3", "device": "cuda", "error": None}


def test_model_as_plain_string_and_not_ready():
    m = ServiceHealthMonitor({"mt": FakeClient({"ready": False, "model": "nllb-600m"})})
    assert m.poll_once()["mt"] == {"connected": True, "ready": False, "model": "nllb-600m", "device": None, "error": None}


def test_health_fallback_when_status_missing():
    m = ServiceHealthMonitor({"tts": FakeClient(None, healthy=True)})
    assert m.poll_once()["tts"] == {"connected": True, "ready": False, "model": None, "device": None, "error": "no-status"}


def test_unreachable_and_initial_snapshot():
    m = ServiceHealthMonitor({"asr": FakeClient(None, healthy=False)})
    assert m.snapshot()["asr"] == DOWN
    assert m.poll_once()["asr"] == DOWN


def test_client_exception_is_reported_not_raised():
    class Boom(FakeClient):
        def status(self):
            raise RuntimeError("boom")

    m = ServiceHealthMonitor({"asr": Boom()})
    assert m.poll_once()["asr"] == DOWN


def test_background_thread_polls_and_stops():
    c = FakeClient({"ready": True})
    m = ServiceHealthMonitor({"asr": c}, interval_s=0.01)
    m.start()
    time.sleep(0.1)
    m.stop()
    assert c.calls >= 2
    n = c.calls
    time.sleep(0.05)
    assert c.calls == n
```

```bash
git add tests/edge/test_edge_health.py
nix develop -c pytest tests/edge/test_edge_health.py -q
```
Expected: FAIL (`No module named 'src.clients.health'`).

- [ ] **Step 2: Реалізація**

`src/clients/health.py`:

```python
"""Background health polling so the Qt thread never does network I/O."""
from __future__ import annotations

import threading
from typing import Any, Dict, Mapping, Optional

from loguru import logger


def _down(error: str = "unreachable") -> Dict[str, Any]:
    return {"connected": False, "ready": False, "model": None, "device": None, "error": error}


def _from_status(status: dict) -> Dict[str, Any]:
    model = status.get("model")
    if isinstance(model, dict):
        model = model.get("id")
    return {
        "connected": True,
        "ready": bool(status.get("ready", True)),
        "model": model,
        "device": status.get("device"),
        "error": None,
    }


class ServiceHealthMonitor:
    def __init__(self, clients: Mapping[str, Any], interval_s: float = 5.0):
        self._clients = dict(clients)
        self._interval = interval_s
        self._lock = threading.Lock()
        self._snapshot: Dict[str, Dict[str, Any]] = {name: _down() for name in self._clients}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _probe(self, client: Any) -> Dict[str, Any]:
        try:
            status = client.status()
            if status is not None:
                return _from_status(status)
            if client.health():
                return {"connected": True, "ready": False, "model": None, "device": None, "error": "no-status"}
        except Exception as e:  # a broken client must not kill the poller
            logger.debug("health probe failed: {}", e)
        return _down()

    def poll_once(self) -> Dict[str, Dict[str, Any]]:
        for name, client in self._clients.items():
            entry = self._probe(client)
            with self._lock:
                self._snapshot[name] = entry
        return self.snapshot()

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {name: dict(entry) for name, entry in self._snapshot.items()}

    def _run(self) -> None:
        self.poll_once()
        while not self._stop.wait(self._interval):
            self.poll_once()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="rt-health")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
```

- [ ] **Step 3: Тести зелені**

```bash
git add src/clients/health.py
nix develop -c pytest tests/edge -q
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(edge): background ServiceHealthMonitor polling /status with /health fallback"
```

---

### Task 9: `TranslationSystem` на HTTP-клієнтах (transcribe | translate)

**Files:**
- Modify: `src/translation_system.py`
- Test: `tests/edge/test_edge_translation_system.py`

**Interfaces:**
- Consumes: `EdgeSettings`, `get_runtime_config` (Task 6); `SpeechClients`, `build_clients`, `ServiceError`, `AsrResult` (Task 7); `ServiceHealthMonitor` (Task 8).
- Produces:
  ```python
  TranslationSystem(settings: Optional[EdgeSettings] = None, source_lang="auto", target_lang="en",
                    sample_rate=16000, tts_voice: Optional[str] = None, tts_speed: Optional[float] = None,
                    audio_router=None, clients: Optional[SpeechClients] = None, health_monitor=None)
  ```
  атрибути `settings`, `clients`, `health_monitor`, `audio_router`, `source_lang`, `target_lang`, `sample_rate`, `tts_voice`, `tts_speed`, `_playback_queue`; методи `start()`, `stop()`, `cleanup()`, `set_languages(src, tgt)`, `set_tts(voice, speed)`, `toggle_translation(enabled)`, `set_status_callback(cb)`, `get_audio_devices()`, `get_stats() -> dict`, `refresh_health() -> dict`, `_process_chunk(audio_int16)`, `_handle_recognized(text: str, detected_language: Optional[str])` (використає Task 14). Ключі `get_stats()`: `running, translation_enabled, mode, source_language, target_language, audio_level_input, pipeline_cycles, pipeline_errors, chunks_processed, total_pipeline_time_s, pending_recognized, pending_translated`, і для кожного `svc ∈ {asr, mt, tts}`: `{svc}_used, {svc}_connected, {svc}_ready, {svc}_model, {svc}_device, {svc}_error`.

- [ ] **Step 1: Падаючі тести**

`tests/edge/test_edge_translation_system.py`:

```python
import numpy as np
import pytest

from src.clients import SpeechClients
from src.clients.asr import AsrResult
from src.clients.base import ServiceError
from src.core.runtime import EdgeSettings
from src.translation_system import TranslationSystem

AUDIO = np.ones(16000, dtype=np.int16)


class StubRouter:
    def get_virtual_devices(self):
        return ("rt_virtual_input", "rt_virtual_output")

    def list_devices(self):
        return {"inputs": [], "outputs": []}

    def cleanup(self):
        pass


class FakeAsr:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def transcribe(self, audio, sample_rate=16000, language=None, prompt=None):
        self.calls.append({"n": len(audio), "language": language, "prompt": prompt, "sr": sample_rate})
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeMt:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def translate(self, texts, source_lang, target_lang):
        self.calls.append((list(texts), source_lang, target_lang))
        if self.error:
            raise self.error
        return [f"{t}->{target_lang}" for t in texts]


class FakeTts:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def synthesize(self, text, voice=None, speed=None):
        self.calls.append((text, voice, speed))
        if self.error:
            raise self.error
        return np.zeros(2400, dtype=np.float32), 24000


class FakeHealth:
    def __init__(self, snap=None):
        self.snap = snap or {}
        self.started = False

    def snapshot(self):
        return self.snap

    def poll_once(self):
        return self.snap

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


def make(mode="transcribe", asr_results=(), mt=None, tts=None, health=None, **kw):
    settings = EdgeSettings(mode=mode,
                            mt_url="http://mt" if mode == "translate" else None,
                            tts_url="http://tts" if mode == "translate" else None)
    clients = SpeechClients(asr=FakeAsr(asr_results), mt=mt, tts=tts)
    events = []
    ts = TranslationSystem(settings=settings, audio_router=StubRouter(), clients=clients,
                           health_monitor=health or FakeHealth(), **kw)
    ts.set_status_callback(events.append)
    return ts, clients, events


def test_transcribe_mode_never_calls_mt_or_tts():
    mt, tts = FakeMt(), FakeTts()
    ts, clients, events = make("transcribe", [AsrResult("привіт світ", "uk")], mt=mt, tts=tts)
    ts._process_chunk(AUDIO)
    assert mt.calls == [] and tts.calls == []
    assert ts._playback_queue.qsize() == 0
    stats = ts.get_stats()
    assert stats["pending_recognized"] == ["привіт світ"]
    assert stats["pending_translated"] == []
    assert stats["mode"] == "transcribe" and stats["mt_used"] is False
    assert events == [{"status": "transcription_complete", "original_text": "привіт світ"}]


def test_translate_mode_uses_detected_language_and_plays_audio():
    mt, tts = FakeMt(), FakeTts()
    ts, clients, events = make("translate", [AsrResult("привіт", "uk")], mt=mt, tts=tts,
                               target_lang="en", tts_voice="am_adam", tts_speed=1.1)
    ts._process_chunk(AUDIO)
    assert clients.asr.calls[0]["language"] is None
    assert mt.calls == [(["привіт"], "uk", "en")]
    assert tts.calls == [("привіт->en", "am_adam", 1.1)]
    assert ts._playback_queue.qsize() == 1
    assert ts.get_stats()["pending_translated"] == ["привіт->en"]
    assert events[-1] == {"status": "translation_complete", "original_text": "привіт", "translated_text": "привіт->en"}


def test_explicit_source_language_is_sent_to_asr_and_mt():
    mt = FakeMt()
    ts, clients, _ = make("translate", [AsrResult("cześć", "uk")], mt=mt, tts=FakeTts(), source_lang="pl")
    ts._process_chunk(AUDIO)
    assert clients.asr.calls[0]["language"] == "pl"
    assert mt.calls[0][1] == "pl"


def test_auto_without_detected_language_skips_mt():
    mt, tts = FakeMt(), FakeTts()
    ts, _, _ = make("translate", [AsrResult("???", None)], mt=mt, tts=tts)
    ts._process_chunk(AUDIO)
    assert mt.calls == [] and tts.calls == []
    assert ts.get_stats()["pending_recognized"] == ["???"]


def test_same_language_skips_mt_and_speaks_original():
    mt, tts = FakeMt(), FakeTts()
    ts, _, _ = make("translate", [AsrResult("hello there", "en")], mt=mt, tts=tts, target_lang="en")
    ts._process_chunk(AUDIO)
    assert mt.calls == []
    assert tts.calls[0][0] == "hello there"


def test_asr_error_counts_and_records_kind():
    ts, _, events = make("transcribe", [ServiceError("asr", "unreachable", "down")])
    ts._process_chunk(AUDIO)
    stats = ts.get_stats()
    assert stats["pipeline_errors"] == 1
    assert stats["asr_error"] == "unreachable"
    assert stats["pending_recognized"] == [] and events == []


def test_mt_error_keeps_transcription_and_skips_tts():
    tts = FakeTts()
    ts, _, _ = make("translate", [AsrResult("привіт", "uk")], mt=FakeMt(ServiceError("mt", "auth", "401")), tts=tts)
    ts._process_chunk(AUDIO)
    stats = ts.get_stats()
    assert stats["pending_recognized"] == ["привіт"]
    assert stats["mt_error"] == "auth" and tts.calls == []


def test_tts_error_keeps_translation_without_playback():
    ts, _, events = make("translate", [AsrResult("привіт", "uk")], mt=FakeMt(), tts=FakeTts(ServiceError("tts", "http", "bad wav")))
    ts._process_chunk(AUDIO)
    stats = ts.get_stats()
    assert stats["pending_translated"] == ["привіт->en"]
    assert stats["tts_error"] == "http"
    assert ts._playback_queue.qsize() == 0
    assert events[-1]["status"] == "translation_complete"


def test_duplicate_and_empty_results_are_skipped():
    ts, _, _ = make("transcribe", [AsrResult("this is a long sentence"), AsrResult("this is a long sentence"), AsrResult("  ")])
    for _ in range(3):
        ts._process_chunk(AUDIO)
    assert ts.get_stats()["pending_recognized"] == ["this is a long sentence"]


def test_previous_text_is_sent_as_prompt():
    ts, clients, _ = make("transcribe", [AsrResult("перше речення тут"), AsrResult("друге речення")])
    ts._process_chunk(AUDIO)
    ts._process_chunk(AUDIO)
    assert clients.asr.calls[0]["prompt"] is None
    assert clients.asr.calls[1]["prompt"] == "перше речення тут"


def test_empty_audio_is_ignored():
    ts, clients, _ = make("transcribe", [])
    ts._process_chunk(np.zeros(0, dtype=np.int16))
    assert clients.asr.calls == []


def test_stats_merge_health_snapshot():
    health = FakeHealth({"asr": {"connected": True, "ready": True, "model": "small", "device": "cpu", "error": None}})
    ts, _, _ = make("transcribe", [], health=health)
    stats = ts.get_stats()
    assert (stats["asr_connected"], stats["asr_ready"], stats["asr_model"], stats["asr_device"]) == (True, True, "small", "cpu")
    assert stats["tts_connected"] is False and stats["tts_used"] is False
```

```bash
git add tests/edge/test_edge_translation_system.py
nix develop -c pytest tests/edge/test_edge_translation_system.py -q
```
Expected: FAIL (`TypeError: TranslationSystem.__init__() got an unexpected keyword argument 'settings'`).

- [ ] **Step 2: Імпорти та видалення IPC-коду**

У `src/translation_system.py`:
- Видалити імпорти `base64`, `json`, `os`, `socket`, `struct`, `from .common.ipc import IPCClient`, `from .core.runtime import get_runtime_config` і класи/функції `WhisperSocketClient`, `_try_connect`.
- Додати імпорти:

```python
from .clients import SpeechClients, build_clients
from .clients.base import ServiceError
from .clients.health import ServiceHealthMonitor
from .core.runtime import EdgeSettings, get_runtime_config
```
- Видалити методи: `_connect_ipc_clients`, `_ipc_service_map`, `_whisper_socket_path`, `reconnect_ipc_clients`, `_translate`, `_speak`, `start_service`, `stop_service`, `set_input_device`, `all_services_connected`.
- `_pipeline_loop`, `_chunk_worker_loop`, `_playback_worker`, `_is_duplicate_stt`, `_resolve_preferred_*`, `toggle_translation`, `set_status_callback`, `get_audio_devices` — без змін.

- [ ] **Step 3: Новий конструктор, start/stop, обробка**

Замінити `__init__` і `_initialize_components`:

```python
    def __init__(
        self,
        settings: Optional[EdgeSettings] = None,
        source_lang: str = "auto",
        target_lang: str = "en",
        sample_rate: int = 16000,
        tts_voice: Optional[str] = None,
        tts_speed: Optional[float] = None,
        audio_router=None,
        clients: Optional[SpeechClients] = None,
        health_monitor=None,
    ):
        import queue as _q

        self.settings = settings or get_runtime_config()
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.sample_rate = sample_rate
        self.tts_voice = tts_voice
        self.tts_speed = tts_speed

        self.audio_router = audio_router if audio_router is not None else AudioRouter()
        self.clients = clients if clients is not None else build_clients(self.settings)
        self.health_monitor = (health_monitor if health_monitor is not None
                               else ServiceHealthMonitor(self.clients.by_name()))

        self.is_running = False
        self.translation_enabled = True
        self.status_callback: Optional[callable] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._chunk_worker_thread: Optional[threading.Thread] = None
        self._playback_thread: Optional[threading.Thread] = None
        self._preferred_input_device_name: Optional[str] = None
        self._preferred_output_device_name: Optional[str] = None
        self._audio_level_input: float = 0.0

        self._pending_recognized: List[str] = []
        self._pending_translated: List[str] = []
        self._pipeline_lock = threading.Lock()
        self._chunk_queue: _q.Queue = _q.Queue(maxsize=5)
        self._playback_queue: _q.Queue = _q.Queue(maxsize=4)
        self._stt_context: deque = deque(maxlen=5)
        self._last_errors: Dict[str, Optional[str]] = {"asr": None, "mt": None, "tts": None}

        self._pipeline_cycles = 0
        self._pipeline_errors = 0
        self._total_whisper_time = 0.0
        self._total_translate_time = 0.0
        self._total_tts_time = 0.0
        self._total_chunks_processed = 0

        logger.info(f"Translation system initialized: mode={self.settings.mode} "
                    f"{source_lang}->{target_lang} asr={self.settings.asr_url}")
```

У `start()` прибрати весь блок «Drop any stale IPC connections … start services manually» і перед `self.is_running = True` вставити `self.health_monitor.start()`. У `stop()` після `join`-ів додати `self.health_monitor.stop()`.

Замінити `_process_chunk` і додати допоміжні методи:

```python
    def _record_error(self, err: ServiceError) -> None:
        self._pipeline_errors += 1
        self._last_errors[err.service] = err.kind
        if self._pipeline_errors == 1 or self._pipeline_errors % 10 == 0:
            logger.warning(f"{err} (pipeline errors so far: {self._pipeline_errors})")

    def _process_chunk(self, audio_int16: np.ndarray):
        """VAD segment -> rt-asr; recognized text -> _handle_recognized."""
        if len(audio_int16) == 0:
            return
        lang = None if self.source_lang == "auto" else self.source_lang
        with self._pipeline_lock:
            prompt = " ".join(self._stt_context) or None
        t0 = _log_timing()
        try:
            result = self.clients.asr.transcribe(audio_int16, sample_rate=self.sample_rate,
                                                 language=lang, prompt=prompt)
        except ServiceError as e:
            self._record_error(e)
            return
        finally:
            self._total_whisper_time += _log_timing() - t0
        self._last_errors["asr"] = None
        self._handle_recognized(result.text, result.language)

    def _handle_recognized(self, text: str, detected_language: Optional[str]) -> None:
        text = (text or "").strip()
        if not text or self._is_duplicate_stt(text):
            return
        logger.info(f"Recognized: {text}")
        self._total_chunks_processed += 1
        with self._pipeline_lock:
            self._pending_recognized.append(text)
            self._stt_context.append(text)

        if self.settings.mode != "translate":
            self._emit({"status": "transcription_complete", "original_text": text})
            return

        source = detected_language if self.source_lang == "auto" else self.source_lang
        if not source:
            logger.warning("Source language unknown (auto, ASR returned none) — skipping translation")
            return

        if source == self.target_lang:
            translated = text
        else:
            t0 = _log_timing()
            try:
                translated = self.clients.mt.translate([text], source, self.target_lang)[0]
            except ServiceError as e:
                self._record_error(e)
                return
            finally:
                self._total_translate_time += _log_timing() - t0
            self._last_errors["mt"] = None
        logger.info(f"Translated: {translated}")
        with self._pipeline_lock:
            self._pending_translated.append(translated)

        t0 = _log_timing()
        try:
            audio, rate = self.clients.tts.synthesize(translated, voice=self.tts_voice, speed=self.tts_speed)
            self._last_errors["tts"] = None
            try:
                self._playback_queue.put_nowait((audio, rate, len(audio) / float(rate)))
            except Exception:
                logger.warning("Playback queue full — dropping TTS audio (playback is behind)")
        except ServiceError as e:
            self._record_error(e)
        finally:
            self._total_tts_time += _log_timing() - t0

        self._emit({"status": "translation_complete", "original_text": text, "translated_text": translated})

    def _emit(self, event: Dict) -> None:
        if self.status_callback:
            self.status_callback(event)
```

Замінити `set_languages`, додати `set_tts`, `refresh_health`, замінити `get_stats` і `cleanup`:

```python
    def set_languages(self, source_lang: str, target_lang: str = "en"):
        self.source_lang = source_lang
        self.target_lang = target_lang
        logger.info(f"Languages updated: {source_lang}->{target_lang}")

    def set_tts(self, voice: Optional[str], speed: Optional[float]) -> None:
        self.tts_voice = voice
        self.tts_speed = speed

    def refresh_health(self) -> Dict:
        return self.health_monitor.poll_once()

    def get_stats(self) -> Dict:
        total = self._total_whisper_time + self._total_translate_time + self._total_tts_time
        stats: Dict = {
            "running": self.is_running,
            "translation_enabled": self.translation_enabled,
            "mode": self.settings.mode,
            "source_language": self.source_lang,
            "target_language": self.target_lang,
            "audio_level_input": self._audio_level_input,
            "pipeline_cycles": self._pipeline_cycles,
            "pipeline_errors": self._pipeline_errors,
            "chunks_processed": self._total_chunks_processed,
            "total_pipeline_time_s": round(total, 2),
        }
        snapshot = self.health_monitor.snapshot()
        active = self.settings.active_services()
        for name in ("asr", "mt", "tts"):
            entry = snapshot.get(name, {})
            stats[f"{name}_used"] = name in active
            stats[f"{name}_connected"] = bool(entry.get("connected", False))
            stats[f"{name}_ready"] = bool(entry.get("ready", False))
            stats[f"{name}_model"] = entry.get("model")
            stats[f"{name}_device"] = entry.get("device")
            stats[f"{name}_error"] = self._last_errors.get(name)
        with self._pipeline_lock:
            stats["pending_recognized"] = list(self._pending_recognized)
            stats["pending_translated"] = list(self._pending_translated)
            self._pending_recognized.clear()
            self._pending_translated.clear()
        return stats

    def cleanup(self):
        self.stop()
        self.health_monitor.stop()
        if self.audio_router:
            try:
                self.audio_router.cleanup()
            except Exception as e:
                logger.exception(f"AudioRouter cleanup error: {e}")
        try:
            self.clients.close()
        except Exception as e:
            logger.debug(f"Error closing service clients: {e}")
        logger.info("Translation system cleaned up")
```

Docstring модуля: `"""Edge coordinator: capture + VAD locally, ASR/MT/TTS over HTTP, playback locally."""`.

- [ ] **Step 4: Тести зелені й немає IPC-залишків**

```bash
nix develop -c pytest tests/edge -q
grep -nE "IPCClient|socket|get_.*socket_path|use_wyoming" src/translation_system.py || echo "clean"
```
Expected: PASS; `clean`.

- [ ] **Step 5: Commit**

```bash
git add src/translation_system.py
git commit -m "feat(edge): TranslationSystem talks to rt-asr/rt-mt/rt-tts over HTTP, transcribe-only mode"
```

---

### Task 10: `DirectAdapter`, контролер, `main.py`

**Files:**
- Rewrite: `src/adapters/direct_adapter.py`
- Modify: `src/adapters/__init__.py`, `src/controller/controller.py`, `src/controller/translator_controller.py`, `src/main.py`
- Delete: `src/adapters/ipc_adapter.py`
- Test: `tests/edge/test_edge_direct_adapter.py`

**Interfaces:**
- Consumes: `TranslationSystem` (Task 9), `EdgeSettings`, `EdgeSettingsError`, `get_runtime_config` (Task 6).
- Produces: `DirectAdapter(source_lang="auto", target_lang="en", sample_rate=16000, skip_preflight=False, settings=None, tts_voice=None, tts_speed=None, translation_system_factory=TranslationSystem)` з `last_error: Optional[str]`, `settings`, `start_pipeline() -> bool`, `stop_pipeline() -> bool`, `reconfigure_services(settings: EdgeSettings) -> bool`, `refresh_services() -> dict`, `get_status() -> dict`, `set_languages`, `set_tts(voice, speed) -> bool`, `get_input_devices`, `get_output_devices`, `set_input_device`, `set_output_device`, `get_audio_levels`, `toggle_translation`, `cleanup`. `TranslatorController` без `start_service`/`stop_service`, з `refresh_services() -> Dict` і `reconfigure_services(settings) -> bool`.

- [ ] **Step 1: Падаючі тести**

`tests/edge/test_edge_direct_adapter.py`:

```python
import pytest

from src.adapters import direct_adapter as da
from src.adapters.direct_adapter import DirectAdapter
from src.core.runtime import EdgeSettings

VALID = EdgeSettings(mode="translate", mt_url="http://m", tts_url="http://t")


class FakeTS:
    instances = []

    def __init__(self, **kw):
        self.kw = kw
        self.settings = kw["settings"]
        self.source_lang = kw.get("source_lang")
        self.target_lang = kw.get("target_lang")
        self.sample_rate = kw.get("sample_rate")
        self.tts = (kw.get("tts_voice"), kw.get("tts_speed"))
        self.audio_router = None
        self.started = False
        self.cleaned = False
        self._audio_level_input = 0.25
        FakeTS.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def get_stats(self):
        return {"running": self.started}

    def refresh_health(self):
        return {"asr": {"connected": True}}

    def set_languages(self, s, t):
        self.source_lang, self.target_lang = s, t

    def set_tts(self, voice, speed):
        self.tts = (voice, speed)

    def cleanup(self):
        self.cleaned = True


def adapter(settings):
    return DirectAdapter(skip_preflight=True, settings=settings, translation_system_factory=FakeTS,
                         tts_voice="af_heart", tts_speed=1.0)


def test_start_pipeline_rejects_invalid_settings():
    a = adapter(EdgeSettings(mode="translate"))
    assert a.start_pipeline() is False
    assert "mt" in a.last_error and "tts" in a.last_error
    assert FakeTS.instances[-1].started is False


def test_start_and_stop_pipeline():
    a = adapter(VALID)
    assert a.start_pipeline() is True and a.last_error is None
    assert FakeTS.instances[-1].started is True
    assert a.stop_pipeline() is True and FakeTS.instances[-1].started is False


def test_reconfigure_services_rebuilds_and_restarts_when_running():
    a = adapter(EdgeSettings())
    a.start_pipeline()
    old = FakeTS.instances[-1]
    assert a.reconfigure_services(VALID) is True
    new = FakeTS.instances[-1]
    assert old.cleaned is True and new is not old
    assert new.settings == VALID and new.started is True
    assert new.tts == ("af_heart", 1.0)


def test_reconfigure_with_invalid_settings_keeps_old_system():
    a = adapter(EdgeSettings())
    old = FakeTS.instances[-1]
    assert a.reconfigure_services(EdgeSettings(mode="translate")) is False
    assert a.translation_system is old and old.cleaned is False


def test_refresh_status_and_levels():
    a = adapter(VALID)
    assert a.refresh_services() == {"asr": {"connected": True}}
    assert a.get_status() == {"running": False}
    assert a.get_audio_levels() == {"input": 0.25, "output": 0.0}


def test_no_service_management_left():
    for name in ("start_service", "stop_service", "restart_service", "restart_all_services",
                 "change_whisper_model", "reconfigure_wyoming", "reconnect_ipc", "get_service_active"):
        assert not hasattr(DirectAdapter, name), name


def test_preflight_failure_mentions_home_manager(monkeypatch):
    monkeypatch.setattr(da.PipeWirePreflight, "check", staticmethod(lambda: False))
    with pytest.raises(RuntimeError, match="virtualSinks"):
        DirectAdapter(settings=VALID, translation_system_factory=FakeTS)
```

```bash
git add tests/edge/test_edge_direct_adapter.py
nix develop -c pytest tests/edge/test_edge_direct_adapter.py -q
```
Expected: FAIL (`unexpected keyword argument 'settings'`).

- [ ] **Step 2: Новий `src/adapters/direct_adapter.py`**

```python
"""DirectAdapter: the edge app's backend — wraps TranslationSystem (remote speech services)."""
from typing import Callable, Dict, List, Optional

from loguru import logger

from ..controller.controller import Device
from ..core.preflight.pipewire import PipeWirePreflight
from ..core.runtime import EdgeSettings, EdgeSettingsError, get_runtime_config
from ..translation_system import TranslationSystem


class DirectAdapter:
    def __init__(self, source_lang: str = "auto", target_lang: str = "en", sample_rate: int = 16000,
                 skip_preflight: bool = False, settings: Optional[EdgeSettings] = None,
                 tts_voice: Optional[str] = None, tts_speed: Optional[float] = None,
                 translation_system_factory: Callable[..., TranslationSystem] = TranslationSystem):
        if not skip_preflight:
            logger.info("Running PipeWire preflight check...")
            if not PipeWirePreflight.check():
                raise RuntimeError(
                    "PipeWire preflight check failed: rt_virtual_input/rt_virtual_output sinks not found. "
                    "Enable rt-translator.virtualSinks (Home Manager), nixosModules.virtual-sinks, or run `nix develop`.")
        self._factory = translation_system_factory
        self.settings = settings or get_runtime_config()
        self.last_error: Optional[str] = None
        self._tts = (tts_voice, tts_speed)
        self.translation_system = self._new_system(self.settings, source_lang, target_lang, sample_rate)
        self.audio_router = self.translation_system.audio_router

    def _new_system(self, settings: EdgeSettings, source_lang: str, target_lang: str, sample_rate: int):
        return self._factory(settings=settings, source_lang=source_lang, target_lang=target_lang,
                             sample_rate=sample_rate, tts_voice=self._tts[0], tts_speed=self._tts[1])

    # -- pipeline ---------------------------------------------------------
    def start_pipeline(self) -> bool:
        try:
            self.settings.validate()
        except EdgeSettingsError as e:
            self.last_error = str(e)
            logger.error(f"Cannot start pipeline: {e}")
            return False
        try:
            self.translation_system.start()
        except Exception as e:
            self.last_error = str(e)
            logger.exception(f"Failed to start pipeline: {e}")
            return False
        self.last_error = None
        return True

    def stop_pipeline(self) -> bool:
        try:
            self.translation_system.stop()
            return True
        except Exception as e:
            logger.exception(f"Failed to stop pipeline: {e}")
            return False

    def reconfigure_services(self, settings: EdgeSettings) -> bool:
        try:
            settings.validate()
        except EdgeSettingsError as e:
            self.last_error = str(e)
            logger.error(f"Rejected service settings: {e}")
            return False
        old = self.translation_system
        was_running = bool(old.get_stats().get("running"))
        if was_running:
            old.stop()
        new = self._new_system(settings, old.source_lang, old.target_lang, old.sample_rate)
        old.cleanup()
        self.translation_system, self.settings = new, settings
        self.audio_router = new.audio_router
        self.last_error = None
        logger.info(f"Speech services reconfigured: mode={settings.mode} asr={settings.asr_url}")
        return self.start_pipeline() if was_running else True

    def refresh_services(self) -> Dict:
        return self.translation_system.refresh_health()

    def get_status(self) -> Dict:
        return self.translation_system.get_stats()

    # -- languages / voice --------------------------------------------------
    def set_languages(self, source_lang: str, target_lang: str = "en") -> bool:
        self.translation_system.set_languages(source_lang, target_lang)
        return True

    def set_tts(self, voice: Optional[str], speed: Optional[float]) -> bool:
        self._tts = (voice, speed)
        self.translation_system.set_tts(voice, speed)
        return True

    # -- audio devices ------------------------------------------------------
    def _devices(self, key: str) -> List[Device]:
        devices = self.translation_system.get_audio_devices()
        result = []
        for info in devices.get(key, []):
            if isinstance(info, dict):
                result.append(Device(info.get("name", ""), info.get("description", "")))
            else:
                result.append(Device(str(info), str(info)))
        return result

    def get_input_devices(self) -> List[Device]:
        return self._devices("inputs")

    def get_output_devices(self) -> List[Device]:
        return self._devices("outputs")

    def set_input_device(self, device_id: str) -> bool:
        if not device_id or device_id.lower() == "default":
            return True
        self.translation_system._preferred_input_device_name = device_id
        if self.audio_router:
            self.audio_router.set_default_source(device_id)
        return True

    def set_output_device(self, device_id: str) -> bool:
        if not device_id or device_id.lower() == "default":
            return True
        self.translation_system._preferred_output_device_name = device_id
        if self.audio_router:
            self.audio_router.set_default_sink(device_id)
        return True

    def get_audio_levels(self) -> Dict[str, float]:
        return {"input": self.translation_system._audio_level_input, "output": 0.0}

    def toggle_translation(self, enabled: bool) -> bool:
        self.translation_system.toggle_translation(enabled)
        return True

    def cleanup(self) -> None:
        self.translation_system.cleanup()
```

- [ ] **Step 3: Адаптери, контролер, `main.py`**

`src/adapters/__init__.py`:

```python
from .direct_adapter import DirectAdapter

__all__ = ['DirectAdapter']
```

```bash
git rm src/adapters/ipc_adapter.py
```

`src/controller/controller.py`: видалити абстрактні `start_service` і `stop_service`; додати

```python
    @abstractmethod
    def refresh_services(self) -> Dict:
        """Force a health poll of the remote speech services."""

    @abstractmethod
    def reconfigure_services(self, settings) -> bool:
        """Apply new EdgeSettings (endpoints/mode)."""
```

`src/controller/translator_controller.py`: прибрати `from ..adapters.ipc_adapter import IPCAdapter`, `Union[...]` → `DirectAdapter`; видалити `start_service`/`stop_service`; додати

```python
    def refresh_services(self) -> Dict:
        return self._adapter.refresh_services()

    def reconfigure_services(self, settings) -> bool:
        return self._adapter.reconfigure_services(settings)
```

`src/main.py`: замінити `create_adapter` на

```python
def create_adapter(mode: str = "direct", **kwargs):
    """Create the backend adapter (only 'direct' exists: remote speech services over HTTP)."""
    if mode != "direct":
        raise ValueError(f"Unsupported adapter mode: {mode}")
    from .core.config import get_config_manager
    from .core.runtime import get_runtime_config
    cfg = get_config_manager()
    return DirectAdapter(
        source_lang=cfg.get("translation.source_lang", "auto"),
        target_lang=cfg.get("translation.target_lang", "en"),
        tts_voice=cfg.get("speech.tts_voice"),
        tts_speed=cfg.get("speech.tts_speed"),
        settings=get_runtime_config(),
        **kwargs,
    )
```
У `parse_args` видалити аргументи `--use-wyoming`, `--wyoming-host`, `--wyoming-port`; `--mode` лишити з `choices=["direct"]`, `default="direct"`. У `main()` виклик → `create_adapter(mode=args.mode)`.

- [ ] **Step 4: Тести + імпорт-перевірка**

```bash
nix develop -c pytest tests/edge -q
nix develop -c python -c "import src.main, src.controller.translator_controller; print('imports ok')"
grep -rn "ipc_adapter\|IPCAdapter\|use_wyoming" src/main.py src/adapters src/controller || echo "clean"
```
Expected: PASS; `imports ok`; `clean`.

- [ ] **Step 5: Commit**

```bash
git add -A src/adapters src/controller src/main.py tests/edge/test_edge_direct_adapter.py
git commit -m "feat(edge): DirectAdapter without systemctl/Wyoming; reconfigure_services, refresh_services"
```

---

### Task 11: UI — статус ASR/MT/TTS, діалог endpoints, без ModelTab/Wyoming

**Files:**
- Rewrite: `src/ui/widgets/service_status_panel.py`
- Create: `src/ui/widgets/endpoints_dialog.py`
- Modify: `src/ui/widgets/main_window.py`, `src/ui/widgets/settings_dialog.py`, `src/ui/controller/ui_controller.py`, `src/core/config.py`
- Delete: `src/ui/widgets/model_tab.py`, `src/ui/widgets/service_settings_dialog.py`
- Test: `tests/edge/test_edge_ui.py`

**Interfaces:**
- Consumes: `resolve_edge_settings`, `EdgeSettings`, `EdgeSettingsError`, `reset_runtime_config` (Task 6); `ServiceClient.status()` (Task 7); `UIController` → `adapter.refresh_services()`, `adapter.reconfigure_services()`, `adapter.set_tts()` (Task 10).
- Produces: `ServiceStatusPanel` з сигналами `refresh_requested()`, `endpoints_requested()`, методами `update_all_services(stats: dict)`, `status_text(name) -> str`; `ServiceStatusManager(panel).update_status(stats)` (сумісність). `EndpointsDialog(config_manager, environ=os.environ, parent=None)` з сигналом `settings_saved(object)` (EdgeSettings) і віджетами `mode`, `asr_url`, `asr_transport`, `mt_url`, `tts_url`, `token_file`, `error_label`. `UIController.refresh_services() -> dict`, `UIController.reconfigure_services(settings) -> bool`.

- [ ] **Step 1: Падаючі Qt-тести (offscreen)**

`tests/edge/test_edge_ui.py`:

```python
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from src.core.config import ConfigManager  # noqa: E402
from src.ui.widgets.endpoints_dialog import EndpointsDialog  # noqa: E402
from src.ui.widgets.service_status_panel import ServiceStatusPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_panel_transcribe_mode(qapp):
    p = ServiceStatusPanel()
    p.update_all_services({"mode": "transcribe", "asr_used": True, "asr_connected": True, "asr_ready": True,
                           "asr_model": "small", "asr_device": "cpu", "mt_used": False, "tts_used": False})
    assert p.status_text("asr") == "Ready · small · cpu"
    assert p.status_text("mt") == "Not used (transcribe mode)"
    assert p.status_text("tts") == "Not used (transcribe mode)"


def test_panel_loading_offline_and_auth(qapp):
    p = ServiceStatusPanel()
    p.update_all_services({"mode": "translate", "asr_used": True, "asr_connected": True, "asr_ready": False,
                           "mt_used": True, "mt_connected": False, "tts_used": True, "tts_connected": True,
                           "tts_ready": True, "tts_error": "auth"})
    assert p.status_text("asr") == "Loading model…"
    assert p.status_text("mt") == "Offline"
    assert p.status_text("tts") == "Auth error (check apiTokenFile)"


def test_endpoints_dialog_managed_fields_disabled(qapp, tmp_path):
    d = EndpointsDialog(ConfigManager(str(tmp_path)), environ={"RT_EDGE_ASR_URL": "http://hm:8091"})
    assert d.asr_url.isEnabled() is False
    assert d.asr_url.text() == "http://hm:8091"
    assert d.mt_url.isEnabled() is True


def test_endpoints_dialog_save_persists(qapp, tmp_path):
    d = EndpointsDialog(ConfigManager(str(tmp_path)), environ={})
    saved = []
    d.settings_saved.connect(saved.append)
    d.mode.setCurrentText("translate")
    d.mt_url.setText("http://gpu:8092")
    d.tts_url.setText("http://gpu:8093/")
    d.accept()
    svc = ConfigManager(str(tmp_path)).get_services_config()
    assert svc["mode"] == "translate"
    assert svc["mt"]["url"] == "http://gpu:8092"
    assert saved and saved[0].tts_url == "http://gpu:8093"


def test_endpoints_dialog_refuses_invalid_translate(qapp, tmp_path):
    d = EndpointsDialog(ConfigManager(str(tmp_path)), environ={})
    d.mode.setCurrentText("translate")
    d.accept()
    assert d.result() != QDialog.Accepted
    assert "mt" in d.error_label.text()
    assert ConfigManager(str(tmp_path)).get_services_config()["mode"] == "transcribe"
```

```bash
git add tests/edge/test_edge_ui.py
nix develop -c pytest tests/edge/test_edge_ui.py -q
```
Expected: FAIL (`No module named 'src.ui.widgets.endpoints_dialog'`).

- [ ] **Step 2: `service_status_panel.py` (повна заміна)**

```python
"""Status of the remote speech services (rt-asr, rt-mt, rt-tts)."""
from typing import Dict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

SERVICES = {"asr": "ASR (rt-asr)", "mt": "MT (rt-mt)", "tts": "TTS (rt-tts)"}
COLORS = {"ok": "green", "warn": "orange", "bad": "red", "off": "gray"}


def describe(name: str, stats: Dict) -> tuple:
    """(text, color-key) for one service from TranslationSystem.get_stats()."""
    if not stats.get(f"{name}_used", name == "asr"):
        return "Not used (transcribe mode)", "off"
    if stats.get(f"{name}_error") == "auth":
        return "Auth error (check apiTokenFile)", "bad"
    if not stats.get(f"{name}_connected"):
        return "Offline", "bad"
    if not stats.get(f"{name}_ready"):
        return "Loading model…", "warn"
    parts = ["Ready"] + [str(v) for v in (stats.get(f"{name}_model"), stats.get(f"{name}_device")) if v]
    return " · ".join(parts), "ok"


class ServiceStatusPanel(QGroupBox):
    refresh_requested = Signal()
    endpoints_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("Speech Services", parent)
        self._dots: Dict[str, QLabel] = {}
        self._texts: Dict[str, QLabel] = {}
        outer = QVBoxLayout(self)
        grid = QGridLayout()
        outer.addLayout(grid)
        for row, (key, label) in enumerate(SERVICES.items()):
            dot, text = QLabel("●"), QLabel("Offline")
            grid.addWidget(dot, row, 0)
            grid.addWidget(QLabel(label), row, 1)
            grid.addWidget(text, row, 2)
            self._dots[key], self._texts[key] = dot, text
            self._paint(key, "Offline", "bad")
        bar = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested.emit)
        endpoints = QPushButton("Endpoints…")
        endpoints.clicked.connect(self.endpoints_requested.emit)
        bar.addWidget(refresh)
        bar.addWidget(endpoints)
        outer.addLayout(bar)

    def _paint(self, key: str, text: str, color: str) -> None:
        self._dots[key].setStyleSheet(f"color: {COLORS[color]}; font-size: 16px; font-weight: bold;")
        self._texts[key].setText(text)
        self._texts[key].setStyleSheet(f"color: {COLORS[color]};")

    def update_all_services(self, stats: Dict) -> None:
        for key in SERVICES:
            self._paint(key, *describe(key, stats))

    def status_text(self, key: str) -> str:
        return self._texts[key].text()


class ServiceStatusManager:
    def __init__(self, panel: ServiceStatusPanel):
        self.panel = panel

    def update_status(self, status_data: Dict):
        self.panel.update_all_services(status_data)
```

- [ ] **Step 3: `endpoints_dialog.py`**

```python
"""Edit remote speech service endpoints (config.yml `services`); RT_EDGE_* values are read-only."""
import os
import threading
from typing import Mapping, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout)

from ...clients.base import ServiceClient
from ...core.runtime import ASR_TRANSPORTS, MODES, EdgeSettingsError, resolve_edge_settings

MANAGED_TIP = "Managed by Home Manager (RT_EDGE_* environment)"


class EndpointsDialog(QDialog):
    settings_saved = Signal(object)  # EdgeSettings
    _test_done = Signal(str)

    def __init__(self, config_manager, environ: Optional[Mapping[str, str]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Speech service endpoints")
        self._cm = config_manager
        self._env = dict(os.environ if environ is None else environ)
        current = resolve_edge_settings(self._cm.get_services_config(), self._env)

        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems(list(MODES))
        self.mode.setCurrentText(current.mode)
        self.asr_url = QLineEdit(current.asr_url)
        self.asr_transport = QComboBox()
        self.asr_transport.addItems(list(ASR_TRANSPORTS))
        self.asr_transport.setCurrentText(current.asr_transport)
        self.mt_url = QLineEdit(current.mt_url or "")
        self.tts_url = QLineEdit(current.tts_url or "")
        self.token_file = QLineEdit(current.api_token_file or "")
        for label, widget, key in (("Mode", self.mode, "mode"), ("ASR URL", self.asr_url, "asr_url"),
                                   ("ASR transport", self.asr_transport, None), ("MT URL", self.mt_url, "mt_url"),
                                   ("TTS URL", self.tts_url, "tts_url"),
                                   ("API token file", self.token_file, "api_token_file")):
            form.addRow(label, widget)
            if key in current.managed:
                widget.setEnabled(False)
                widget.setToolTip(MANAGED_TIP)

        self.test_label = QLabel("")
        test = QPushButton("Test connection")
        test.clicked.connect(self._start_test)
        self._test_done.connect(self.test_label.setText)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(test)
        layout.addWidget(self.test_label)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

    def _services_cfg(self) -> dict:
        cfg = self._cm.get_services_config()
        cfg["mode"] = self.mode.currentText()
        cfg.setdefault("asr", {})["url"] = self.asr_url.text().strip()
        cfg["asr"]["transport"] = self.asr_transport.currentText()
        cfg.setdefault("mt", {})["url"] = self.mt_url.text().strip() or None
        cfg.setdefault("tts", {})["url"] = self.tts_url.text().strip() or None
        cfg["api_token_file"] = self.token_file.text().strip() or None
        return cfg

    def _start_test(self) -> None:
        settings = resolve_edge_settings(self._services_cfg(), self._env)
        self.test_label.setText("Testing…")

        def run():
            parts = []
            for name in settings.active_services():
                url = getattr(settings, f"{name}_url")
                client = ServiceClient(url, token_file=settings.api_token_file, timeout_s=5.0)
                status = client.status()
                client.close()
                parts.append(f"{name}: {'ok' if status is not None else 'unreachable'}")
            self._test_done.emit(", ".join(parts))

        threading.Thread(target=run, daemon=True, name="rt-endpoint-test").start()

    def accept(self) -> None:
        cfg = self._services_cfg()
        settings = resolve_edge_settings(cfg, self._env)
        try:
            settings.validate()
        except EdgeSettingsError as e:
            self.error_label.setText(str(e))
            return
        self._cm.set("services", cfg)
        self._cm.save()
        self.settings_saved.emit(settings)
        super().accept()
```

- [ ] **Step 4: `ui_controller.py`, `main_window.py`, `settings_dialog.py`, `config.py`**

`src/ui/controller/ui_controller.py`: видалити методи `start_service`, `stop_service`, `toggle_service`, `restart_service`, `restart_all_services`, `change_whisper_model`, `reconnect_ipc`, `reconfigure_controller`, `update_service_config`; додати

```python
    def refresh_services(self) -> dict:
        try:
            return self._controller._adapter.refresh_services()
        except Exception as e:
            logger.error(f"Error refreshing services: {e}")
            return {}

    def reconfigure_services(self, settings) -> bool:
        from ...core.runtime import reset_runtime_config
        reset_runtime_config()
        ok = self._controller.reconfigure_services(settings)
        if not ok:
            logger.error(f"Service reconfiguration failed: {self._controller._adapter.last_error}")
        return ok

    def set_tts(self, voice, speed) -> bool:
        return self._controller._adapter.set_tts(voice, speed)
```

`src/ui/widgets/main_window.py`:
- Видалити блок «Wyoming settings — read from config…» у `__init__` (рядки з `get_wyoming_config` і `_current_wyoming_*`).
- П'ять рядків `self.service_status_panel.*.connect(...)` замінити на:

```python
        self.service_status_panel.refresh_requested.connect(self.on_refresh_services_requested)
        self.service_status_panel.endpoints_requested.connect(self.on_endpoints_requested)
```
- Видалити методи `on_service_control_requested`, `on_service_restart_requested`, `on_restart_all_requested`, `on_reconnect_ipc_requested`, `on_service_settings_requested`, `on_service_specific_settings_changed`; додати:

```python
    def on_refresh_services_requested(self):
        snapshot = self.ui_controller.refresh_services()
        up = [name for name, entry in snapshot.items() if entry.get("connected")]
        self.status_manager.log_info(f"Speech services reachable: {', '.join(up) or 'none'}")

    def on_endpoints_requested(self):
        from ...core.config import get_config_manager
        from .endpoints_dialog import EndpointsDialog
        dialog = EndpointsDialog(get_config_manager(), parent=self)
        dialog.settings_saved.connect(self._apply_endpoint_settings)
        dialog.exec()

    def _apply_endpoint_settings(self, settings):
        if self.ui_controller.reconfigure_services(settings):
            self.status_manager.log_info(f"Endpoints applied (mode={settings.mode})")
        else:
            self.status_manager.log_error("Failed to apply endpoints — see log")
```
- `on_general_settings_changed`: видалити всю Wyoming-гілку; лишити

```python
    def on_general_settings_changed(self, settings):
        logger.info(f"General settings changed: {settings}")
        self.current_settings = settings
        source_lang = settings.get('source_lang', 'auto')
        target_lang = settings.get('target_lang', 'en')
        self.ui_controller.set_languages(source_lang, target_lang)
        self.ui_controller.set_tts(settings.get('tts_voice'), settings.get('tts_speed'))
        self._update_language_combo(self.source_lang_combo, source_lang)
        self.status_manager.log_info("Settings applied successfully")
```
- У `toggle_translation`: якщо `toggle_pipeline()` повернув `False`, показати `self.status_manager.log_error(f"Cannot start: {self.ui_controller._controller._adapter.last_error}")`.
- У `_apply_ui_update`: після оновлення панелі, якщо `update_data.get("mode") == "transcribe"`, вимкнути віджет перекладу: знайти в `init_ui` віджет, куди пише `log_translated` (через `self.status_manager`), і викликати на ньому `setEnabled(update_data.get("mode") != "transcribe")` — або, якщо окремого віджета немає, нічого не робити (лог перекладу просто порожній).

`src/ui/widgets/settings_dialog.py`:
- Видалити `from .model_tab import ModelTab` і створення/додавання вкладки `ModelTab` (`self._models = ModelTab(config)` та відповідний `addTab`, а також використання `self._models` у збиранні налаштувань).
- Видалити комбобокс `self.translate_model` (модель MT — серверна; рядки 148–160) і ключ `translate_model` з `_load_config`/`_save_config`.
- У `_load_config`: `"tts_voice": cfg.get("speech.tts_voice", _TTS_VOICES[0])`, додати `"tts_speed": cfg.get("speech.tts_speed", 1.0)`; у `_save_config`: `cfg.set("speech.tts_voice", …)`, `cfg.set("speech.tts_speed", settings.get("tts_speed", 1.0))`.

`src/core/config.py`: видалити `get_wyoming_config` і `set_wyoming_config`.

```bash
git rm src/ui/widgets/model_tab.py src/ui/widgets/service_settings_dialog.py
```

- [ ] **Step 5: Тести, імпорт, ручна перевірка**

```bash
git add -A src/ui src/core/config.py tests/edge/test_edge_ui.py
nix develop -c pytest tests/edge -q
grep -rn "wyoming\|ModelTab\|ServiceSettingsDialog\|restart_all\|reconnect_ipc\|change_whisper_model" src/ui src/core/config.py || echo "clean"
nix develop -c python -m src.main --help
```
Expected: PASS; `clean`; довідка CLI без `--use-wyoming`. Ручна перевірка (на машині з PipeWire): `nix develop -c python3 -m src.main` → панель «Speech Services» показує ASR/MT/TTS; «Endpoints…» відкриває діалог.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(ui): ASR/MT/TTS status panel, endpoints dialog; drop Wyoming/model tab/service control"
```

---

### Task 12: Edge-пакет `rt-edge`, `checks.edge-pytest`, нові `packages`/`apps`

**Files:**
- Create: `nix/packages/edge.nix`, `nix/checks/edge-pytest.nix`
- Modify: `nix/packages/default.nix`, `flake.nix`

**Interfaces:**
- Consumes: `nix/packages/sources.nix` (`edgeSrc`), `nix/packages/edge-deps.nix`.
- Produces: `rtPkgs.edge` (`$out/bin/translator-ui`, `meta.mainProgram = "translator-ui"`); flake `packages.rt-edge`, `packages.default`, `apps.default`; `checks.rt-edge`, `checks.edge-pytest`.

- [ ] **Step 1: Падаюча перевірка**

`nix/checks/edge-pytest.nix`:

```nix
{ pkgs }:
let
  lib = pkgs.lib;
  py = pkgs.python313.withPackages (ps: (import ../packages/edge-deps.nix ps) ++ [ ps.pytest ]);
  testSrc = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [ ../../src ../../tests/edge ../../tests/conftest.py ];
  };
in
pkgs.runCommand "rt-edge-pytest" { nativeBuildInputs = [ py ]; } ''
  cp -r ${testSrc} work
  chmod -R u+w work
  cd work
  export HOME=$TMPDIR QT_QPA_PLATFORM=offscreen
  python -m pytest tests/edge -q -p no:cacheprovider
  touch $out
''
```

У `flake.nix` → `checks` додати `edge-pytest = import ./nix/checks/edge-pytest.nix { inherit pkgs; };` і `rt-edge = (rtPackagesFor pkgs).edge;`.

```bash
git add nix/checks/edge-pytest.nix flake.nix
nix build .#checks.x86_64-linux.edge-pytest -L
nix build .#checks.x86_64-linux.rt-edge -L
```
Expected: `edge-pytest` PASS (тести вже зелені в devShell; якщо падає — відмінність середовища, напр. HOME), `rt-edge` FAIL (`attribute 'edge' missing`).

- [ ] **Step 2: `edge.nix` і підключення**

`nix/packages/edge.nix`:

```nix
{ lib, python313Packages }:
let
  sources = import ./sources.nix { inherit lib; };
in
python313Packages.buildPythonApplication {
  pname = "rt-edge";
  version = "0.1.0";
  pyproject = true;
  src = sources.edgeSrc;
  build-system = [ python313Packages.setuptools ];
  dependencies = import ./edge-deps.nix python313Packages;
  # pyproject also declares rt-asr/rt-mt/rt-tts (sub-project 1); their modules are not in edgeSrc.
  postInstall = ''
    find $out/bin -mindepth 1 -maxdepth 1 ! -name translator-ui -delete
  '';
  pythonImportsCheck = [ "src.translation_system" "src.clients" "src.adapters.direct_adapter" ];
  meta = {
    description = "Real-time translator edge app (UI, capture, playback; remote ASR/MT/TTS)";
    mainProgram = "translator-ui";
    platforms = [ "x86_64-linux" ];
  };
}
```

`nix/packages/default.nix` — додати рядок `edge = pkgs.callPackage ./edge.nix { };` у результуючий attrset.

`flake.nix`:

```nix
      packages = forAllSystems (pkgs:
        let rt = rtPackagesFor pkgs; in
        flattenRt rt // { rt-edge = rt.edge; default = rt.edge; });
      apps = forAllSystems (pkgs: {
        default = { type = "app"; program = lib.getExe (rtPackagesFor pkgs).edge; };
      });
```
(легасі `flake-global/prod/packages.nix` більше не імпортується з `packages`; HM-легасі модуль сам його імпортує до Task 13).

- [ ] **Step 3: Збірка**

```bash
git add nix/packages/edge.nix nix/packages/default.nix flake.nix
nix build .#rt-edge -L && ls result/bin
nix build .#checks.x86_64-linux.edge-pytest .#checks.x86_64-linux.rt-edge -L
nix flake check -L
```
Expected: `result/bin` містить лише `translator-ui` (і `.translator-ui-wrapped`); checks і `flake check` зелені.

- [ ] **Step 4: Commit**

```bash
git commit -m "build(nix): rt-edge package and edge pytest check; flake packages switched to nix/"
```

---

### Task 13: `homeManagerModules.rt-translator` (edge only)

**Files:**
- Create: `nix/home-manager/rt-translator.nix`, `nix/checks/hm-edge.nix`
- Modify: `flake.nix`

**Interfaces:**
- Consumes: `rtPkgs.edge` (Task 12); `RT_EDGE_*` (Task 6).
- Produces: опції `rt-translator.{enable, package, mode, endpoints.{asr,mt,tts}, apiTokenFile, autostart, virtualSinks.enable}`; юніт `systemd.user.services.rt-app`; `home.sessionVariables.RT_EDGE_*`; drop-in `pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf`.

- [ ] **Step 1: Падаюча перевірка**

`nix/checks/hm-edge.nix`:

```nix
{ pkgs, self, home-manager }:
let
  lib = pkgs.lib;
  fakeEdge = pkgs.writeShellScriptBin "translator-ui" "exit 0";
  hm = extra: home-manager.lib.homeManagerConfiguration {
    inherit pkgs;
    modules = [
      self.homeManagerModules.rt-translator
      { home.username = "tester"; home.homeDirectory = "/srv/tester"; home.stateVersion = "25.11"; }
      extra
    ];
  };
  translate = hm {
    rt-translator = {
      enable = true;
      package = fakeEdge;
      mode = "translate";
      endpoints = { asr = "http://gpu:8091"; mt = "http://gpu:8092"; tts = "http://gpu:8093"; };
      apiTokenFile = "/run/user/1000/rt-token";
    };
  };
  transcribe = hm { rt-translator = { enable = true; package = fakeEdge; autostart = false; virtualSinks.enable = false; }; };
  missingMt = hm { rt-translator = { enable = true; package = fakeEdge; mode = "translate"; }; };
  legacy = hm { rt-translator.whisper.model = "small"; };
  evalFails = cfg: !(builtins.tryEval (builtins.deepSeq cfg.activationPackage.drvPath true)).success;
  env = cfg: cfg.config.systemd.user.services.rt-app.Service.Environment;

  failures = lib.runTests {
    testTranslateEnv = {
      expr = builtins.filter (lib.hasPrefix "RT_EDGE_") (env translate);
      expected = [
        "RT_EDGE_API_TOKEN_FILE=/run/user/1000/rt-token"
        "RT_EDGE_ASR_URL=http://gpu:8091"
        "RT_EDGE_MODE=translate"
        "RT_EDGE_MT_URL=http://gpu:8092"
        "RT_EDGE_TTS_URL=http://gpu:8093"
      ];
    };
    testSessionVariables = { expr = translate.config.home.sessionVariables.RT_EDGE_MODE; expected = "translate"; };
    testOnlyEdgeUnit = { expr = builtins.attrNames translate.config.systemd.user.services; expected = [ "rt-app" ]; };
    testNoSockets = { expr = translate.config.systemd.user.sockets; expected = { }; };
    testSinksDropIn = { expr = translate.config.xdg.configFile ? "pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf"; expected = true; };
    testTranscribeDefaults = {
      expr = {
        env = builtins.filter (lib.hasPrefix "RT_EDGE_") (env transcribe);
        install = transcribe.config.systemd.user.services.rt-app.Install;
        sinks = transcribe.config.xdg.configFile ? "pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf";
      };
      expected = { env = [ "RT_EDGE_ASR_URL=http://127.0.0.1:8091" "RT_EDGE_MODE=transcribe" ]; install = { }; sinks = false; };
    };
    testTranslateNeedsMtTts = { expr = evalFails missingMt; expected = true; };
    testLegacyOptionRemoved = { expr = evalFails legacy; expected = true; };
  };
in
pkgs.runCommand "rt-hm-edge-check" { activation = translate.activationPackage; } (
  if failures == [ ] then "touch $out"
  else ''
    echo "home-manager module failures:"
    echo ${lib.escapeShellArg (builtins.toJSON failures)}
    exit 1
  ''
)
```

`flake.nix` → `checks`: `hm-edge = import ./nix/checks/hm-edge.nix { inherit pkgs self home-manager; };`

```bash
git add nix/checks/hm-edge.nix flake.nix
nix build .#checks.x86_64-linux.hm-edge -L
```
Expected: FAIL (легасі-модуль: `rt-translator.package`/`mode` не існують).

- [ ] **Step 2: Модуль**

`nix/home-manager/rt-translator.nix`:

```nix
# Edge only: UI + capture + VAD + playback + PipeWire virtual sinks.
# Speech services run as NixOS system units (nixosModules.rt-speech), possibly on other hosts.
{ config, lib, pkgs, ... }:
let
  inherit (lib) mkOption mkEnableOption types;
  cfg = config.rt-translator;
  rtPkgs = import ../packages { inherit pkgs lib; };

  removed = name: lib.mkRemovedOptionModule [ "rt-translator" name ]
    "Speech services no longer run as Home Manager user units. Deploy them with the NixOS module services.rtSpeech (nixosModules.rt-speech) and set rt-translator.endpoints.*.";

  edgeEnv = { RT_EDGE_MODE = cfg.mode; RT_EDGE_ASR_URL = cfg.endpoints.asr; }
    // lib.optionalAttrs (cfg.endpoints.mt != null) { RT_EDGE_MT_URL = cfg.endpoints.mt; }
    // lib.optionalAttrs (cfg.endpoints.tts != null) { RT_EDGE_TTS_URL = cfg.endpoints.tts; }
    // lib.optionalAttrs (cfg.apiTokenFile != null) { RT_EDGE_API_TOKEN_FILE = cfg.apiTokenFile; };

  sinkModule = name: description: ''
    {
      name = libpipewire-module-null-sink
      args = {
        node.name = "${name}"
        node.description = "${description}"
        media.class = "Audio/Sink"
        stream.props = { audio.position = [ FL FR ]; }
      }
    }
  '';
in
{
  imports = map removed [ "whisper" "translate" "tts" "wyoming" ];

  options.rt-translator = {
    enable = mkEnableOption "real-time translator edge app";
    package = mkOption {
      type = types.package;
      default = rtPkgs.edge;
      defaultText = lib.literalExpression "rtSpeech.edge";
      description = "Edge app package providing translator-ui.";
    };
    mode = mkOption {
      type = types.enum [ "transcribe" "translate" ];
      default = "transcribe";
      description = "transcribe = ASR only; translate = ASR -> MT -> TTS (needs endpoints.mt and endpoints.tts).";
    };
    endpoints = {
      asr = mkOption { type = types.str; default = "http://127.0.0.1:8091"; description = "rt-asr base URL."; };
      mt = mkOption { type = types.nullOr types.str; default = null; example = "http://gpu-host:8092"; description = "rt-mt base URL."; };
      tts = mkOption { type = types.nullOr types.str; default = null; example = "http://gpu-host:8093"; description = "rt-tts base URL."; };
    };
    apiTokenFile = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "File with the bearer token for the speech services (outside the Nix store).";
    };
    autostart = mkOption { type = types.bool; default = true; description = "Start translator-ui with the graphical session."; };
    virtualSinks.enable = mkOption { type = types.bool; default = true; description = "Create rt_virtual_input/rt_virtual_output PipeWire sinks."; };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.mode != "translate" || (cfg.endpoints.mt != null && cfg.endpoints.tts != null);
        message = ''rt-translator: mode = "translate" requires endpoints.mt and endpoints.tts'';
      }
      {
        assertion = cfg.apiTokenFile == null || !(lib.hasPrefix builtins.storeDir cfg.apiTokenFile);
        message = "rt-translator.apiTokenFile must not point into the Nix store";
      }
    ];

    home.packages = [ cfg.package pkgs.pulseaudio ];
    home.sessionVariables = edgeEnv;

    systemd.user.services.rt-app = {
      Unit = {
        Description = "Real-time Translator (edge UI)";
        After = [ "graphical-session.target" "pipewire.service" ];
        PartOf = [ "graphical-session.target" ];
      };
      Service = {
        Type = "simple";
        ExecStart = lib.getExe cfg.package;
        Restart = "on-failure";
        RestartSec = 5;
        Environment = [
          "PATH=${lib.makeBinPath [ pkgs.pulseaudio ]}:/run/wrappers/bin:/etc/profiles/per-user/%u/bin:/run/current-system/sw/bin"
        ] ++ lib.mapAttrsToList (n: v: "${n}=${v}") edgeEnv;
        PassEnvironment = "WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS QT_QPA_PLATFORM";
      };
      Install = lib.optionalAttrs cfg.autostart { WantedBy = [ "graphical-session.target" ]; };
    };

    xdg.configFile."pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf" = lib.mkIf cfg.virtualSinks.enable {
      text = ''
        context.modules = [
        ${sinkModule "rt_virtual_input" "RT-Virtual-Input"}
        ${sinkModule "rt_virtual_output" "RT-Virtual-Output"}
        ]
      '';
    };
  };
}
```

`flake.nix`:

```nix
      homeManagerModules.rt-translator = import ./nix/home-manager/rt-translator.nix;
      homeManagerModules.default = self.homeManagerModules.rt-translator;
```

- [ ] **Step 3: Перевірка**

```bash
git add nix/home-manager/rt-translator.nix flake.nix
nix build .#checks.x86_64-linux.hm-edge -L
nix flake check -L
```
Expected: PASS. (Якщо `testTranslateEnv` падає через порядок — `mapAttrsToList` сортує ключі, `expected` уже відсортований.)

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(hm): edge-only rt-translator module (endpoints, mode, token file, sinks drop-in)"
```

---

### Task 14 (опційно, можна відкласти): WS-транспорт ASR

**Files:**
- Create: `src/clients/asr_stream.py`
- Modify: `src/translation_system.py`
- Test: `tests/edge/test_edge_asr_stream.py`

**Interfaces:**
- Consumes: `read_token` (Task 7), `TranslationSystem._handle_recognized` (Task 9), `EdgeSettings.asr_transport` (Task 6).
- Produces: `ws_url(base_url: str) -> str`; `AsrStreamClient(base_url, token_file=None, on_partial=None, on_final=None, open_timeout_s=3.0)` з `start(language: Optional[str], sample_rate: int = 16000)`, `send_pcm(data: bytes)`, `stop(timeout_s: float = 5.0)`; `on_final(text: str, language: Optional[str])`.

- [ ] **Step 1: Падаючі тести (локальний websockets-сервер)**

`tests/edge/test_edge_asr_stream.py`:

```python
import json
import threading

from websockets.sync.server import serve

from src.clients.asr_stream import AsrStreamClient, ws_url


def test_ws_url_conversion():
    assert ws_url("http://gpu:8091") == "ws://gpu:8091/v1/audio/stream"
    assert ws_url("https://proxy/asr/") == "wss://proxy/asr/v1/audio/stream"


def test_stream_roundtrip_with_token(tmp_path):
    tok = tmp_path / "t"
    tok.write_text("abc\n")
    received = {"bytes": 0}

    def handler(ws):
        received["auth"] = ws.request.headers.get("Authorization")
        received["start"] = json.loads(ws.recv())
        for msg in ws:
            if isinstance(msg, bytes):
                received["bytes"] += len(msg)
                ws.send(json.dumps({"type": "partial", "text": "при"}))
            elif json.loads(msg)["type"] == "stop":
                ws.send(json.dumps({"type": "final", "text": "привіт", "start": 0.0, "end": 1.0, "language": "uk"}))
                return

    with serve(handler, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        partials, finals = [], []
        client = AsrStreamClient(f"http://127.0.0.1:{port}", token_file=str(tok),
                                 on_partial=partials.append, on_final=lambda t, lang: finals.append((t, lang)))
        client.start("uk", 16000)
        client.send_pcm(b"\x00\x01" * 160)
        client.stop()
        server.shutdown()

    assert received["auth"] == "Bearer abc"
    assert received["start"] == {"type": "start", "language": "uk", "sample_rate": 16000}
    assert received["bytes"] == 320
    assert partials == ["при"]
    assert finals == [("привіт", "uk")]
```

```bash
git add tests/edge/test_edge_asr_stream.py
nix develop -c pytest tests/edge/test_edge_asr_stream.py -q
```
Expected: FAIL (`No module named 'src.clients.asr_stream'`).

- [ ] **Step 2: `src/clients/asr_stream.py`**

```python
"""WS /v1/audio/stream client (overview §4.3): JSON control + binary PCM16 frames."""
from __future__ import annotations

import json
import threading
from typing import Callable, Optional

from loguru import logger
from websockets.sync.client import connect

from .base import read_token


def ws_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    return url + "/v1/audio/stream"


class AsrStreamClient:
    def __init__(self, base_url: str, token_file: Optional[str] = None,
                 on_partial: Optional[Callable[[str], None]] = None,
                 on_final: Optional[Callable[[str, Optional[str]], None]] = None,
                 open_timeout_s: float = 3.0):
        self.url = ws_url(base_url)
        token = read_token(token_file)
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._on_partial = on_partial or (lambda text: None)
        self._on_final = on_final or (lambda text, lang: None)
        self._open_timeout = open_timeout_s
        self._ws = None
        self._reader: Optional[threading.Thread] = None

    def start(self, language: Optional[str], sample_rate: int = 16000) -> None:
        self._ws = connect(self.url, additional_headers=self._headers, open_timeout=self._open_timeout)
        self._ws.send(json.dumps({"type": "start", "language": language, "sample_rate": sample_rate}))
        self._reader = threading.Thread(target=self._read, daemon=True, name="rt-asr-stream")
        self._reader.start()

    def _read(self) -> None:
        try:
            for raw in self._ws:
                msg = json.loads(raw)
                if msg.get("type") == "partial":
                    self._on_partial(msg.get("text", ""))
                elif msg.get("type") == "final":
                    self._on_final(msg.get("text", ""), msg.get("language"))
        except Exception as e:
            logger.debug(f"ASR stream reader finished: {e}")

    def send_pcm(self, data: bytes) -> None:
        self._ws.send(data)

    def stop(self, timeout_s: float = 5.0) -> None:
        if self._ws is None:
            return
        try:
            self._ws.send(json.dumps({"type": "stop"}))
        except Exception as e:
            logger.debug(f"ASR stream stop send failed: {e}")
        if self._reader:
            self._reader.join(timeout=timeout_s)
        self._ws.close()
        self._ws = None
```

- [ ] **Step 3: Підключити в `TranslationSystem`**

У `src/translation_system.py`:
- Імпорт: `from .clients.asr_stream import AsrStreamClient`.
- Метод:

```python
    def _ws_pipeline_loop(self):
        """Streaming transport: mic 48 kHz -> 16 kHz PCM16 -> rt-asr WS; server does VAD."""
        import queue as _queue
        import sounddevice as sd
        from scipy.signal import resample_poly

        texts: _queue.Queue = _queue.Queue()
        stream_client = AsrStreamClient(
            self.settings.asr_url, token_file=self.settings.api_token_file,
            on_final=lambda text, lang: texts.put((text, lang)))
        audio_q: _queue.Queue = _queue.Queue()

        def _cb(indata, frames, cb_time, status):
            mono = indata[:, 0]
            self._audio_level_input = min(float(np.sqrt(np.mean(mono ** 2))) * 5.0, 1.0)
            audio_q.put(mono.copy())

        try:
            stream_client.start(None if self.source_lang == "auto" else self.source_lang, self.sample_rate)
        except Exception as e:
            self._record_error(ServiceError("asr", "unreachable", str(e)))
            self.is_running = False
            return
        stream = sd.InputStream(samplerate=48000, channels=1, dtype="float32", blocksize=1024,
                                callback=_cb, device=self._resolve_preferred_device())
        stream.start()
        try:
            while self.is_running:
                try:
                    block = audio_q.get(timeout=0.2)
                    if self.translation_enabled:
                        pcm = np.clip(resample_poly(block, 1, 3) * 32767, -32768, 32767).astype("<i2")
                        stream_client.send_pcm(pcm.tobytes())
                except _queue.Empty:
                    pass
                while not texts.empty():
                    self._handle_recognized(*texts.get_nowait())
        finally:
            stream.stop()
            stream.close()
            stream_client.stop()
            while not texts.empty():
                self._handle_recognized(*texts.get_nowait())
```
- У `start()`: `target=self._ws_pipeline_loop if self.settings.asr_transport == "ws" else self._pipeline_loop` для `self._capture_thread`.

- [ ] **Step 4: Тести і commit**

```bash
git add src/clients/asr_stream.py src/translation_system.py
nix develop -c pytest tests/edge -q
git commit -m "feat(edge): optional WebSocket ASR transport (services.asr.transport: ws)"
```
Expected: PASS.

---

### Task 15: Видалення легасі (код, юніти, Nix-дерева)

**Передумова для видалення `src/translate/` і `src/tts/`:** підпроєкт 1 злитий і `grep -rnE "src\.(translate|tts)\.|from \.\.(translate|tts)\b" src/services` порожній. Інакше ці дві теки лишити й видалити пізніше окремим комітом.

**Files:**
- Delete: `src/capture/`, `src/playback/`, `src/whisper/`, `src/common/ipc.py`, `src/pipeline/`, (за передумовою) `src/translate/`, `src/tts/`, `src/status_logger.py` (якщо невикористаний), `flake-global/`, `parts/`, `nixosModules/` (порожня), `flake.nix.old`, `tests/test_ipc_communication.py`, `tests/test_wyoming_integration.py`, `tests/test_integration.py`, `tests/test_service_status.py`, `tests/test_pipeline.py`, `tests/test_direct_adapter.py`, `tests/systemd-update/`
- Modify: `pyproject.toml`, `flake.nix`

**Interfaces:**
- Consumes: усе попереднє.
- Produces: фінальний `flake.nix` (нижче), чистий `src/`.

- [ ] **Step 1: Видалити**

```bash
git rm -r src/capture src/playback src/whisper src/common/ipc.py src/pipeline \
  flake-global parts flake.nix.old \
  tests/test_ipc_communication.py tests/test_wyoming_integration.py tests/test_integration.py \
  tests/test_service_status.py tests/test_pipeline.py tests/test_direct_adapter.py tests/systemd-update
# за передумовою:
git rm -r src/translate src/tts
grep -rn "status_logger" src --include=*.py | grep -v "src/ui/widgets/status_logger.py\|from .status_logger\|\.status_logger" || git rm src/status_logger.py
rmdir nixosModules 2>/dev/null || true
test -z "$(ls src/common 2>/dev/null)" && git rm -r --ignore-unmatch src/common
```

- [ ] **Step 2: `pyproject.toml`**

Секцію `[project.scripts]` звести до:

```toml
[project.scripts]
translator-ui = "src.main:main"
```
(рядки `rt-asr/rt-mt/rt-tts`, якщо їх додав підпроєкт 1, — лишити.)

- [ ] **Step 3: Фінальний `flake.nix`**

```nix
{
  description = "Real-time speech translation: edge UI + distributed ASR/MT/TTS services";

  # HINT only: CUDA variants (rt-*-gpu) are substitutable from cache.nixos-cuda.org.
  # Untrusted users' flake nixConfig is ignored — CUDA hosts get it via system
  # nix.settings in the infra (SP3 Task 7a). Never relied upon for correctness.
  nixConfig = {
    extra-substituters = [ "https://cache.nixos-cuda.org" ];
    extra-trusted-public-keys = [ "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M=" ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, ... }:
    let
      lib = nixpkgs.lib;
      systems = [ "x86_64-linux" ];
      pkgsFor = system: import nixpkgs { inherit system; config.allowUnfree = true; };
      forAllSystems = f: lib.genAttrs systems (system: f (pkgsFor system));
      rtLib = import ./nix/lib { inherit lib; };
      rtPackagesFor = pkgs: import ./nix/packages { inherit pkgs; };
      flattenRt = rt: lib.filterAttrs (_: drv: !(drv.meta.broken or false)) (lib.listToAttrs (lib.concatMap
        (service: lib.mapAttrsToList (variant: drv: lib.nameValuePair "rt-${service}-${variant}" drv) rt.${service})
        [ "asr" "mt" "tts" ]));
    in
    {
      lib.rtSpeech = rtLib;

      overlays.default = final: _prev: { rtSpeech = import ./nix/packages { pkgs = final; }; };

      packages = forAllSystems (pkgs:
        let rt = rtPackagesFor pkgs; in
        flattenRt rt // { rt-edge = rt.edge; default = rt.edge; });

      apps = forAllSystems (pkgs: {
        default = { type = "app"; program = lib.getExe (rtPackagesFor pkgs).edge; };
      });

      nixosModules = {
        rt-speech = import ./nix/nixos/rt-speech.nix;
        virtual-sinks = import ./nix/nixos/virtual-sinks.nix;
        default = self.nixosModules.rt-speech;
      };

      homeManagerModules = {
        rt-translator = import ./nix/home-manager/rt-translator.nix;
        default = self.homeManagerModules.rt-translator;
      };

      devShells = forAllSystems (pkgs:
        { default = import ./nix/devshell.nix { inherit pkgs; }; }
        # bench-cpu / bench-gpu / model-tools (SP5 Task 1); bench-gpu uses its own cudaSupport instance
        // lib.optionalAttrs (builtins.pathExists ./nix/bench-shells.nix)
          (import ./nix/bench-shells.nix { inherit nixpkgs; system = pkgs.stdenv.hostPlatform.system; }).devShells);

      checks = forAllSystems (pkgs:
        let rt = rtPackagesFor pkgs; in
        {
          flake-purity = import ./nix/checks/flake-purity.nix { inherit pkgs; src = self; };
          env-lines = import ./nix/checks/env-lines.nix { inherit pkgs rtLib; };
          overlay = import ./nix/checks/overlay.nix { inherit pkgs self; };
          rt-asr-cpu = rt.asr.cpu;
          rt-mt-cpu = rt.mt.cpu;
          rt-tts-cpu = rt.tts.cpu;
          rt-edge = rt.edge;
          edge-pytest = import ./nix/checks/edge-pytest.nix { inherit pkgs; };
          nixos-eval-gpu = import ./nix/checks/nixos-eval-gpu.nix { inherit pkgs self nixpkgs; };
          nixos-rt-speech = import ./nix/checks/nixos-rt-speech.nix { inherit pkgs self; };
          hm-edge = import ./nix/checks/hm-edge.nix { inherit pkgs self home-manager; };
        });
    };
}
```

- [ ] **Step 4: Перевірки відсутності залишків**

```bash
git add -A
grep -rnE "IPCClient|IPCServer|socket_path|systemctl|hybrid_whisper|wyoming_client|orchestrator" src --include=*.py || echo "src clean"
grep -rn "flake-global\|parts/\|nixosModules/" flake.nix nix/ || echo "nix clean"
nix develop -c pytest tests/edge -q
nix flake check -L
```
Expected: `src clean`, `nix clean`, PASS, `flake check` зелений. (Легасі-тести поза `tests/edge`, що імпортують видалені модулі, мають бути видалені в Step 1; `nix develop -c pytest tests -q --co` не повинен мати import errors для видалених модулів — якщо є, видалити відповідний тест.)

- [ ] **Step 5: Commit**

```bash
git commit -m "chore: remove IPC microservices, socket-activated units, flake-global/ and parts/"
```

---

### Task 16: Документація + фінальна верифікація git-flake

**Files:**
- Modify: `AGENT.md` (tracked), `CLAUDE.md` (local, gitignored — той самий текст), `plans/todo.md`

**Interfaces:**
- Consumes: фінальна структура (Task 15).

- [ ] **Step 1: Переписати розділи в `AGENT.md` і `CLAUDE.md`**

Замінити блок команд (`nix build .#capture …`) на:

```bash
nix develop                       # dev shell (edge + cpu service deps, pytest), creates PipeWire sinks
python3 -m src.main               # edge UI
nix run .                         # edge UI via flake app
nix build .#rt-asr-cpu            # speech bundles: rt-{asr,mt}-{cpu,gpu,rocm}, rt-tts-{cpu,gpu}
nix build .#rt-edge
nix flake check -L                # purity, env-lines, overlay, bundles, edge pytest, NixOS VM test, HM eval
nix develop -c pytest tests/edge -q
```

Замінити розділ «Nix flake structure» на:

```markdown
### Nix flake structure
- `flake.nix` — single flake (nixpkgs + home-manager, x86_64-linux only); no flake-utils/flake-parts.
- `nix/packages/default.nix { pkgs }` — the one function behind `packages`, `overlays.default` (`pkgs.rtSpeech`) and both modules; `speech-bundle.nix` builds `$out/bin/rt-<svc>`; `deps.nix` lists Python deps per service; `variant-pkgs.nix` selects the package set per variant (cpu = consumer pkgs; gpu = scoped `cudaSupport` instance served by cache.nixos-cuda.org; rocm = nixpkgs `ctranslate2.override { rocmSupport = true; }`). Never torch → torch-bin.
- `nix/lib/default.nix` — `lib.rtSpeech.{services,envLines,mkEnvFile,variantForDevice,healthCheckCommand}` (used by the infra for deploy-rs env files).
- `nix/nixos/rt-speech.nix` — `nixosModules.rt-speech` (`services.rtSpeech.{asr,mt,tts}`: system units, static users, `deployRsPath`, LoadCredential token, GPU sandbox, firewall on tailscale0).
- `nix/home-manager/rt-translator.nix` — edge only (`rt-translator.{mode,endpoints.*,apiTokenFile}` → `RT_EDGE_*`).
- `nix/checks/` — flake checks. The infra consumes this repo only as `github:DohuyaMa/realtime-translation`: everything a build needs must be tracked in git.
```

Таблицю «Python source layout» оновити: прибрати `src/capture/`, `src/whisper/`, `src/translate/`, `src/tts/`, `src/playback/`, `src/common/ipc.py`, `src/pipeline/orchestrator.py`; додати `src/clients/` (HTTP/WS clients + health monitor), `src/services/` (FastAPI services, sub-project 1); `src/core/runtime.py` → «`EdgeSettings`: `RT_EDGE_*` > config.yml `services` > defaults». Розділи «Service communication» і «Wyoming integration» замінити на:

```markdown
### Service communication
Edge (`TranslationSystem`) captures audio and runs Silero VAD locally, sends segments to `rt-asr` (`POST /v1/audio/transcriptions`, or WS `/v1/audio/stream` with `services.asr.transport: ws`), then — in `translate` mode — `rt-mt` (`POST /v1/translate`) and `rt-tts` (`POST /v1/audio/speech`), and plays TTS audio locally. Services are NixOS system units deployed via deploy-rs (see `docs/superpowers/specs/2026-10-06-distributed-speech-services-overview.md`).
```

- [ ] **Step 2: `plans/todo.md`**

У пункті «2. Nix packaging + NixOS module + edge HM» поставити `[x]` і дописати під секцією «GPU-перехід» рядок: `Рішення 2026-10-06 (reconciliation C2): GPU-варіанти — зі scoped cudaSupport-інстансу nixpkgs (cache.nixos-cuda.org), без torch-bin; TTS default — ORT-рушій підпроєкту 5 (torch KPipeline — fallback RT_TTS_ENGINE=torch); на старті GPU-хости деплоять rt-tts-cpu (див. spec nix-packaging §3.4).`

- [ ] **Step 3: Повна локальна верифікація**

```bash
git add AGENT.md plans/todo.md
nix flake check -L
nix build .#rt-asr-cpu .#rt-mt-cpu .#rt-tts-cpu .#rt-edge -L
for p in rt-asr-gpu rt-mt-gpu rt-tts-gpu rt-asr-rocm rt-mt-rocm; do nix eval --raw .#packages.x86_64-linux.$p.drvPath 2>&1 | tail -1; echo; done
```
Expected: усе зелене; п'ять `drvPath` (або явне «attribute missing» для rocm, якщо в Task 5 збірка nixpkgs-CT2 з `rocmSupport` впала і bundle позначено `broken` — тоді це вже записано в spec).

- [ ] **Step 4: Commit**

```bash
git commit -m "docs: describe nix/ flake layout, rt-speech module and edge HTTP clients"
```

- [ ] **Step 5: Доказ самодостатності git-flake (overview §4.5a)**

```bash
tmp=$(mktemp -d)
git clone --quiet "file://$PWD" "$tmp/rt"
git -C "$tmp/rt" status --porcelain | wc -l            # 0
nix flake check "$tmp/rt" -L
nix build "$tmp/rt#rt-mt-cpu" "$tmp/rt#rt-edge" -L --no-link
nix flake metadata "$tmp/rt" --json | jq -r '.locks.nodes | keys[]'
rm -rf "$tmp"
```
Expected: `0`; check і build зелені зі свіжого клону (тобто лише з закомічених файлів); у lock тільки `home-manager`, `nixpkgs`, `root`.

- [ ] **Step 6: (лише якщо користувач попросив push) перевірка з GitHub**

```bash
git push origin HEAD
rev=$(git rev-parse HEAD)
nix build "github:DohuyaMa/realtime-translation/$rev#rt-mt-cpu" -L --no-link
nix eval "github:DohuyaMa/realtime-translation/$rev#lib.rtSpeech.services.asr.port"
```
Expected: збірка з GitHub-ревізії успішна; `8091`. Без дозволу користувача на push — пропустити й зазначити у звіті, що Step 5 (клон) уже доводить самодостатність.

---

## Self-review (виконано)

1. **Покриття spec:** §5.1 flake → T1/T4/T12/T13/T15; §5.2–5.5 packages/bundle/deps/CT2 → T4/T5; §5.6 `lib.rtSpeech` → T1; §5.7 NixOS module → T2/T3; §5.8 HM → T13; §5.9 edge Python → T6–T11, T14; §5.9a git-flake → T1 (gitignore, `result`, purity) і T16 (clone/GitHub); §5.10 видалення → T10/T11/T15; §8 перевірки → checks у T1–T4/T12/T13; §3.4 TTS-gpu рішення → T4 (`variant-pkgs.nix`: scoped CUDA-інстанс, без torch-bin) і T5 (кеш); Open questions 1, 3 → T5; nixpkgs ≥ інфри (C1) → T0.
2. **Placeholders:** немає TBD/TODO; опційність Task 14 і fallback `meta.broken` для ROCm у Task 5 — явні рішення з критеріями.
3. **Узгодженість імен:** `EdgeSettings`/`resolve_edge_settings`/`get_runtime_config` (T6) = використання в T7, T9, T10, T11; `SpeechClients.by_name()` (T7) = T8/T9; `_handle_recognized(text, detected_language)` (T9) = T14; `refresh_services`/`reconfigure_services`/`set_tts` (T10) = T11; `rtLib.services.<svc>.{port,unit,stateDir}` (T1) = T2/T4; `quoteEnv` — T2 модуль і тест; `flattenRt` фільтрує `meta.broken` (T4) = механізм T5.
4. **Вимоги інфри R1–R7 (spec §15):** R1 → Task 4 `checks.overlay` (лише `rtSpeech`, незмінні `python313`/`ctranslate2`, `rtSpeech.lib.envLines`); R2/R3 → Task 1 `env-lines`, Task 2 `testTokenViaCredential`, Task 3 VM (root 0400 файл + літеральний шлях); R4 → Task 4 Step 5 (≤ 10 с, крос-реф підпроєкт 1); R5 → Task 2 `testDeployRsPath`/`testFirewallClosedByDefault`, Task 3 ConditionResult; R6 → Task 2/3 імена; R7 → Task 1 gitignore/purity, Task 16 Step 5–6.
5. **Review Focus:** кожен пункт має тест у задачі-власнику (T2/T3, T7, T9, T7, T10/T7).
