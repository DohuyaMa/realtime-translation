# Підпроєкт 2 — Nix packaging + NixOS module + edge (design)

Дата: 2026-10-06
Статус: draft, класифікація **ARCHITECTURAL** (делеговано; напрямок A затверджено; інтерактивних питань не було — припущення і відкриті питання записано явно). Узгоджено з іншими підпроєктами 2026-10-06 — overview §6–§7 (C1: nixpkgs bump + `ctranslate2.rocmSupport`; C2: scoped `cudaSupport`-інстанс замість torch-bin; C4: `nixConfig` лише підказка; C5: `httpx` у deps, MT `context` знято).
Базовий документ: `docs/superpowers/specs/2026-10-06-distributed-speech-services-overview.md` (далі — **overview**). Цей spec дотримується контракту overview §4; відхилення — у розділі 12.
План: `docs/superpowers/plans/2026-10-06-nix-packaging.md`.

---

## 1. Розуміння задачі

### 1.1 Що сказано (вхідні вимоги)
- Flake цього репо має віддавати: `packages.<system>.rt-{asr,mt,tts}-{cpu,gpu,rocm}` (bundle з `$out/bin/rt-<svc>`), `overlays.default` → `pkgs.rtSpeech.<svc>.<variant>`, `nixosModules.rt-speech` (`services.rtSpeech.{asr,mt,tts}`) за зразком `llama-service.nix`, `lib.rtSpeech.envLines` (єдина cfg→env функція, аналог `env-lines.nix`), `homeManagerModules.rt-translator` — **тільки edge** (UI + capture + playback + PipeWire sinks; `endpoints.{asr,mt,tts}`, `mode = transcribe|translate`, `apiTokenFile`).
- Edge-клієнт переходить з AF_UNIX IPC на HTTP/WS (httpx + websockets), підтримує режим «тільки транскрипція», TTS-аудіо грається локально на edge.
- Спланувати видалення старих socket-activated user-юнітів і старих IPC-мікросервісів.
- Розв'язати неузгодженість опису flake у `CLAUDE.md`.
- Зафіксувати рішення щодо `kokoro → torch → libnvshmem` для TTS gpu.
- Верифікація: `nix flake check`, `nix build` (мінімум cpu), nixosTest (VM) з fake-бекендом, eval-перевірка HM-модуля.
- Nix-only: жодних pip/venv.

### 1.2 Що припущено (можна виправити)
- **A1.** Підпроєкт 1 дає `src/services/{asr,mt,tts}/__main__.py` (запуск `python -m src.services.<svc>`), читає env з overview §4.2, піднімає `/health` одразу, модель вантажить у фоні. Bundle викликає саме `python -m src.services.<svc>`, тож від console scripts у `pyproject.toml` не залежить.
- **A2.** Перелік Python-залежностей кожного сервісу остаточно визначають підпроєкти 1 і 5 (+ `httpx` для воркера підпроєкту 4); цей spec задає **місце** (`nix/packages/deps.nix`) і перелік (§5.4). Розбіжність module-level імпортів ловить import-check під час збірки bundle; ліниві імпорти — slow-тести/смоук підпроєктів 1 і 5.
- **A3.** TTS `POST /v1/audio/speech` з `response_format: "wav"` віддає **PCM16 mono WAV** (edge декодує stdlib `wave`, без `soundfile`). **Підтверджено** SP1 spec §8.1 (вихід — PCM16LE mono, `X-Sample-Rate`).
- **A4.** Один bearer-токен на всі три сервіси з погляду edge (`apiTokenFile` — один). На сервері кожен сервіс має свій `apiTokenFile`, але значення може збігатися.
- **A5.** Цільова платформа — тільки `x86_64-linux` (darwin/aarch64 у поточному flake фактично не працюють: PipeWire, CUDA).
- **A6.** `ProtectSystem=strict` + `StateDirectory` достатньо для HF-кешу в `<stateDir>/models/hf`.

### 1.3 Критерії успіху
1. `nix flake check -L` проходить на x86_64-linux (включно з VM-тестом і HM eval-check).
2. `nix build .#rt-{asr,mt,tts}-cpu .#rt-edge` збирається тільки з `cache.nixos.org` + локальна збірка нашого коду (без torch/CUDA source-build); `rt-*-gpu` — з `cache.nixos-cuda.org` (scoped `cudaSupport`-інстанс, §5.5), без torch-bin.
3. `nix eval .#packages.x86_64-linux.rt-{asr,mt}-{gpu,rocm}.drvPath` і `rt-tts-gpu.drvPath` інстанціюються.
4. Інфра (підпроєкт 3) може: підключити overlay, імпортувати `nixosModules.rt-speech`, згенерувати per-host `env` через `lib.rtSpeech.mkEnvFile`, деплоїти bundle у `/var/lib/rt-<svc>/current` — без знання внутрішньої структури цього репо.
5. Edge у режимі `transcribe` працює лише з `rt-asr`; у режимі `translate` — з усіма трьома; жодного `AF_UNIX`/`systemctl --user` для speech-сервісів.
6. Flake самодостатній як **git-flake** (overview §4.5a): `nix flake check` і `nix build .#rt-mt-cpu` проходять зі свіжого `git clone` (а після push — `nix build github:DohuyaMa/realtime-translation/<rev>#rt-mt-cpu`), без untracked-файлів, абсолютних локальних шляхів і `--impure`.

---

## 2. Поточний стан (результати дослідження)

### 2.1 Flake — фактична структура vs `CLAUDE.md`
- `CLAUDE.md`/`AGENT.md` кажуть: «`flake.nix` – thin wrapper, що делегує в `flake-global/` (flake-parts)». **Це застаріло.** Коміт `61bb7fb "remove flake-parts and sub-flake, simplify to single flake.nix"` прибрав `flake-global/flake.nix`. Зараз:
  - кореневий `flake.nix` — один flake на **flake-utils** (`eachSystem` з 4 системами, включно з darwin), просто `import`-ить plain-Nix файли з `flake-global/` (`prod/packages.nix`, `dev/devshell.nix`, `home-manager-module.nix`);
  - `flake-global/flake.lock` — **мертвий** залишок (містить flake-parts, `nixos-25.11`; ніким не читається);
  - `flake-global/parts/python/*.nix` — живі (через `prod/packages.nix`), кожен пакет будує **весь репо** (`src = ../../..`) з кореневого `pyproject.toml`;
  - кореневий `parts/` (`runtime/`, `systemd/`, `pipewire/`) — **мертвий код**, ніде не імпортується;
  - `nixosModules/virtual-sinks.nix` — живий, експортується як `nixosModules.virtual-sinks`;
  - `flake.nix.old` — мертвий.
- `flake-global/prod/apps.nix` — не використовується (apps задані inline у `flake.nix`).
- HM-модуль одночасно створює sinks **двома** способами (pactl oneshot `rt-virtual-sinks` і `pipewire.conf.d` drop-in) → дублікати sinks.
- devShell містить `pip` і `virtualenv` — порушення Nix-only політики.

### 2.1a Git-flake гігієна (overview §4.5a: інфра бере репо лише як `github:DohuyaMa/realtime-translation`)
- `.gitignore` містить шаблонні **неанкеровані** `lib/`, `env/`, `var/`, `data/`, `build/`, `dist/` тощо. Перевірено: `git check-ignore nix/lib/default.nix` → **ігнорується** правилом `lib/`. Без виправлення `nix/lib/` не потрапить у git → git-flake не збереться на жодній іншій машині (локально `nix build` на брудному дереві теж його не побачить, бо Nix бере лише tracked-файли).
- `result` (симлінк у `/nix/store/…-pysilero-vad`) **закомічений** у git, хоча `result` є в `.gitignore`.
- `CLAUDE.md` у `.gitignore` («local only») — не потрапляє в git; трекається лише `AGENT.md` (той самий текст).
- `src/services/` і майбутні `tests/edge/` не ігноруються — ок. Моделі (`/models/`, `*.onnx`, `*.pt`) ігноруються — ок, моделі провіжняться сервісами в `<stateDir>/models`.

### 2.2 nixpkgs (lock на момент дослідження: `89570f2…`, `26.11pre`; **bump до ≥ ревізії інфри `7a0f122…` — план Task 0**, reconciliation C1)
| Пакет | Стан |
|---|---|
| `ctranslate2` (C++) 4.8.0 → 4.8.2 | у старому lock `89570f2` — лише `withCUDA`/`withCuDNN`, **без** `rocmSupport`; у nixpkgs інфри (`7a0f122`, 4.8.2) **є** `rocmSupport ? config.rocmSupport` + `rocmGpuTargets` + hip-бібліотеки (`WITH_HIP`). Оскільки overlay будується з `pkgs` споживача (інфри), а Task 0 піднімає lock цього репо, ROCm-варіант = `ctranslate2.override { rocmSupport = true; rocmGpuTargets = …; }` |
| `python3Packages.ctranslate2` | аргумент `ctranslate2-cpp` → GPU-варіант через `.override { ctranslate2-cpp = …; }` |
| `faster-whisper` 1.2.1 | аргумент `ctranslate2` → підміняється тим самим способом; тягне CPU `onnxruntime` (Silero VAD) — це ок |
| `kokoro`, `misaki[en]` | **жорстко** залежать від `torch` (+ `transformers`, `spacy`) |
| `torch-bin` 2.12.0 | з CUDA вимагає `cudaPackages_13` (з 12.9 — `unsupported-cuda-version`), і **все одно** тягне source-build `cuda13.2-libnvshmem` + `nccl` (dry-run: 102 derivations); override міняє хеші → промах кешу (SP5 F8). **Не використовуємо.** Натомість з глобальним `cudaSupport = true` (окремий інстанс) `torch`, `libnvshmem`, `ctranslate2`, `onnxruntime` є в `cache.nixos-cuda.org` (SP5 F7) |
| `onnxruntime` 1.26 (1.27.1 в інфрі) | має `cudaSupport`, `rocmSupport` (з 1.23 upstream — лише MIGraphX EP, SP5 F11), `ncclSupport` |
| `kokoro-onnx` | **відсутній** у nixpkgs |
| `fastapi`, `uvicorn`, `httpx`, `websockets` 16, `python-multipart`, `respx`, `pytest-asyncio` | є |
| CPU-замикання (kokoro + torch CPU + faster-whisper + CT2 + fastapi) | повністю substitutable з `cache.nixos.org` (dry-run: 0 важких збірок) |
| CUDA CT2 + faster-whisper | 26 derivations: розпаковка CUDA redistributables + компіляція CT2 з nvcc; без torch/nvshmem |

Машина розробника: `/dev/kvm` є, `system-features` містить `kvm nixos-test` → VM-тести можливі.

### 2.3 Edge-код
- Capture (sounddevice 48k → 16k), Silero VAD і playback (`sd.play`) **вже** працюють у процесі UI (`src/translation_system.py`). Мережеві виклики потрібні лише для ASR/MT/TTS.
- `TranslationSystem` тримає `IPCClient` для capture/translate/tts/playback (+ власний `WhisperSocketClient`), перевіряє `os.path.exists(socket)`.
- `DirectAdapter` керує `systemctl --user` юнітами, Wyoming, завантаженням whisper-моделей (`ModelManager`) — усе це на edge більше не має сенсу.
- UI: `ServiceStatusPanel` (whisper/translate/tts/wyoming + Start/Stop/Restart), `ServiceSettingsDialog` (Wyoming routing, зміна whisper-моделі), `ModelTab` (локальні моделі/Wyoming) — прив'язані до старої моделі.
- MT зараз отримує `context` (попередні фрази); контракт `/v1/translate` поля `context` не має.

### 2.4 Зразок у swarm-nix (read-only)
- `applications/llm/package.nix`: `{ pkgs, lib, cudaSupport ? false, rocmSupport ? false, rocmGpuTarget ? "gfx900" }` → `symlinkJoin [ pythonEnv appDir ]`.
- `common/modules/llm/llama-service.nix`: system-юніт, static user, `ExecStart` через `deployRsPath`, `Environment = mkEnvLines cfg`, `EnvironmentFile = "-${deployRsPath}/env"` (перекриває `Environment=`), hardening, GPU `DeviceAllow`.
  - Нюанс: `DeviceAllow = [ "/dev/nvidiactl" "rw" … ]` генерує **окремі** рядки `DeviceAllow=/dev/nvidiactl` і `DeviceAllow=rw` (останній невалідний; працює випадково, бо без прав = `rwm`). Ми пишемо `"/dev/nvidiactl rw"`.
  - Firewall відкривається глобально (`allowedTCPPorts`), а не на інтерфейсі — overview §4.5 вимагає `tailscale0`.
  - `LimitNPROC = 64` рахує **потоки** користувача — небезпечно для CT2/torch з багатьма intra-op потоками; не копіюємо.
- `applications/lib/mkDeployProfiles.nix`: `symlinkJoin [ app.package (mkEnvFile hostCfg) ]` → bundle **не повинен** мати файл `$out/env`.
- `flake.nix` передає готовий `pkgs` у `nixosSystem` → наш модуль **не може** покладатися на те, що overlay застосовано.
- `common/overlays/cuda.nix` (вимкнений) глобально міняє `torch` на `torch-bin` з `cudaPackages_13` — ми цього **не** повторюємо (ні глобально, ні scoped): GPU-варіанти беруться з окремого `cudaSupport`-інстансу, створеного всередині `nix/packages` (§5.5).

---

## 3. Розглянуті підходи

### 3.1 Організація flake
| Підхід | Плюси | Мінуси |
|---|---|---|
| **(рекомендовано) A. Один flake, plain-Nix дерево `nix/`, одна функція `pkgs → rtSpeech` спільна для packages/overlay/modules** | одна точка правди; модуль працює з `pkgs` хоста без overlay; без нових inputs | треба перенести/видалити `flake-global/`, `parts/` |
| B. Повернути flake-parts | модульність | користувач щойно свідомо прибрав flake-parts (`61bb7fb`) |
| C. Окремий sub-flake для сервісів | інфра тягне менший input | два lock-файли, дрейф nixpkgs, той самий мертвий-lock сценарій, що вже був |

### 3.2 Форма bundle
| Підхід | Плюси | Мінуси |
|---|---|---|
| **(рекомендовано) a. `python.withPackages deps` + `lib.fileset`-джерела + `makeWrapper` → `$out/bin/rt-<svc>`** | точно контракт `bin/rt-<svc>`; зміни UI не перебудовують сервіси; без setuptools | власний маленький builder |
| b. Як llama: `symlinkJoin [ pythonEnv appDir ]`, `ExecStart = bin/python -m …` | 1:1 зі зразком | немає `bin/rt-<svc>` (контракт), WorkingDirectory-магія |
| c. `buildPythonApplication` з кореневого `pyproject.toml` на кожен сервіс | знайомо | у `bin/` всі console scripts (UI тощо) з відсутніми deps; будь-яка зміна в `src/` = redeploy усіх сервісів |

### 3.3 Транспорт ASR на edge
| Підхід | Плюси | Мінуси |
|---|---|---|
| **(рекомендовано, default) HTTP `POST /v1/audio/transcriptions` на кожен VAD-сегмент** | зберігає перевірений edge-VAD; stateless; простий retry/timeout | немає partial-результатів |
| WS `/v1/audio/stream` (сервер робить VAD) | partials, менша латентність | складніше; залежить від якості серверного endpointing (підпроєкт 1) |

Рішення: реалізуємо обидва клієнти, default — `http`; `ws` вмикається конфігом `services.asr.transport: ws`. WS-завдання в плані — останнє серед edge-задач і може бути відкладене, не блокуючи решту.

### 3.4 TTS gpu (kokoro → torch → libnvshmem)
| Варіант | Оцінка |
|---|---|
| torch (source, CUDA) | години збірки, OOM — відкинуто |
| torch-bin (cu13) scoped у TTS-gpu bundle | override ламає хеші → `libnvshmem`/`nccl` source-build; `cuda-maintainers.cachix.org` їх не має (SP5 F7/F8) — **відкинуто** (reconciliation C2) |
| **окремий інстанс nixpkgs з `config.cudaSupport = true` (дефолтні `cudaCapabilities`) + substituter `cache.nixos-cuda.org`** | усі важкі пакети (torch, libnvshmem, CT2, ORT) — з кешу (SP5 F7); інстанс створюється в `nix/packages/variant-pkgs.nix` з `pkgs.path` споживача, глобальний `pkgs` не змінюється (R1) — **обрано** |
| kokoro-onnx (PyPI) | не в nixpkgs, гірший G2P — відкинуто (SP5 T3) |
| власний тонкий ORT-рушій + misaki G2P через `kokoro.KPipeline(model=False)` | реалізує підпроєкт 5 (T4) — **default-рушій TTS**; torch `KPipeline` — fallback `RT_TTS_ENGINE=torch` |

**Рішення (зафіксовано, reconciliation C2 — SP5 має пріоритет):**
1. `rt-tts-cpu` — `onnxruntime` (CPU) + `kokoro` (для misaki G2P; torch CPU лише імпортується) з `cache.nixos.org`. **Рекомендований варіант для всіх хостів на старті**, включно з GPU-хостами (VRAM-first: VRAM лишається для ASR/MT).
2. `rt-tts-gpu` — визначається (контракт), будується з того самого scoped `cudaSupport`-інстансу, що й `rt-{asr,mt}-gpu` (ORT CUDA EP; torch теж CUDA, але з кешу). Не входить у `checks`; збірка — на хостах із `cache.nixos-cuda.org` у системних `nix.settings` (підпроєкт 3, Task 7a). Перевірка — `drvPath` eval і cache-hit (план Task 5).
3. `rt-tts-rocm` — **не експортується** (D2): ORT ROCm EP видалено в 1.23, MIGraphX EP на gfx90c не перевірений; може з'явитись пізніше як експериментальний через `onnxruntime.override { rocmSupport = true; }`. Причина — не torch.
4. Override `torch → torch-bin` заборонено (SP5 D7).

---

## 4. Архітектура рішення

```
flake.nix  (inputs: nixpkgs, home-manager; systems = [ "x86_64-linux" ])
│
├─ nix/packages/default.nix   { pkgs } → { asr.{cpu,gpu,rocm}, mt.{…}, tts.{cpu,gpu}, edge, mkBundle }
│    ├─ sources.nix            lib.fileset: speechSrc (src/__init__.py + src/services/**), edgeSrc (src/** − src/services)
│    ├─ deps.nix               { pkgs, service } → ps → [ python deps ]   ← єдине місце залежностей
│    ├─ variant-pkgs.nix       { pkgs, variant, rocmGpuTargets } → { pkgs, python } (cpu | scoped cudaSupport-інстанс | CT2 rocmSupport)
│    ├─ speech-bundle.nix      mkBundle { service, variant, … } → $out/bin/rt-<svc>
│    └─ edge.nix               buildPythonApplication → $out/bin/translator-ui
│
├─ nix/lib/default.nix        { lib } → { envLines, mkEnvFile, services (port/stateDir/unit), variantForDevice, healthCheckCommand }
├─ nix/nixos/rt-speech.nix    nixosModules.rt-speech  (services.rtSpeech.{asr,mt,tts})
├─ nix/nixos/virtual-sinks.nix nixosModules.virtual-sinks (перенесено без змін)
├─ nix/home-manager/rt-translator.nix  homeManagerModules.rt-translator (edge only)
├─ nix/devshell.nix           (+ nix/bench-shells.nix — devShells підпроєкту 5)
└─ nix/checks/{flake-purity,env-lines,nixos-rt-speech,nixos-eval-gpu,hm-edge,edge-pytest}.nix
```

Потік залежностей: `packages`, `overlays.default`, `nixosModules.rt-speech` і `homeManagerModules.rt-translator` **усі** викликають `nix/packages/default.nix` з `pkgs` споживача. Тому інфра, що передає свій `pkgs`, отримує збірку проти **свого** nixpkgs (як llama), а overlay — лише зручність для `registry.nix` (`{ pkgs, lib }`).

---

## 5. Компоненти

### 5.1 `flake.nix`
- Inputs: `nixpkgs` (unstable, як зараз), `home-manager` (follows). **`flake-utils` прибираємо** (`lib.genAttrs [ "x86_64-linux" ]`).
- `pkgsFor = system: import nixpkgs { inherit system; config.allowUnfree = true; }` — **без** глобального `cudaSupport`/`rocmSupport`; GPU вмикається точково всередині bundle (`variant-pkgs.nix`: CUDA — окремий scoped інстанс з `pkgs.path`, ROCm — override CT2).
- `nixConfig.extra-substituters = [ "https://cache.nixos-cuda.org" ]` + ключ `cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M=` — **лише підказка** (для не-trusted користувача ігнорується); на CUDA-хостах substituter ставить інфра в системних `nix.settings` (підпроєкт 3).
- Lock `nixpkgs` — не старіший за ревізію інфри (план Task 0; C1).
- Виходи:
  - `packages.x86_64-linux`: `rt-asr-cpu`, `rt-asr-gpu`, `rt-asr-rocm`, `rt-mt-cpu`, `rt-mt-gpu`, `rt-mt-rocm`, `rt-tts-cpu`, `rt-tts-gpu`, `rt-edge`, `default = rt-edge`.
  - `apps.x86_64-linux.default` → `rt-edge/bin/translator-ui`.
  - `overlays.default = final: prev: { rtSpeech = import ./nix/packages { pkgs = final; }; }`.
  - `nixosModules.rt-speech`, `nixosModules.virtual-sinks`, `nixosModules.default = rt-speech`.
  - `homeManagerModules.rt-translator`, `homeManagerModules.default = rt-translator`.
  - `lib.rtSpeech = import ./nix/lib { inherit (nixpkgs) lib; }` (system-незалежно).
  - `checks.x86_64-linux`: `flake-purity`, `env-lines`, `rt-asr-cpu`, `rt-mt-cpu`, `rt-tts-cpu`, `rt-edge`, `edge-pytest`, `nixos-rt-speech` (VM), `nixos-eval-gpu`, `hm-edge`.
  - `devShells.x86_64-linux.default`.
  - `formatter` — не додаємо (YAGNI).

### 5.2 `nix/packages/default.nix`
```nix
{ pkgs, lib ? pkgs.lib }:
let mkBundle = import ./speech-bundle.nix { inherit pkgs lib; };
in {
  asr  = { cpu = mkBundle { service = "asr"; variant = "cpu"; }; gpu = …; rocm = …; };
  mt   = { cpu = …; gpu = …; rocm = …; };
  tts  = { cpu = …; gpu = …; };          # без rocm (D2: ORT ROCm не перевірений)
  edge = pkgs.callPackage ./edge.nix { };
  inherit mkBundle;                       # для кастомних GPU-арх: mkBundle { …; rocmGpuTargets = [ "gfx1030" ]; }
  lib = import ../lib { inherit lib; };   # pkgs.rtSpeech.lib.envLines — alias lib.rtSpeech (infra R1)
}
```

### 5.3 `mkBundle` (`speech-bundle.nix`)
Аргументи: `{ service, variant, rocmGpuTargets ? [ "gfx900" ] }`. CUDA-архітектури — **дефолтні** `cudaCapabilities` scoped-інстансу (будь-яка зміна → промах `cache.nixos-cuda.org`; окремий параметр — YAGNI).
- `vp = import ./variant-pkgs.nix { … }`; `pythonEnv = vp.python.withPackages (import ./deps.nix { pkgs = vp.pkgs; inherit service; })`. Жодних override `torch` (C2).
- Джерела: `speechSrc` з `sources.nix` (`lib.fileset.toSource { root = ../..; fileset = unions [ ../../src/__init__.py ../../src/services ]; }`). Якщо підпроєкт 1 використовує інші модулі з `src/`, він додає їх у `speechSrc` (import-check це виявить).
- Результат (`stdenvNoCC.mkDerivation`, `nativeBuildInputs = [ makeWrapper ]`):
  ```
  $out/bin/rt-<svc>            makeWrapper ${pythonEnv}/bin/python
                                 --add-flags "-m src.services.<svc>"
                                 --set PYTHONPATH ${speechSrc}
                                 --set PYTHONNOUSERSITE 1
                                 [gpu]  --prefix LD_LIBRARY_PATH : /run/opengl-driver/lib
                                 [tts]  --prefix PATH : ${espeak-ng}/bin
  $out/bin/rt-models             makeWrapper … --add-flags "-m src.services.common.models_cli"   (CLI провізіонування моделей SP5 §4.7)
  $out/share/rt-speech/info.json   {"service":"asr","variant":"gpu","port":8091}
  ```
  Жодного `$out/env` (його додає інфра через `symlinkJoin`).
- `doInstallCheck = true`: `installCheckPhase` імпортує `src.services.<svc>` (`${pythonEnv}/bin/python -c "import src.services.<svc>"` з `PYTHONPATH`) — аналог llama-валідації. Імпорт не має торкатися GPU.
- `passthru = { inherit service variant; port = …; pythonEnv; }`, `meta.mainProgram = "rt-<svc>"`, `meta.platforms = [ "x86_64-linux" ]`.

### 5.4 `deps.nix` (перелік узгоджено з підпроєктами 1, 4, 5)
| Сервіс | Python deps | Варіанти |
|---|---|---|
| спільні | `fastapi`, `uvicorn`, `websockets` (WS у uvicorn), `pydantic`, `python-multipart`, `numpy`, `loguru`, `soundfile`, `scipy` (SP1 `common/audio.py`), `huggingface-hub` (SP5 `model_store`), **`httpx`** (runtime-залежність воркера підпроєкту 4, reconciliation C5) | — |
| asr | `faster-whisper`, `pysilero-vad` (SP1 серверний VAD) | cpu / gpu (CUDA-інстанс) / rocm (CT2 `rocmSupport`) |
| mt | `ctranslate2`, `sentencepiece` — **без** `transformers`/`torch` (SP5 §4.5) | cpu / gpu / rocm |
| tts | `onnxruntime`, `kokoro` (misaki G2P; torch лише імпортується), `en_core_web_sm` (wheel з `flake-global/parts/python/tts.nix`, переноситься) | cpu / gpu (CUDA-інстанс; ORT CUDA EP) |

`transformers` — лише в devShell (legacy `TransformersNllbBackend` підпроєкту 1 і `model-tools` підпроєкту 5), у bundle не потрапляє: bundle `rt-mt` працює лише з CT2-бекендом підпроєкту 5 (SP5 Task 12a робить його default).

### 5.5 `variant-pkgs.nix`
- `cpu`: `pkgs` споживача як є (`ctranslate2` OpenBLAS + Ruy, CPU `onnxruntime`) — усе з `cache.nixos.org`.
- `gpu`: `cudaPkgs = import pkgs.path { system = pkgs.stdenv.hostPlatform.system; config = { allowUnfree = true; cudaSupport = true; }; }` — окремий інстанс **з nixpkgs споживача**, дефолтні `cudaCapabilities` (інакше промах кешу), свіжий `config` (не перевикористовуємо evaluated `pkgs.config`). Python-пакети беруться з `cudaPkgs.python313` без жодних override → хеші збігаються з `cache.nixos-cuda.org` (SP5 F7). Глобальний `pkgs` інфри не змінюється (R1).
- `rocm`: `pkgs.ctranslate2.override { rocmSupport = true; inherit rocmGpuTargets; }` (штатна опція nixpkgs ≥ `7a0f122`, C1) + scoped `python313.override { packageOverrides = …ctranslate2.override { ctranslate2-cpp = ct2Rocm; }; }` → `faster-whisper` підхоплює той самий CT2. Власний `WITH_HIP` overrideAttrs і time-box **прибрано**. Варіант «experimental» лише з апаратних причин (NOTE17 gfx90c офіційно не підтримується → `HSA_OVERRIDE_GFX_VERSION=9.0.0`); якщо nixpkgs-збірка впаде — `meta.broken` (план Task 5).
- На ROCm Python-API CT2 використовує device-рядок `"cuda"` (HIP-збірка мапить його) — вимога до підпроєкту 1 (уже в SP1 §4.5: `RT_DEVICE=rocm` → `device="cuda"`).

### 5.6 `lib.rtSpeech` (`nix/lib/default.nix`)
```nix
{ lib }: rec {
  services = {
    asr = { port = 8091; unit = "rt-asr"; stateDir = "/var/lib/rt-asr"; };
    mt  = { port = 8092; unit = "rt-mt";  stateDir = "/var/lib/rt-mt";  };
    tts = { port = 8093; unit = "rt-tts"; stateDir = "/var/lib/rt-tts"; };
  };
  variantForDevice = { cpu = "cpu"; cuda = "gpu"; rocm = "rocm"; };
  envLines = service: cfg: [ … ];                      # див. нижче
  mkEnvFile = { pkgs, service, cfg }: pkgs.writeTextDir "env" (lib.concatLines (envLines service cfg));
  healthCheckCommand = service: cfg: "curl -sf http://127.0.0.1:${toString cfg.port}/health > /dev/null";
}
```
`envLines service cfg` (порядок фіксований; значення з `\n` → `throw`):
| Рядок | Умова |
|---|---|
| `PYTHONUNBUFFERED=1` | завжди |
| `RT_HOST=`, `RT_PORT=`, `RT_DEVICE=`, `RT_MODEL_DIR=`, `HF_HOME=<modelDir>/hf`, `RT_LOG_LEVEL=` | завжди |
| `RT_MODEL=` | `cfg.model != null` |
| `RT_COMPUTE_TYPE=` | `cfg.computeType != null` (asr, mt) |
| `HSA_OVERRIDE_GFX_VERSION=` | `device == "rocm" && rocmGfxVersion != ""` |
| asr: `RT_ASR_LANGUAGE=` (якщо не null), `RT_ASR_BEAM_SIZE=` (якщо не null), `RT_ASR_VAD=true\|false` | service = asr |
| mt: `RT_MT_SOURCE_LANG=`, `RT_MT_TARGET_LANG=`, `RT_MT_NUM_BEAMS=` (якщо не null) | service = mt |
| tts: `RT_TTS_VOICE=`, `RT_TTS_SPEED=`, `RT_TTS_SAMPLE_RATE=` | service = tts |
| `RT_API_TOKEN_FILE=/run/credentials/rt-<svc>.service/api-token` | `cfg.apiTokenFile != null` |
| `K=V` з `cfg.extraEnv` (відсортовано за ключем) | завжди, в кінці (перекриває попередні при однакових ключах — systemd бере останнє) |

`RT_API_TOKEN_FILE=/run/credentials/rt-<svc>.service/api-token` — емітується **літеральним шляхом** (не `%d`: systemd не розгортає specifiers у вмісті `EnvironmentFile`), коли `cfg.apiTokenFile != null`; сам секрет потрапляє туди через `LoadCredential` юніта (5.7). Значення в `envLines` — завжди лише шляхи, ніколи секрети (infra R2/R3).

Свідомо **не** емітується: `RT_DJANGO_*`/`RT_WORKER_*` — у v1 підпроєкт 4 задає їх через `extraEnv` (окремих опцій модуля немає; секрет — файл, власник `rt-<svc>`, див. SP4 plan Task C1). Дефолти моделей (`model = null`) і device-залежні дефолти beam-ів (`beamSize`/`numBeams = null`) свідомо не дублюються в Nix — єдине джерело дефолтів — Python-сервіс (підпроєкти 1/5). Значення `model` — alias з реєстру SP5 (`whisper-large-v3-turbo`, `nllb-200-distilled-600M-ct2-int8`, `kokoro-82m-v1.0-onnx`, …) або абсолютний шлях (SP5 D5).

Функція бере тільки `lib`, читає лише ці атрибути cfg і не торкається `cfg.package` → її можна викликати з `nixosConfigurations.<host>.config.services.rtSpeech.<svc>` в інфрі без збірки пакета.

### 5.7 `nixosModules.rt-speech`
Генерується однією функцією `mkServiceModule service` для трьох сервісів (без копіпасти).

**Опції `services.rtSpeech.<svc>`:**
| Опція | Тип / default | Примітка |
|---|---|---|
| `enable` | bool / false | |
| `package` | package / `(import ../packages { inherit pkgs; }).<svc>.<variantForDevice.${device}>` | використовується лише при `deployRsPath == ""` (лінива — не збирається в deploy-rs режимі) |
| `deployRsPath` | str / `""`, example `/var/lib/rt-<svc>/current` | |
| `host` | str / `"127.0.0.1"` | для доступу з edge: `"0.0.0.0"` + `openFirewall` |
| `port` | port / 8091·8092·8093 | |
| `device` | enum `cpu cuda rocm` / `"cpu"` | для tts `rocm` → assertion (D2) |
| `model` | nullOr str / null | |
| `computeType` | nullOr str / null | тільки asr, mt |
| `modelDir` | str / `"<stateDir>/models"` | |
| `apiTokenFile` | nullOr str / null | **рядок-шлях**, не `path` (щоб не копіювати секрет у store); assertion: не починається з `/nix/store` |
| `openFirewall` | bool / false | |
| `firewallInterfaces` | listOf str / `[ "tailscale0" ]` | |
| `logLevel` | enum `debug info warning error` / `"info"` | |
| `rocmGfxVersion` | str / `""` | |
| `memoryDenyWriteExecute` | bool / `device == "cpu" && service != "tts"` | GPU JIT і oneDNN JIT у torch потребують W+X |
| `extraEnv` | attrsOf str / `{}` | |
| asr: `language` nullOr str / null; `beamSize` nullOr int / null (Python: 1 cpu / 3 cuda); `vad` bool / true | | |
| mt: `sourceLang` str / `"uk"`; `targetLang` str / `"en"`; `numBeams` nullOr int / null (Python: 2 cpu / 4 cuda) | | |
| tts: `voice` str / `"af_heart"`; `speed` float / 1.0; `sampleRate` int / 24000 | | |

**Конфіг при `enable`:**
- `users.users.rt-<svc> = { isSystemUser = true; group = "rt-<svc>"; home = stateDir; }`, `users.groups.rt-<svc> = {}`.
- `systemd.tmpfiles.rules = [ "d ${modelDir} 0750 rt-<svc> rt-<svc> - -" ]`.
- `systemd.services.rt-<svc>`:
  - `after = [ "network-online.target" ]; wants = [ "network-online.target" ]; wantedBy = [ "multi-user.target" ]`.
  - `unitConfig`: `StartLimitIntervalSec = "5min"; StartLimitBurst = 5;` і при `deployRsPath != ""` — `ConditionPathExists = "${deployRsPath}/bin/rt-<svc>"` (до першого deploy-rs push юніт «skipped», а не крутиться в restart-loop; `systemctl restart` з activation-скрипта після `ln -sfn` проходить).
  - `ExecStart = "${binRoot}/bin/rt-<svc>"`, де `binRoot = if deployRsPath != "" then deployRsPath else cfg.package`.
  - `Environment = map quoteEnv (lib.rtSpeech.envLines svc cfg)` (включно з літеральним `RT_API_TOKEN_FILE`), де `quoteEnv` бере рядок у `"…"`, екранує `\` і `"`, і подвоює `%` (systemd розкриває specifiers у `Environment=` і ділить незакавичене значення по пробілах). `envLines` лишається «сирим» `K=V` — саме цей формат потрібен для `EnvironmentFile`.
  - `EnvironmentFile = optional (deployRsPath != "") "-${deployRsPath}/env"` (перекриває `Environment=` — поведінка llama).
  - `LoadCredential = optional (apiTokenFile != null) "api-token:${apiTokenFile}"` — секрет root-owned, юзер сервісу читає копію в `$CREDENTIALS_DIRECTORY`.
  - `User`/`Group = rt-<svc>`, `StateDirectory = "rt-<svc>"`, `StateDirectoryMode = "0750"`, `WorkingDirectory = stateDir`.
  - Hardening: `PrivateTmp`, `ProtectSystem = "strict"`, `ProtectHome`, `NoNewPrivileges`, `ProtectKernelTunables`, `ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`, `ProtectClock`, `ProtectHostname`, `RestrictNamespaces`, `RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`, `SystemCallArchitectures = "native"`, `CapabilityBoundingSet = ""`, `RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ]`, `MemoryDenyWriteExecute = cfg.memoryDenyWriteExecute`, `PrivateDevices = device == "cpu"`, `ReadWritePaths = optional (!hasPrefix stateDir modelDir) modelDir`.
  - GPU: `SupplementaryGroups = [ "video" "render" ]`; `DeviceAllow` — cuda: `"/dev/nvidiactl rw" "/dev/nvidia0 rw" "/dev/nvidia-modeset rw" "/dev/nvidia-uvm rw" "/dev/nvidia-uvm-tools rw"`; rocm: `"/dev/kfd rw" "char-drm rw"`.
  - `LimitNOFILE = 65536`; **без** `LimitNPROC`. `Restart = "on-failure"`, `RestartSec = "5s"`, `TimeoutStartSec = "60s"`, `TimeoutStopSec = "15s"`, `SyslogIdentifier = "rt-<svc>"`.
- Firewall: `networking.firewall.interfaces = genAttrs firewallInterfaces (_: { allowedTCPPorts = [ port ]; })` при `openFirewall`.
- Assertions/warnings:
  - tts + `device = "rocm"` без власного `package`/`deployRsPath`: default `package` = `rtPkgs.tts.rocm or (throw "…see spec D2…")` → зрозуміла evaluation error (лінива: не спрацьовує, якщо `package` задано або `deployRsPath` не порожній);
  - `apiTokenFile` не в `/nix/store`;
  - warning: `openFirewall && host == "127.0.0.1"` (порт відкрито, але ніхто не слухає ззовні).
- Хост сам відповідає за GPU-драйвер (`hardware.nvidia`/`hardware.graphics`, ROCm) — модуль його не вмикає (як llama).

### 5.8 `homeManagerModules.rt-translator` (edge only)
```
rt-translator.enable
rt-translator.package          default: (import ../packages { inherit pkgs; }).edge
rt-translator.mode             enum [ "transcribe" "translate" ], default "transcribe"
rt-translator.endpoints.asr    str, default "http://127.0.0.1:8091"
rt-translator.endpoints.mt     nullOr str, default null
rt-translator.endpoints.tts    nullOr str, default null
rt-translator.apiTokenFile     nullOr str, default null   (рядок-шлях; секрет не в store)
rt-translator.autostart        bool, default true          (WantedBy graphical-session.target)
rt-translator.virtualSinks.enable  bool, default true
```
- `imports = [ mkRemovedOptionModule [ "rt-translator" "whisper" ] "…speech services moved to services.rtSpeech (NixOS)" … ]` для `whisper`, `translate`, `tts`, `wyoming` — зрозуміла помилка замість «option does not exist».
- assertion: `mode == "translate" -> endpoints.mt != null && endpoints.tts != null`.
- `edgeEnv` = `RT_EDGE_MODE`, `RT_EDGE_ASR_URL`, `RT_EDGE_MT_URL` (якщо задано), `RT_EDGE_TTS_URL` (якщо задано), `RT_EDGE_API_TOKEN_FILE` (якщо задано).
- `home.sessionVariables = edgeEnv` (запуск з терміналу/лаунчера) і `systemd.user.services.rt-app.Service.Environment = edgeEnv` (user-юніти не бачать session vars).
- `systemd.user.services.rt-app` — як зараз (`ExecStart = ${package}/bin/translator-ui`, `PassEnvironment` Wayland/X/DBus), але `After = [ "graphical-session.target" "pipewire.service" ]` без `rt-virtual-sinks`.
- Sinks: **лише** `xdg.configFile."pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf"` (pactl-oneshot `rt-virtual-sinks` видаляється — він дублював sinks).
- `home.packages = [ package pkgs.pulseaudio ]` (`pactl` для `AudioRouter`); `espeak-ng` прибирається (TTS не на edge).
- **Видаляються**: юніти й сокети `rt-whisper`, `rt-hybrid-whisper`, `rt-translate`, `rt-tts`, `rt-virtual-sinks`, `tmpfiles %t/rt`. HM сам зупиняє/видаляє зниклі юніти під час `home-manager switch` (sd-switch).

### 5.9 Edge-клієнт (Python)
Нові/змінені модулі:

| Файл | Відповідальність |
|---|---|
| `src/core/runtime.py` (переписується) | `EdgeSettings` (dataclass: `mode`, `asr_url`, `mt_url`, `tts_url`, `api_token_file`, `asr_transport`, timeouts, `managed: frozenset[str]`) + `get_runtime_config()` / `reset_runtime_config()`. Пріоритет: env `RT_EDGE_*` > `config.yml` (`services.*`) > дефолти. Поля з env позначаються як `managed` (UI робить їх read-only). Сокет-функції видаляються. |
| `src/clients/base.py` | `ServiceClient(base_url, token_file, timeout)`: спільний `httpx.Client`, `Authorization: Bearer` з файлу (читається при створенні; відсутній файл → warning і запити без auth), `health() -> bool`, `status() -> dict \| None`, мапінг помилок у `ServiceError(kind=unreachable\|auth\|http\|timeout)`. |
| `src/clients/asr.py` | `AsrClient.transcribe(audio_int16, sample_rate=16000, language=None, prompt=None) -> AsrResult(text, language, segments)`; WAV кодується stdlib `wave`; multipart `file`, `response_format=verbose_json`. |
| `src/clients/asr_stream.py` | `AsrStreamClient` (websockets, sync API у власному потоці): `start(language, sample_rate)`, `send_pcm(bytes)`, `stop()`, колбеки `on_partial`/`on_final`. Опційний транспорт. |
| `src/clients/mt.py` | `MtClient.translate(texts: list[str], source_lang, target_lang) -> list[str]`. |
| `src/clients/tts.py` | `TtsClient.synthesize(text, voice, speed) -> (np.ndarray[float32], sample_rate)`; `response_format="wav"`, декодування `wave` (PCM16 → float32). |
| `src/clients/health.py` | `ServiceHealthMonitor`: фоновий потік, кожні 5 с `GET /status` (fallback `/health`) для активних у поточному режимі сервісів; кешований `snapshot()` → `{asr: {...}, mt: {...}, tts: {...}}`. UI ніколи не робить мережевих викликів у Qt-потоці. |
| `src/translation_system.py` | прибрати `WhisperSocketClient`, `IPCClient`, `_ipc_service_map`, `use_wyoming`. Конструктор приймає `EdgeSettings` (або бере з `get_runtime_config()`). `_process_chunk`: ASR → (якщо `mode == translate`) MT (`source_lang` = обраний або `language` з ASR при `auto`) → TTS → playback-черга. `prompt` для ASR = склеєний `_stt_context`. Режим `transcribe` пропускає MT/TTS повністю. `get_stats()` повертає `mode`, `asr/mt/tts_connected` + `*_model`/`*_device` з `ServiceHealthMonitor`. `set_languages` — лише локальний стан (сервіси stateless). |
| `src/adapters/direct_adapter.py` | залишаються preflight PipeWire, пристрої, рівні, start/stop pipeline. Видаляються: `systemctl`, spawn, `_MODULE_ENTRYPOINTS`, Wyoming, `change_whisper_model`, `reconnect_ipc`, `restart_*`, `get_service_active`, `setup_ml_env()`. Додаються: `reconfigure_services(settings: EdgeSettings) -> bool` (перестворює клієнти; якщо pipeline біг — stop/start), `refresh_services() -> dict` (примусовий health-poll). |
| `src/adapters/ipc_adapter.py`, `src/adapters/__init__.py`, `src/controller/*` | `IPCAdapter` видаляється; з інтерфейсу `TranslatorController` прибираються `start_service`/`stop_service`; додається `refresh_services`. |
| `src/main.py` | прибрати `--mode ipc`, `--use-wyoming`, `--wyoming-*`; `create_adapter()` без Wyoming. |
| UI | `ServiceStatusPanel`: рядки `asr`/`mt`/`tts` (статус, модель, device з `/status`; для `transcribe` mt/tts — «не використовується»), кнопки Start/Stop/Restart прибрано, «Reconnect» → `refresh_services`. `ServiceSettingsDialog`: група «Endpoints» (mode, 3 URL, transport, token file, кнопка «Test» → `status()` у фоновому потоці), поля `managed` — disabled з tooltip «керується Home Manager (RT_EDGE_*)»; Wyoming і зміну whisper-моделі прибрано. `SettingsDialog`: вкладку `ModelTab` прибрано (моделі — серверна справа); `src/ui/widgets/model_tab.py` видаляється. `MainWindow`: прибрати Wyoming-стан/`reconfigure_controller`; режим `transcribe` ховає/дизейблить панель перекладу. |
| `src/core/config.py`, `config/default.yml` | дефолти: прибрати `wyoming`, `models.*`; додати `services: { mode: transcribe, asr: { url, transport: http }, mt: { url: null }, tts: { url: null }, api_token_file: null, timeouts: { connect_s: 3, asr_s: 30, mt_s: 15, tts_s: 30 } }`; `translation.{source_lang,target_lang}` і `speech.{tts_voice,tts_speed}` лишаються як **параметри запиту**. `get_wyoming_config`/`set_wyoming_config` видаляються, додається `get_services_config()`. Старі ключі в існуючих `config.yml` ігноруються (merge їх не ламає). |

Python-залежності edge (`nix/packages/edge.nix`): `pyside6`, `sounddevice`, `numpy`, `scipy`, `pysilero-vad`, `pulsectl`, `pyyaml`, `python-dotenv`, `loguru`, `httpx`, `websockets`. Прибираються: `pyaudio`, `huggingface-hub`, `en_core_web_sm`. Edge-пакет будується з `edgeSrc` (без `src/services`) і в `postInstall` лишає в `$out/bin` тільки `translator-ui` (інакше console scripts `rt-asr/rt-mt/rt-tts` з pyproject вказували б на відсутні модулі).

### 5.9a Розповсюдження тільки через git (overview §4.5a)
- `.gitignore`: шаблонні Python-правила анкеруються до кореня (`/lib/`, `/lib64/`, `/env/`, `/var/`, `/data/`, `/build/`, `/dist/`, `/downloads/`, `/eggs/`, `/sdist/`, `/wheels/`, `/develop-eggs/`); `result` → `result` + `result-*`; `git rm --cached result`.
- Усі Nix-джерела — відносні шляхи всередині репо (`../..`, `lib.fileset`); заборонено `/home/…`, `builtins.getEnv`, `builtins.currentSystem`, `builtins.currentTime`, `<nixpkgs>`, `builtins.fetchurl`/`fetchTarball` без хешу. Зовнішні артефакти — лише fixed-output (`pkgs.fetchurl` з `sha256`, напр. `en_core_web_sm`).
- `checks.flake-purity`: `runCommand`, що grep-ає `flake.nix` і `nix/` на `/home/`, `getEnv`, `currentSystem`, `currentTime`, `<nixpkgs>` і падає при збігу.
- Моделі в репо не потрапляють; bundle не містить моделей — тільки код і Python env.
- Правило розробки: нові файли `git add` **до** будь-якої `nix`-команди (git-flake не бачить untracked).
- Доказ самодостатності: `git clone` у тимчасову теку + `nix flake check`/`nix build .#rt-mt-cpu` там; після push — `nix build github:DohuyaMa/realtime-translation/<rev>#rt-mt-cpu`.

### 5.10 Що видаляємо / що лишаємо
| Шлях | Рішення | Коли |
|---|---|---|
| `src/capture/`, `src/playback/` | видалити (capture/playback уже в процесі UI) | після edge-міграції |
| `src/whisper/` (`whisper_service`, `hybrid_whisper_service`, `wyoming_client`) | видалити (Wyoming — не основний шлях, overview §2) | після edge-міграції |
| `src/translate/`, `src/tts/` | видалити | після того, як підпроєкт 1 переніс логіку в `src/services/{mt,tts}` (перевірка: `grep -r "src.translate\|src.tts\." src/services` порожній) |
| `src/common/ipc.py`, `src/pipeline/`, `src/adapters/ipc_adapter.py` | видалити | після edge-міграції |
| `src/status_logger.py` | видалити, якщо після чистки ніхто не імпортує | разом зі службами |
| `src/models/`, `src/core/models.py`, `src/core/huggingface.py`, `src/core/env.py` | **лишити** — власник підпроєкт 1 (може перенести в `src/services`) | — |
| `src/ui/main_window.py.{backup,fixed,old}`, `src/ui/qml/`, `src/audio/{capture,processor}.py` | поза scope (не чіпаємо) | — |
| `tests/test_ipc_communication.py`, `tests/test_wyoming_integration.py`, `tests/test_integration.py`, `tests/test_service_status.py`, `tests/test_pipeline.py`, `tests/test_direct_adapter.py` | видалити або переписати під нову модель (див. план) | з відповідними задачами |
| `flake-global/`, `parts/`, `nixosModules/`, `flake.nix.old`, `result` (симлінк у git) | видалити; `virtual-sinks.nix` і `en_core_web_sm` переносяться в `nix/` | фінальна чистка |
| `pyproject.toml` console scripts `translator-{capture,playback,whisper,hybrid-whisper,translate,tts}` | видалити; `translator-ui` лишити; `rt-asr/rt-mt/rt-tts` — підпроєкт 1 | з чисткою |
| `CLAUDE.md` (local, gitignored), `AGENT.md` (tracked) | переписати розділи Nix/архітектури під нову структуру (обидва; у git іде лише `AGENT.md`) | фінальна задача |
| `result` (tracked симлінк) | `git rm --cached` | Task 1 |

---

## 6. Потоки даних

**Transcribe:** mic → sounddevice 48k → Silero VAD → сегмент int16 16k → `AsrClient.transcribe` (HTTP) → текст → `pending_recognized` → UI.
(WS-транспорт: mic → 16k PCM16 фрейми → `AsrStreamClient` → `partial`/`final` → UI.)

**Translate:** … → текст → `MtClient.translate([text], src, tgt)` → `TtsClient.synthesize(translated, voice, speed)` → `(float32, sr)` → playback-черга → `sd.play` на обраному output (локально на edge).

**Деплой сервісу:** colmena: `services.rtSpeech.asr = { enable; deployRsPath = "/var/lib/rt-asr/current"; device = "cuda"; host = "0.0.0.0"; openFirewall = true; apiTokenFile = "/run/secrets/rt-token"; }` → юніт є, але `ConditionPathExists` → skipped. deploy-rs (підпроєкт 3): `symlinkJoin [ pkgs.rtSpeech.asr.gpu (lib.rtSpeech.mkEnvFile { pkgs; service = "asr"; cfg = hostCfg; }) ]` → `ln -sfn $PROFILE /var/lib/rt-asr/current` → `systemctl restart rt-asr` → `curl /health`.

---

## 7. Обробка помилок

| Ситуація | Поведінка |
|---|---|
| Endpoint недоступний / timeout | запит падає з `ServiceError`; сегмент відкидається, `pipeline_errors += 1`, лог `warning` раз на N помилок (rate-limit), статус сервісу червоний з `ServiceHealthMonitor`. Ретраїв у real-time шляху немає. |
| 401/403 | `ServiceError(kind="auth")` → статус «auth error», лог `error` з підказкою про `apiTokenFile`. |
| `mode = translate`, але MT або TTS лежить | транскрипція все одно показується; переклад/озвучка пропускаються для цього сегмента. |
| `mode = translate` без URL MT/TTS | HM — assertion; без HM (`config.yml`) — `EdgeSettings` валідація при старті pipeline → `start_pipeline()` повертає `False` + повідомлення в UI. |
| Token file відсутній/нечитабельний | warning, запити без `Authorization` (сервер відповість 401 → див. вище). |
| `/status` каже `ready: false` (модель вантажиться) | статус «loading» (жовтий); запити все одно надсилаються (сервер може відповісти 503 → `ServiceError(kind="http")`). |
| Сервіс до першого deploy-rs push | `ConditionPathExists` → юніт `inactive (condition failed)`, без restart-loop. |
| Bundle без потрібного Python-модуля | збірка падає на `installCheckPhase` (import-check). |

---

## 8. Тестування і верифікація

| Перевірка | Що доводить | Як |
|---|---|---|
| `checks.env-lines` | `envLines` дає очікувані рядки для asr/mt/tts, cuda/rocm, `extraEnv`, null-моделі; `throw` на `\n` | `lib.runTests` → `runCommand` падає, якщо список невдач не порожній |
| `checks.rt-{asr,mt,tts}-cpu`, `checks.rt-edge` | bundle збирається з кешу, import-check проходить, `bin/rt-<svc>` існує | `nix build` |
| `checks.edge-pytest` | клієнти й `EdgeSettings` (httpx `MockTransport`, локальний `websockets` сервер), `TranslationSystem._process_chunk` у двох режимах з фейковими клієнтами | `pytest tests/edge -q` у Nix-середовищі (без Qt, без звуку) |
| `checks.nixos-rt-speech` (VM, `pkgs.testers.runNixOSTest`) | юзер/юніт/hardening; firewall лише на заданому інтерфейсі; `LoadCredential` токена; deploy-rs потік (`ConditionPathExists` → symlink → `EnvironmentFile` перекриває `Environment`) | 2 ноди (`server`, `client`); fake-бекенд — `writers.writePython3Bin` на stdlib `http.server`, що віддає `/health`, `/status` (з поточними `RT_*` env), `/v1/ping` (вимагає bearer, якщо `RT_API_TOKEN_FILE`) |
| `checks.nixos-eval-gpu` | при `device = "cuda"`/`"rocm"`: правильні `DeviceAllow`, `SupplementaryGroups`, `MemoryDenyWriteExecute = false`, `PrivateDevices = false`; tts+rocm → assertion | `nixpkgs.lib.nixosSystem` eval (без збірки) + `builtins.tryEval` |
| `checks.hm-edge` | HM-модуль: юніт `rt-app` має `RT_EDGE_*`; немає старих юнітів; assertion для `translate` без mt/tts; removed-option повідомлення | `home-manager.lib.homeManagerConfiguration` eval + `activationPackage` build |
| `checks.flake-purity` | немає абсолютних/impure посилань у Nix-коді | grep у `runCommand` |
| Git-flake самодостатність | збірка зі свіжого клону / з GitHub-ревізії | `git clone file://$PWD $TMP/rt && nix build $TMP/rt#rt-mt-cpu && nix flake check $TMP/rt`; після push: `nix build github:DohuyaMa/realtime-translation/<rev>#rt-mt-cpu` |
| Ручні | `nix eval .#packages.x86_64-linux.rt-{asr,mt}-{gpu,rocm}.drvPath`, `…rt-tts-gpu.drvPath`; відсутність `torch-bin` у замиканні; cache-hit `cache.nixos-cuda.org` для важких CUDA-пакетів; опційно `nix build .#rt-asr-gpu` на GPU-хості; smoke `RT_PORT=18091 ./result/bin/rt-asr` + `curl /health` ≤ 10 с (після підпроєкту 1) | |

---

## 9. Безпека
- Токени — тільки шляхи до файлів (`nullOr str`), assertion проти `/nix/store`. На сервері — `LoadCredential`, на edge — файл користувача (sops-nix HM або ручний).
- Порти відкриваються лише на `firewallInterfaces` (default `tailscale0`), bind за замовчуванням `127.0.0.1`.
- Без `RestrictAddressFamilies`-послаблень «для Tailscale» (Tailscale — звичайний інтерфейс, AF_INET/AF_INET6 достатньо).

---

## 10. Міграція
0. Bump nixpkgs ≥ ревізії інфри (Task 0) — до всіх інших планів (overview §6).
1. Реалізувати `nix/` і нові виходи поруч зі старими (`flake-global/` ще живе) → `nix flake check`.
2. Edge Python перейти на HTTP/WS (старі IPC-сервіси ще в дереві, але не використовуються).
3. Замінити HM-модуль (той самий output name `homeManagerModules.rt-translator`) — removed-options дають зрозумілу помилку споживачам (зараз у swarm-nix імпорт закоментований, тож живих споживачів немає).
4. Видалити старий код/юніти/`flake-global/`/`parts/` (після підпроєкту 1 — `src/translate`, `src/tts`).
5. Оновити `CLAUDE.md`/`AGENT.md`.

---

## 11. Межі (out of scope)
- Внутрішній API сервісів, `src/services/**` (підпроєкт 1).
- swarm-nix: overlay у `pkgs`, `applications/services/rt-*.nix`, `mkDeployProfiles` узагальнення, per-host конфіги, substituters (підпроєкт 3).
- Django worker опції (`RT_DJANGO_*`) (підпроєкт 4).
- Конвертація моделей, ORT-рушій Kokoro, реєстр моделей, VRAM-бюджет (підпроєкт 5).
- Substituter `cache.nixos-cuda.org` у системних `nix.settings` хостів (підпроєкт 3, Task 7a); тут — лише `nixConfig`-підказка.

---

## 12. Deviations from overview

| # | Overview каже | Цей spec | Чому |
|---|---|---|---|
| D1 | «`ctranslate2` у nixpkgs з CUDA і `rocmSupport`» (§2) | **Відхилення знято (C1).** У старому lock `89570f2` опції не було; у nixpkgs інфри (`7a0f122`) вона є, overlay будується з `pkgs` інфри, а Task 0 піднімає lock цього репо → ROCm = `ctranslate2.override { rocmSupport = true; rocmGpuTargets; }`. ROCm-варіанти — «experimental» з апаратних причин (gfx90c), не пакувальних | перевірено `pkgs/by-name/ct/ctranslate2/package.nix` в обох ревізіях |
| D2 | `packages.rt-tts-rocm` / `pkgs.rtSpeech.tts.rocm` (§4.1, §4.4) | **не експортується**; `services.rtSpeech.tts.device = "rocm"` — assertion (якщо не задано власний `package`/`deployRsPath`) | TTS default — ORT-рушій (SP5 T4); ORT ROCm EP видалено в 1.23, MIGraphX EP на gfx90c не перевірений; 82M-модель на iGPU не варта ризику (SP5 D2). Може з'явитися пізніше як експериментальний через `onnxruntime.override { rocmSupport = true; }`. Причина — не torch |
| D3 | `RT_API_TOKEN_FILE` — шлях до файлу-секрету (§4.2) | секрет доставляється `LoadCredential=api-token:<apiTokenFile>`; `RT_API_TOKEN_FILE` = літеральний `/run/credentials/rt-<svc>.service/api-token` (емітує `envLines`, тож він же потрапляє в deploy-rs `env`) | секрет root-owned, сервісний юзер не має прямого доступу до `/run/secrets`; не залежить від прав файлу |
| D4 | опції модуля «`… , ...`» (§4.4) | додано `memoryDenyWriteExecute`, `rocmGfxVersion`, `modelDir`, `logLevel`, сервіс-специфічні; `model`/`computeType` — `nullOr str` з default `null` | єдине джерело дефолтів моделей — Python; GPU/torch JIT несумісні з MDWE |
| D5 | HM: `rt-translator.endpoints.*`, `mode`, `apiTokenFile` (§4.4) | додано `package`, `autostart`, `virtualSinks.enable`; edge отримує значення через env `RT_EDGE_*` | env — явний, тестований канал; `config.yml` мутабельний з UI і не може бути HM-симлінком |
| D6 | — | `flake-utils` прибрано, системи — тільки `x86_64-linux` | darwin/aarch64 фактично не збираються (PipeWire/CUDA); `nix flake check` не має падати на них |

---

## 13. Open questions

1. **CUDA binary cache для `rt-*-gpu`.** SP5 F7: з глобальним `cudaSupport = true` і дефолтними `cudaCapabilities` усе (включно з libnvshmem) є в `cache.nixos-cuda.org` (у `cuda-maintainers.cachix.org` — ні). План Task 5 Step 3 перевіряє це для ревізії, що буде в swarm-nix. Незалежно від результату рекомендація: GPU-хости деплоять `rt-tts-cpu` (VRAM-first).
2. ~~torch-bin без libnvshmem~~ — **закрито** (C2): torch-bin не використовується взагалі.
3. **CT2 ROCm на залізі**: збірка — штатний nixpkgs `rocmSupport` (C1, план Task 5 Step 5); відкрито лише, чи `gfx900` + `HSA_OVERRIDE_GFX_VERSION=9.0.0` (Vega APU, як llama-rocm) працює стабільно — верифікація на SKTOP-NOTE17-NIXOS (SP5 Task 22).
4. ~~MT `context`~~ — **закрито** (C5): контракт `/v1/translate` поля `context` не має (NLLB/CT2 його й не використовує); edge контекст **не шле**. Залежність від підпроєкту 1 знято (менша зміна, ніж розширювати API).
5. ~~TTS WAV формат~~ — **закрито**: SP1 spec §8.1 гарантує PCM16LE mono (+ `X-Sample-Rate`).
6. **Один токен на edge** (A4) vs per-service токени — достатньо одного? Якщо ні, HM отримає `apiTokenFiles.{asr,mt,tts}`.
7. **Bind до Tailscale IP** замість `0.0.0.0`: health-check інфри (`curl 127.0.0.1`) тоді не працює. Залишаємо `0.0.0.0` + firewall на інтерфейсі; інфра має знати про це обмеження (підпроєкт 3).
8. **`rt-asr` WS endpointing** (підпроєкт 1) — якщо серверний VAD гірший за edge Silero, WS лишається опційним назавжди.
9. **aarch64-linux cpu-варіанти** — потрібні (Raspberry/ARM-хости в registry)? Зараз ні (A5).

---

## 14. Spec self-review (виконано)
- Placeholders: TBD/TODO відсутні; невизначене винесено в «Open questions» з власником.
- Узгодженість: порти/імена/шляхи/env збігаються з overview §4.1–4.2; відхилення — лише D1–D6. `envLines` емітує лише шлях credential (D3, R3), секрет — через `LoadCredential` (5.7), VM-тест це перевіряє (8).
- Інтерфейс для підпроєкту 3: кожна вимога R1–R7 зіставлена з розділом дизайну і перевіркою (§15); єдиний конфлікт — відсутній `rtSpeech.tts.rocm` (D2), явно позначений.
- Scope: один план; edge Python і Nix розділені на незалежні задачі; WS-транспорт явно відкладуваний.
- Неоднозначності: default `mode = "transcribe"`; пріоритет env > `config.yml` > дефолти; `rt-tts-rocm` відсутній (не `throw`, а відсутній атрибут) — зафіксовано.

---

## 15. Вимоги підпроєкту 3 (infra, `2026-10-06-infra-deploy-rs-design.md` §12) → цей дизайн

| R | Вимога | Де в дизайні | Перевірка (план) |
|---|---|---|---|
| R1 | `overlays.default` додає лише `rtSpeech` (`{asr,mt,tts}.{cpu,gpu,rocm}` + `lib.envLines`); CUDA/ROCm — scoped, без глобальних `python313`/`ctranslate2` override (llama byte-identical) | 5.1 (overlay = один атрибут), 5.2 (`rtSpeech.lib` = alias `lib.rtSpeech`), 5.3/5.5 (CUDA — окремий інстанс `import pkgs.path {cudaSupport}` всередині `mkBundle`; ROCm CT2 — override всередині `mkBundle`; torch-bin не використовується). **Виняток:** `rtSpeech.tts.rocm` відсутній (D2) — інфра не створює `rt-tts-rocm` | Task 4: `checks.overlay` — `p.python313`/`p.ctranslate2` мають той самий `outPath`, що й без overlay; `p.rtSpeech.lib.envLines` повертає `RT_PORT` |
| R2 | `envLines` завжди емітує `RT_HOST`, `RT_PORT`, `RT_DEVICE`; без секретів | 5.6 (перші рядки безумовні; токен — лише шлях) | Task 1: `checks.env-lines` (`testAlwaysHostPortDevice`, `testTokenPathLiteral`) |
| R3 | `LoadCredential=api-token:<apiTokenFile>`; env — літеральний `RT_API_TOKEN_FILE=/run/credentials/rt-<svc>.service/api-token` | 5.6, 5.7, D3 | Task 1 env-lines; Task 2 eval (`testTokenViaCredential`); Task 3 VM (`/status` показує літеральний шлях, bearer працює) |
| R4 | `/health` ≤ ~10 с після `systemctl restart` на найслабшому CPU-хості | **Крос-реф на підпроєкт 1**: важкі імпорти (torch, ctranslate2, kokoro) — ліниво, після старту uvicorn; модель — у фоні. Тут: bundle не робить нічого перед `exec python -m …` (лише makeWrapper), `TimeoutStartSec=60s` не заважає | Task 4 Step 5: smoke `curl /health` з лімітом 10 с (fail, якщо довше — баг підпроєкту 1; SP1 `test_svc_lazy_imports` + SP5 lazy-imports constraint) |
| R5 | `ConditionPathExists = "${deployRsPath}/bin/rt-<svc>"`; при `openFirewall = false` порт не відкривається ніде, незалежно від `host` | 5.7 (`unitConfig`, firewall лише через `networking.firewall.interfaces` під `mkIf openFirewall`) | Task 2 eval (`testDeployRsPath`, `testFirewallClosedByDefault`, `testFirewallOnlyOnTailscale`); Task 3 VM (ConditionResult=no до symlink) |
| R6 | Імена юніта/юзера/stateDir — точно `rt-asr`, `/var/lib/rt-asr` тощо | 5.6 (`services` у `lib.rtSpeech` — єдине джерело), 5.7 | Task 2 eval (`User = "rt-asr"`, `StateDirectory = "rt-asr"`), Task 3 VM (`stat` власника `/var/lib/rt-asr`) |
| R7 | Збірка з чистого git-checkout / GitHub-ревізії | 5.9a | Task 1 (`.gitignore`, `flake-purity`), Task 16 Step 5–6 (`git clone` + `nix build`, `nix build github:…/<rev>#rt-asr-cpu`) |
