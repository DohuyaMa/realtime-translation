# Підпроєкт 3 — Інтеграція speech-сервісів в інфру (swarm-nix, deploy-rs) — design

> **Статус 2026-10-22:** частково замінено. Scope MVP (без `rt-mt`, переклад через llama, лише цей репо) — `docs/superpowers/specs/2026-10-22-realtime-mvp-design.md`; план виконання — `docs/superpowers/plans/2026-10-22-realtime-mvp.md` (§10 spec: що KEEP/DROP/DEFER). Цей документ лишається джерелом деталей для задач, на які посилається новий план.

Дата: 2026-10-06
Статус: draft, архітектурне рішення делеговане (напрямок A затверджено, без інтерактивного Q&A). Припущення і відкриті питання зібрано наприкінці. Узгоджено з іншими підпроєктами 2026-10-06 — overview §7 (C0: cluster-generic, розміщення — дані clusterops, §7; C4: CUDA substituter; C1/C2: пакування GPU/ROCm).
Базовий документ: [`2026-10-06-distributed-speech-services-overview.md`](2026-10-06-distributed-speech-services-overview.md) (далі — **overview**). Контракт з §4 overview обов'язковий; відхилення — у розділі 11.
Код, який змінюватиметься: `~/repos/swarm-nix` (`system-conf/` + `django_api/clusterops/`). Цей документ код не змінює.

---

## 1. Мета і критерії успіху

**Що сказано (вхідні вимоги):**
- `rt-asr`, `rt-mt`, `rt-tts` деплояться **так само, як `llama-service`**: colmena ставить NixOS-модуль (юніт, user, tmpfiles), deploy-rs ставить код у `/var/lib/rt-<svc>/current` і робить restart + health check.
- Flake цього репо — input `system-conf/flake.nix` **виключно** як `github:DohuyaMa/realtime-translation` (публічний), ревізія пінится в закоміченому `system-conf/flake.lock`; жодних `path:`-inputs і `--override-input` у деплої (overview §4.5a: щонайменше два контролери — cyborg і SKTOP-NOTE27-NIXOS — деплоять з swarm-nix і мусять бачити ту саму git-ревізію). Overlay застосовується до `pkgs`, бо registry-файли бачать лише `{ pkgs, lib }`.
- Per-host `services.rtSpeech.*` рендериться в `env` бандла через `lib.rtSpeech.envLines` — через **узагальнений** env-merge hook (llama лишається byte-identical).
- `registry.nix`/`inventory.nix` згенеровані clusterops NixGen з Django DB — нові Service створюються через систему, не ручною правкою згенерованого файлу.
- Per-host увімкнення в `machines/<host>/modules/flake-services/`, firewall лише Tailscale, токен лише з файлу (sops-nix / colmena keys).
- Загальна процедура розміщення (клас заліза → варіанти, admission check за VRAM/RAM, add/move через clusterops) — без фіксованого розміщення (overview §7 C0); топології «тільки транскрипція» і «повний переклад» — як приклади.
- Прибирання застарілих артефактів; rollout і перевірка.

**Поза scope:** внутрішня реалізація Python-сервісів (підпроєкт 1), збірка пакетів/модуля/HM-модуля в цьому репо (2), маршрутизація WorkItem у Django engine (4), вибір конкретних моделей (5 — тут лише VRAM-стелі на хост).

**Критерії успіху:**
1. `nix flake check --no-build` у `system-conf/` проходить; `drvPath` усіх п'яти llama-профілів (`kira-kiosk.llm-cpu`, `sktop-nixos-top.llm-cpu`, `cyborg.llm-gpu`, `SKTOP-NOTE27-NIXOS.llm-gpu`, `SKTOP-NOTE17-NIXOS.llm-rocm`) **не змінився** після всіх змін.
2. `registry.nix` з `rt-*` записами отримано **генерацією** з clusterops DB (`git diff` = лише результат генерації), а `import_infra` не перекласифіковує `rt-*-cpu`/`-gpu`/`-rocm` у `llm-*`.
3. `nix run .#deploy-rs -- .#<host>.rt-<svc>-<variant>` (приклад: `.#cyborg.rt-asr-gpu`) (і через clusterops API `service-targets/<pk>/deploy/`) проходить health check; `curl` з іншої машини по Tailscale отримує `200` від `/health` і коректну відповідь `/v1/*`; з LAN-адреси порт закритий.
4. Топологія «повний переклад» працює на будь-якому розміщенні, обраному даними clusterops; для кожного хоста з rt-сервісами admission check (§7.3: пік ≤ 90 % VRAM/RAM пристрою з усіма co-located сервісами, без OOM) пройдено до і після деплою.
5. Застарілі rt/wyoming-артефакти видалені з коду **і** з усіх згадок (README, tasks, TD, коментарі).
6. `system-conf/flake.lock` (у git) пінить `realtime-translation` як `type = github` з конкретним `rev`; на обох контролерах (cyborg, SKTOP-NOTE27-NIXOS) після `git pull` ця ревізія однакова; у swarm-nix немає жодного `path:`/`git+file:`-посилання на цей репо.

## 2. Ключові факти, знайдені при дослідженні swarm-nix (2026-10-06)

| # | Факт | Де | Наслідок для дизайну |
|---|---|---|---|
| F1 | `mkApplication` = `ln -sfn $PROFILE <stateDir>/current` → `systemctl restart <serviceName>` → health 20×1s; коментар про **падіння auto-rollback при першому деплої** (нема попередньої генерації) | `applications/lib/mkApplication.nix` | Перший деплой кожного `rt-*` — з ручним планом відновлення (§8.4) |
| F2 | `mkDeployProfiles` має LLM-специфічний прапорець `mergeEnvFromLlamaService` + `mkEnvFile` жорстко імпортує `common/modules/llm/lib/env-lines.nix` | `applications/lib/mkDeployProfiles.nix`, `mkEnvFile.nix` | Узагальнити до hook-функції (§5.3) |
| F3 | NixGen: `_service_app_ref` → `llm-*` → `./llm/<v>.nix`, інакше `./services/<svc.name>.nix` | `django_api/clusterops/services/nix_generator/io.py:163-178` | `ServiceType.CUSTOM` + файл `applications/services/<name>.nix`; нових choices не треба |
| F4 | **Блокер:** `NixImporter.import_all` визначає тип за підрядком імені: `"cpu" in name → llm-cpu`, `"gpu" → llm-gpu`, `"rocm" → llm-rocm`, і робить `update_or_create(..., service_type=..., config={})` для **кожного** запису registry | `django_api/clusterops/services/nix_importer.py` (`import_all`) | `rt-asr-gpu` після будь-якого `import_infra` (у т.ч. `clusterops_startup_sync`) стане `llm-gpu` → NixGen відрендерить `import ./llm/gpu.nix` під ключем `rt-asr-gpu`, а деплой піде з профілем `llm-gpu`. Треба виправити до створення Service |
| F5 | `_profile_name()` повертає `service_type` для LLM і `name` для CUSTOM; а `generate_registry` завжди використовує ключ `svc.name` | `django_api/clusterops/services/deploy_service.py:119` | Для CUSTOM не блокує, але в парі з F4 блокує (профіль `llm-gpu`). Уніфікувати: профіль = `service.name` завжди |
| F6 | `generate_all()` кладе реєстр під ключ `"registry.nix"` (а не `applications/registry.nix`) → `generate_infra --write` пише **зайвий файл у корінь** `system-conf/`. Коректно реєстр перегенеровує лише `hostname_rename` (через `NixFile` + `drift.write_to_disk`) | `nix_generator/io.py:47`, `services/hostname_rename.py:258-265` | Виправити ключ; винести «перегенерувати один згенерований файл» у спільний хелпер і дати йому CLI |
| F7 | `generate_all()` **завжди** викликає `generate_nix_files()`, яка пише на диск `source` кожного `NixFile` з БД — навіть при `generate_infra --dry-run` | `nix_generator/io.py:42-62` | Не використовувати `generate_infra` без `--write`-дисципліни; для реєстру — точковий хелпер (F6). Ручні правки спершу `import_infra` (disk→DB), інакше генерація їх відкотить |
| F8 | REST API: `POST /api/clusterops/services/` (усі deploy-поля, **крім** `flake_module`), `POST /api/clusterops/services/<pk>/targets/`, `POST /api/clusterops/service-targets/<pk>/deploy/`, `POST /api/clusterops/secrets/`; DRF `TokenAuthentication` | `clusterops/urls.py`, `serializers.py`, `views/api.py` | Service створюємо через API (відтворювано, «система робить сама») |
| F9 | `deploy()` блокується, якщо будь-який `NixFile` ноди `divergent` (диск ≠ БД) | `deploy_service.py` (`DivergentArtifactsError`) | Після ручних правок per-host файлів — `import_infra` перед деплоєм через clusterops |
| F10 | `flake-services/default.nix` (лише cyborg і note27) — **не модуль-імпорт**: викликає кожен `*.nix` як `import f { inherit lib pkgs; }`, бере лише файли з top-level `enable = true` і форвардить тільки `systemd.services`/`systemd.timers`/`deployment.keys` | `machines/{cyborg,sktop-note27-nixos}/modules/flake-services/default.nix` | `services.rtSpeech` через агрегатор **мовчки загубиться**. Файл має (а) не мати top-level `enable`, (б) мати сигнатуру `{ ... }:` (агрегатор передає лише `lib`,`pkgs`), (в) імпортуватись явно з `configuration.nix` — як `llama-service.nix` |
| F11 | `tailscale0` уже в `networking.firewall.trustedInterfaces` для всього флоту | `common/modules/security/firewall.nix` | Окреме відкриття портів не потрібне; головне — щоб модуль **не** відкрив порт глобально |
| F12 | sops-nix імпортовано fleet-wide (`common/modules/services/sops-nix.nix`, ключ — host ed25519); clusterops `Secret` → `machines/<dir>/secrets.sops.yaml` + `secrets-module.nix` (path/mode/owner/restartUnits). Наразі `secrets-module.nix` не згенеровано **на жодному** хості; `secrets.sops.yaml` є лише в `sktop-nixos-top` | `nix_generator/pure.py::render_sops_secrets_module`, `rotation_preflight.py:124` | Токен — через clusterops `Secret`(sops), але це перша реальна поставка sops-секретів → окрема фаза B (§8) |
| F13 | `pkgs` створюється один раз у `flake.nix` і йде і в registry, і в `nixosSystem`, і в `colmenaHive.meta.nixpkgs` | `system-conf/flake.nix:46-48` | Один overlay у `flake.nix` → `pkgs.rtSpeech` доступний і deploy-rs, і NixOS-модулям |
| F14 | `deploy-rs` `checks` (`deployChecks`) у `nix flake check` будує activate-check, що залежить від шляхів **усіх** профілів | `system-conf/flake.nix` (`checks`) | Повний `nix flake check` локально збиратиме rt-бандли (torch/CUDA) — див. ризики і відкрите питання Q4 |
| F15 | Tailscale: `kira-kiosk` — direct; `sktop-nixos-top`, `sktop-note17-nixos` — через DERP relay `waw` (знімок `tailscale status`, 2026-10-06) | — | Для realtime-шляху (ASR) краще loopback/direct-піри; relay-хости — для менш чутливих до затримки сервісів |
| F16 | cyborg: RTX 4060 Laptop, 8188 MiB; llama Qwen3.5-4B Q4_K_M, `ctxSize = 16384`, `idleUnloadTimeout` вивантажує модель у простої | `nvidia-smi`, `machines/cyborg/modules/flake-services/llama-service.nix` | VRAM-бюджет рахуємо для «llama завантажена» (гірший випадок) |
| F17 | Застарілі: `machines/cyborg/modules/services/realtime-translation.nix` (не імпортується, посилається на неіснуючий `flake-global/...`), `machines/sktop-note27-nixos/modules/realtime-translation.nix` (не імпортується; wyoming + translatelocally + coqui), `common/modules/services/wyoming-faster-whisper.nix` (+ закоментований import у `common/nixos-config.nix`), закоментований HM-import `rt-translator` у `machines/cyborg/system.nix`, `system-conf/todo/todo.md` (секція path-input; **друга половина файлу — живий Yggdrasil TODO**), `README.md:263` (`--update-input realtime-translation`), `context/tasks.md` Wave 2c (RTT-1..4), `django_api/context/technical-debt.md` TD-119 (Wyoming CUDA), коментар у `common/nixos-base.nix:13`, README note27 (`README.md:16`, `modules/README.md:11`) | — | Прибираємо всюди одним проходом (§10) |

## 3. Розглянуті підходи

### 3.1 Як registry отримує пакети цього репо
- **A (обрано): overlay у `pkgs` (`flake.nix`)** — `inputs.realtime-translation.overlays.default` → `pkgs.rtSpeech.<svc>.<variant>`. Сигнатура згенерованого registry `{ pkgs, lib }` не змінюється; NixOS-модулі бачать те саме. Узгоджено з overview §4.4.
- B: додати `inputs` у сигнатуру registry (зміна NixGen + importer, який імпортує registry з `pkgs = {}; lib = {};`) — ламає контракт згенерованого файлу для всіх застосунків заради одного. Відхилено.
- C: окремий `ServiceType` на кожен rt-варіант + гілка в `_service_app_ref` — 9 нових choices + міграція + код у генераторі на кожне сімейство застосунків. Відхилено: `CUSTOM` + `./services/<name>.nix` уже є розширенням, спроєктованим саме для цього (F3).

### 3.2 Env-merge
- **A1 (обрано): hook-функція в записі застосунку** — `app.envLinesFor :: hostConfig -> [string] | null`. `mkDeployProfiles` не знає жодного конкретного сервісу; llama реалізує hook через свій `env-lines.nix`, rt — через `pkgs.rtSpeech.lib.envLines`. Друга реальна потреба (rt) уже настала — узагальнення виправдане.
- A2: модуль rt експонує read-only опцію `services.rtSpeech.<svc>.envLines`, інфра її читає. Чисто, але інфра тоді залежить від внутрішньої опції модуля; і llama все одно потребує окремого шляху. Відхилено як «два механізми».
- A3: ще один прапорець `mergeEnvFromRtSpeech` — N прапорців на N застосунків. Відхилено.

### 3.3 Як створюються Service у clusterops
- **Обрано: REST API clusterops** (F8) з токеном DRF — відтворювана послідовність `curl`, яку може повторити і сама система. UI (`/cluster/services/add`) — еквівалентна альтернатива для оператора.
- Відхилено: fixture/data-migration (Service — живі реплікаційні дані, `replicator` синхронізує `clusterops.*` між інстансами; fixture з pk конфліктуватиме), ручна правка `registry.nix` + `import_infra` (згенерований файл як джерело — суперечить правилу «DB = SSOT», та й deploy-поля `deploy_backend/state_dir/service_name` імпорт не заповнює).

### 3.4 Секрет `RT_API_TOKEN_FILE`
- **Обрано: clusterops `Secret` (delivery `sops`)** → згенерований `secrets.sops.yaml` + `secrets-module.nix` per host; файл `root:root 0400` у `/run/secrets/rt-api-token`; юніти читають його через systemd `LoadCredential` (вимога R3 до підпроєкту 2). Переживає reboot, працює і для colmena, і для `nixos-rebuild switch` (self-deploy на cyborg), має `restartUnits`.
- Відхилено як основний: colmena `deployment.keys` — не існує в plain `nixosConfigurations` eval (TD-92), не працює при self-deploy через `nixos-rebuild`, `/run/keys` на tmpfs (прецедент crash-loop у nix.md §4).
- Фаза A (перший rollout) — **без токена** (`apiTokenFile = null`): сервіси доступні лише через trusted `tailscale0`, як llama. Фаза B вмикає токен (§8.3). Це явна per-host конфігурація, не мовчазний fallback.

## 4. Архітектура

```
github:DohuyaMa/realtime-translation (підпроєкт 2)
  ├─ overlays.default ─────────────┐   pkgs.rtSpeech.{asr,mt,tts}.{cpu,gpu,rocm}, pkgs.rtSpeech.lib.envLines
  ├─ nixosModules.rt-speech ──┐    │
  └─ lib.rtSpeech.envLines    │    │
                              │    ▼
swarm-nix/system-conf/flake.nix: pkgs = import nixpkgs { overlays = [ rt overlay ]; }
  │                           │
  │  colmena / nixos-rebuild  │                       deploy-rs
  ▼                           ▼                         ▼
machines/<host>/configuration.nix               applications/registry.nix  (AUTO-GENERATED з clusterops DB)
  imports ../../common/modules/rt-speech.nix       "rt-asr-gpu" = { app = import ./services/rt-asr-gpu.nix {pkgs lib}; targets }
          (= inputs.realtime-translation               │
             .nixosModules.rt-speech)                  ▼
  imports ./modules/flake-services/rt-speech.nix   applications/services/rt-asr-gpu.nix
     services.rtSpeech.asr = { enable; device;…}     └─ applications/lib/mkRtSpeechApplication.nix { svc="asr"; variant="gpu"; }
  ⇒ system unit rt-asr, user rt-asr,                     ├─ mkApplication (symlink+restart+health)
    /var/lib/rt-asr, EnvironmentFile=-…/current/env      └─ envLinesFor = hostConfig: pkgs.rtSpeech.lib.envLines "asr" hostConfig.services.rtSpeech.asr
                                                    applications/lib/mkDeployProfiles.nix
                                                      package = app.package ⊕ mkEnvFile (app.envLinesFor nixosConfigurations.<host>.config)
```

Ланцюг даних «конфіг хоста → env у бандлі»: `machines/<host>/modules/flake-services/rt-speech.nix` → `nixosConfigurations.<host>.config.services.rtSpeech.<svc>` → `app.envLinesFor` → `pkgs.rtSpeech.lib.envLines` (та сама функція, що модуль використовує для `Environment=`) → `$PROFILE/env` → `EnvironmentFile=-/var/lib/rt-<svc>/current/env`. Один маппінг опція→env-змінна (у репо підпроєкту 2), як у llama.

## 5. Компоненти (swarm-nix)

### 5.1 Flake input + overlay — `system-conf/flake.nix`
```nix
inputs.realtime-translation = {
  url = "github:DohuyaMa/realtime-translation";
  inputs.nixpkgs.follows = "nixpkgs";
};
# outputs: додати realtime-translation до деструктуризації аргументів
pkgs = import nixpkgs {
  inherit system;
  config = pkgsConfig;
  overlays = [ inputs.realtime-translation.overlays.default ];
};
```
- Overlay **лише додає** атрибут `rtSpeech`; жодних override існуючих nixpkgs-атрибутів (вимога R1) — інакше змінились би drvPath llama і всіх хостів. Перевіряється порівнянням drvPath (критерій 1).
- `follows` — щоб не тягнути другий nixpkgs у lock. Інші inputs rt-flake (`home-manager`, `flake-utils`) — `follows` на відповідні swarm inputs, **якщо** вони лишаться в rt-flake після підпроєкту 2 (вирішується в Task плану за фактичним `nix flake metadata`).
- **Розповсюдження — тільки git (overview §4.5a).** Єдине джерело версії rt-коду для всього флоту — `flake.lock` у git swarm-nix. Контролери (cyborg, SKTOP-NOTE27-NIXOS — обидва з `allowLocalDeployment` і власним Django/clusterops) роблять `git pull` swarm-nix і отримують той самий `narHash`/`rev` → однакові derivations на будь-якому таргеті. `path:`/`git+file:`-inputs і `--override-input` у робочому процесі деплою **заборонені** (локальний шлях існує лише на одній машині — саме це зламало note27 за старим `todo/todo.md`).
- **Release flow:**
  1. У цьому репо: commit + `git push` (опційно `git tag rt-vX.Y.Z && git push --tags`).
  2. У `swarm-nix/system-conf`: `nix flake update realtime-translation` (або загальний `nix flake update` за правилом власника, nix.md §5).
  3. `nix flake check --no-build` → commit `flake.lock` (`chore(flake): bump realtime-translation to <short-rev>`) → `git push`.
  4. Деплой (colmena/deploy-rs/clusterops) з будь-якого контролера після `git pull`.
  Фіксація релізу (опційно): `url = "github:DohuyaMa/realtime-translation?ref=rt-vX.Y.Z"` або `?rev=<sha>`; за замовчуванням — default branch + lock-пін, бо lock і так фіксує ревізію.
- **Перевірка консистентності:** `nix flake metadata --json | jq -r '.locks.nodes."realtime-translation".locked | "\(.type) \(.rev)"'` → має бути `github <sha>` на обох контролерах; тип ≠ `github` (path/git+file) — помилка, lock не комітиться.
- **Розробка самого rt-коду** відбувається і перевіряється в цьому репо (`nix build .#rt-asr-gpu`, `nix flake check` тут, тести підпроєктів 1/2) до push; в інфру потрапляє лише запушена ревізія.
- Наслідок для rt-flake (вимога R7): git-flake бачить лише tracked-файли — усе для збірки закомічене, жодних абсолютних `/home/...`, моделі не в репо.
- clusterops-деплой (`deploy_executor`) працює з тим самим закоміченим lock — додаткових механізмів не потрібно; відомий розрив «`deploy_service` не робить flake update сам» (nix.md §5) лишається поза scope.

### 5.2 `common/modules/rt-speech.nix` (новий, 3 рядки)
```nix
{ inputs, ... }: { imports = [ inputs.realtime-translation.nixosModules.rt-speech ]; }
```
Аналог `../../common/modules/llm/llama-service.nix` в `imports` хостів: тримає посилання на input в одному місці, а не в кожному `configuration.nix`. Імпортується **лише** хостами, що мають rt-сервіси (прецедент llama — per-host).

### 5.3 Узагальнений env-merge — `applications/lib/mkDeployProfiles.nix`, `mkEnvFile.nix`
Новий контракт запису застосунку: опційний атрибут `envLinesFor :: hostConfig -> [string] | null`.
- `null` → бандл без `env` (поточна поведінка для застосунків без hook).
- `mkEnvFile` стає generic: `lines: pkgs.writeTextDir "env" (lib.concatStringsSep "\n" lines + "\n")` — той самий вираз, що зараз, лише без імпорту `env-lines.nix`.
- `mkDeployProfiles`:
  ```nix
  hostConfig = nixosConfigurations.${hostname}.config;
  envLines = if entry.app ? envLinesFor then entry.app.envLinesFor hostConfig else null;
  package = if envLines != null
    then pkgs.symlinkJoin { name = "${entry.app.name}-${hostname}-with-env";
                            paths = [ entry.app.package (mkEnvFile envLines) ]; }
    else entry.app.package;
  ```
- Llama: новий `applications/llm/env.nix` = `{ lib }: hostConfig: let cfg = hostConfig.services.llamaService or null; in if cfg == null then null else import ../../common/modules/llm/lib/env-lines.nix { inherit lib; } cfg;`; у `llm/{cpu,gpu,rocm}.nix` `mergeEnvFromLlamaService = true;` → `envLinesFor = import ./env.nix { inherit lib; };`. Ім'я `symlinkJoin`, ім'я `writeTextDir "env"` і текст — ті самі ⇒ drvPath ідентичний (перевіряється до/після).
- Різниця в поведінці лише для неіснуючого хоста в registry: раніше `or null` мовчки давав бандл без env, тепер eval-помилка «attribute missing» — явна помилка замість тихого дефолту (CLAUDE.md §5). Для всіх наявних хостів вихід ідентичний.

### 5.4 `applications/lib/mkRtSpeechApplication.nix` + `applications/services/rt-<svc>-<variant>.nix`
Хелпер (єдине місце rt-специфіки в інфрі):
```nix
{ pkgs, lib }:
{ svc, variant }:            # svc ∈ asr|mt|tts, variant ∈ cpu|gpu|rocm
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
  healthCheck.command = ''
    port=$(sed -n 's/^RT_PORT=//p' ${stateDir}/current/env)
    [ -n "$port" ] || { echo "RT_PORT missing in ${stateDir}/current/env"; exit 1; }
    curl -sf "http://127.0.0.1:$port/health" > /dev/null
  '';
} // {
  envLinesFor = hostConfig:
    let cfg = hostConfig.services.rtSpeech.${svc} or null;
        host = hostConfig.networking.hostName;
    in
    if cfg == null || !cfg.enable then
      throw "rt-${svc}-${variant} targets ${host}, but services.rtSpeech.${svc}.enable is not true there (machines/<dir>/modules/flake-services/rt-speech.nix)"
    else if cfg.device != device then
      throw "rt-${svc}-${variant} (device ${device}) targets ${host}, but services.rtSpeech.${svc}.device = ${cfg.device}"
    else if !(builtins.elem cfg.host [ "127.0.0.1" "0.0.0.0" ]) then
      throw "rt-${svc} on ${host}: host must be 127.0.0.1 or 0.0.0.0 (deploy-rs health check uses loopback)"
    else pkgs.rtSpeech.lib.envLines svc cfg;
}
```
- Health check читає порт з env поточного профілю → per-host `port` працює без хардкоду; відсутній `RT_PORT` — явна помилка (вимога R2: `envLines` завжди емітить `RT_PORT`).
- Три `throw` перетворюють тихі runtime-розбіжності (сервіс вимкнено / не той варіант / bind на tailscale-IP) на eval-помилку на етапі `nix flake check`/деплою.
- Файли сервісів — по одному на ім'я Service (контракт NixGen `./services/<svc.name>.nix`), кожен — 3 рядки:
  ```nix
  # applications/services/rt-asr-gpu.nix — deploy-rs app for clusterops Service "rt-asr-gpu"
  { pkgs, lib }:
  import ../lib/mkRtSpeechApplication.nix { inherit pkgs lib; } { svc = "asr"; variant = "gpu"; }
  ```
  Створюємо **всі 9** (`rt-{asr,mt,tts}-{cpu,gpu,rocm}`): файл без Service у registry не evaluates і нічого не коштує, зате будь-яку комбінацію можна задеплоїти через clusterops UI/API без правки Nix (North Star). Якщо підпроєкт 2 не експортує якийсь варіант — файл для нього **не** створюється (рішення в Task за фактичним `nix eval`).

### 5.5 clusterops (Django) — виправлення перед створенням Service
1. **Importer (F4, блокер):** `import_all` для **існуючого** Service не перезаписує `service_type` і `config` (DB = SSOT; registry — похідне); для **нового** імені тип = `llm-<v>`, лише якщо `llm-<v>` стоїть у імені окремим dash-сегментом (`re.search(r"(?:^|-)llm-(cpu|gpu|rocm)(?:-|$)", name)`), інакше `custom`. Тест: registry-ключ `rt-asr-gpu` → `custom`; наявний CUSTOM-рядок не перекласифіковується; наявний тест з `_uniq("llm-cpu")` (= `zzqvex-llm-cpu-<hex>`) лишається зеленим.
2. **`_profile_name` (F5):** завжди `service.name` (= ключ registry = ключ `deploy.nodes.<host>.profiles`). Для живих LLM-рядків ім'я == тип, тому нічого не змінюється; тест `profile_name == "llm-gpu"` переписується на `service.name`.
3. **Регенерація реєстру (F6):** ключ `generate_all()` → `"applications/registry.nix"`; B3-цикл з `hostname_rename` виноситься в `NixGenerator.regenerate_generated_file(path)` (оновлює `NixFile.source` з генератора + `drift.write_to_disk`; відсутній `NixFile` → явна помилка) і використовується і `hostname_rename`, і новим `manage.py generate_infra --regenerate applications/registry.nix` (без виклику `generate_nix_files()`, F7).
4. **Serializer:** додати `flake_module` у `ServiceSerializer.fields` (traceability-поле зараз не задається через API).

`ServiceType` **не** розширюється (обґрунтування — §3.1 C).

Service-рядки (по одному на використаний варіант):

| Поле | Значення (приклад `rt-asr-gpu`) |
|---|---|
| `name` | `rt-asr-gpu` (= ключ registry = deploy-rs профіль = ім'я файлу) |
| `service_type` | `custom` |
| `deploy_backend` | `deploy_rs` |
| `state_dir` | `/var/lib/rt-asr` |
| `service_name` | `rt-asr` (systemd unit) |
| `health_check_cmd` | `curl -sf http://127.0.0.1:8091/health` (traceability; справжня перевірка — у Nix, §5.4) |
| `activation_script` | `systemctl restart rt-asr` (traceability; дефолт моделі — llama-specific) |
| `magic_rollback_enabled` | `true` |
| `flake_module` | `applications/services/rt-asr-gpu.nix` |
| targets | `{ node: <cyborg pk>, ssh_user: "deploy", target_user: "root" }` |

### 5.6 Per-host конфіг — `machines/<dir>/modules/flake-services/rt-speech.nix`
```nix
# machines/<host>/modules/flake-services/rt-speech.nix   (приклад: cyborg)
# NOTE: no top-level `enable` — flake-services/default.nix only forwards
# systemd.services/timers/deployment.keys of files with `enable = true`;
# this file is a real NixOS module imported explicitly from configuration.nix.
{ ... }:
{
  services.rtSpeech.asr = {
    enable = true;
    deployRsPath = "/var/lib/rt-asr/current";
    host = "0.0.0.0";          # reachable via tailscale0 (trusted fleet-wide); other ifaces stay firewalled
    openFirewall = false;      # tailscale0 already trusted in common/modules/security/firewall.nix
    device = "cuda";
    model = "whisper-large-v3-turbo";   # alias from sub-project 5 registry; budget spec §7
    computeType = "int8_float16";
  };
  # EXAMPLE only — placement is clusterops data (spec §7); add more services after the admission check:
  # services.rtSpeech.mt = { enable = true; deployRsPath = "/var/lib/rt-mt/current"; host = "0.0.0.0";
  #   openFirewall = false; device = "cuda"; model = "nllb-200-distilled-600M-ct2-int8"; computeType = "int8_float16"; };
}
```
`configuration.nix` хоста отримує два імпорти: `../../common/modules/rt-speech.nix` і `./modules/flake-services/rt-speech.nix`. На хостах без агрегатора (`SKTOP-NOTE17-NIXOS`, `sktop-nixos-top`, `kira-kiosk`) шаблон той самий. Значення `model` — **alias-и реєстру** підпроєкту 5 (`whisper-large-v3-turbo`, `whisper-small`, `nllb-200-distilled-600M-ct2-int8`, `kokoro-82m-v1.0-onnx`) або абсолютний шлях; довільні HF repo id сервіси не приймають (SP5 D5). `beamSize`/`numBeams` не задаються — device-залежні дефолти Python.

`openFirewall = false` — свідомо: Tailscale trust model флоту (`trustedInterfaces = [ "lo" "tailscale0" ]`) — єдиний механізм доступу, другий (per-interface allowedTCPPorts) не додаємо. Перевірка: `networking.firewall.allowedTCPPorts` хоста не містить 8091–8093.

### 5.7 Edge на cyborg (Home Manager)
Закоментований `inputs.realtime-translation.homeManagerModules.rt-translator` у `machines/cyborg/system.nix` замінюється робочим імпортом з `rt-translator.endpoints.{asr,mt,tts}` і `mode` (опції — overview §4.4). Це остання задача плану, залежить від готовності HM-модуля підпроєкту 2; без неї e2e «мікрофон → переклад → динамік» не доведено.

### 5.8 CUDA substituter на CUDA-хостах (`common/modules/nix/cuda-substituter.nix`)
`rt-*-gpu` бандли (і `bench-gpu` підпроєкту 5) збираються з окремого `cudaSupport`-інстансу nixpkgs (SP2 `variant-pkgs.nix`); усі важкі CUDA-пакети є лише в `cache.nixos-cuda.org` (SP5 F7). deploy-rs будує на таргеті (`--remote-build`) як `deploy`, а звичайні користувачі не trusted (`trusted-users = root deploy`) — тому `nixConfig`/`--option extra-substituters` не діють, substituter має бути в **системних** `nix.settings`:
```nix
# common/modules/nix/cuda-substituter.nix — import on every CUDA-class host
{ ... }:
{
  nix.settings.extra-substituters = [ "https://cache.nixos-cuda.org" ];
  nix.settings.extra-trusted-public-keys = [ "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M=" ];
}
```
Розкочується colmena (`colmena apply --on <cuda-host>`) / self-deploy `nixos-rebuild switch` **до першого** деплою будь-якого `rt-*-gpu` на цьому хості (план Task 7a). Змінює лише toplevel CUDA-хостів (llama-профілі — без змін).

## 6. Обробка помилок і явні відмови

| Ситуація | Поведінка |
|---|---|
| Хост у registry, але `services.rtSpeech.<svc>.enable` ≠ true / не той device / bind ≠ loopback|any | `throw` під час eval (`nix flake check`, deploy-rs) з іменем хоста і файлу для виправлення |
| `RT_PORT` відсутній у env | health check падає з явним повідомленням → deploy-rs rollback (або §8.4 при першому деплої) |
| `/health` не відповів за 20 с | deploy-rs failure; `/health` за контрактом відповідає одразу після старту uvicorn, модель вантажиться у фоні (вимога R4) |
| Юніт до першого деплою (нема `current`) | `ConditionPathExists` (вимога R5) → юніт пропускається, не `failed`, не вичерпує `StartLimitBurst` |
| `import_infra` бачить `rt-*` | тип не змінюється (виправлення 5.5.1) |
| `NixFile` ноди `divergent` після ручної правки | clusterops `deploy()` відмовляє (F9) → `import_infra` або Adopt у `/cluster/config/?tab=artifacts` |
| Overlay підпроєкту 2 змінив існуючі пакети | drvPath llama/хостів змінився → Task-перевірка падає до будь-якого деплою |

## 7. Розміщення: загальна процедура (cluster-generic, overview §7 C0)

**Розміщення — це дані, не дизайн.** Який сервіс на якому хості — рядки clusterops (Service × deploy targets) і per-host `services.rtSpeech.<svc>.enable`; змінюється будь-коли без змін коду чи spec. Будь-який хост може запускати будь-який сервіс у будь-якому варіанті, який підтримує його залізо. Імена хостів нижче — лише **приклади**.

### 7.1 Клас заліза → дозволені варіанти
| Клас хоста | Варіанти | Примітка |
|---|---|---|
| NVIDIA (CUDA) | `rt-*-gpu`, `rt-*-cpu` | потрібен `cache.nixos-cuda.org` у системних `nix.settings` (§5.8) до першого `-gpu` деплою |
| AMD ROCm | `rt-{asr,mt}-rocm` (experimental, лише після GO ROCm-spike SP5 Task 22 для цього GPU), `rt-*-cpu` | `rocmGfxVersion` за потреби (`HSA_OVERRIDE_GFX_VERSION`); `rt-tts-rocm` не існує |
| CPU-only | `rt-*-cpu` | real-time ASR — лише якщо admission check (CPU RTF) пройдено; інакше batch (фаза 4) |

### 7.2 Модель — за класом заліза / бюджетом, не за hostname
Defaults беруться з Python (SP5 §4.3/§4.8: `RT_DEVICE` → модель, compute type, beams). Per-host override — опціями `services.rtSpeech.<svc>.{model,computeType,extraEnv}` (alias-и реєстру SP5). Вибір за вільним бюджетом `F` пристрою — правило SP5 §4.6.

### 7.3 Admission check (обов'язковий для кожного нового/зміненого розміщення)
SP5 harness `system`-gate, параметризований хостом: усі сервіси, що будуть co-located на цьому пристрої (+ llama з завантаженою моделлю, гірший випадок), під 5-хв навантаженням. Поріг: пік ≤ **90 % VRAM пристрою** (GPU) / ≤ 90 % RAM (CPU), без OOM, RTF у межах порогів SP5 §6.4. FAIL → драбина SP5 §4.6 (менша модель → CPU-варіант → інший хост). Після деплою — повторний живий вимір (`nvidia-smi` / `free`), той самий поріг.

### 7.4 Додати / перенести сервіс
1. Перевірити клас заліза (§7.1) і пройти admission check (§7.3) для `<host>`.
2. `machines/<host>/modules/flake-services/rt-speech.nix`: `services.rtSpeech.<svc> = { enable = true; deployRsPath = "/var/lib/rt-<svc>/current"; host = "0.0.0.0"; openFirewall = false; device = …; }` + 2 імпорти в `configuration.nix` (§5.6); для `cuda` — імпорт `common/modules/nix/cuda-substituter.nix`.
3. colmena `apply --on <host>` (або `nixos-rebuild switch` для self-deploy).
4. clusterops API: Service `rt-<svc>-<variant>` (якщо ще нема) + target `<host>` (§5.5) → `generate_infra --regenerate applications/registry.nix` → commit → `import_infra`.
5. deploy-rs `.#<host>.rt-<svc>-<variant>` → health → smoke → живий вимір (§7.3).
6. Перенесення = додати на новому хості (1–5) → перемкнути edge-endpoint → видалити target на старому хості в clusterops + `enable = false` → регенерація registry.

### 7.5 Приклади (ілюстративні, не рішення)
- *Приклад* cyborg (RTX 4060 8188 MiB, дисплей на Intel iGPU): виміряний бюджет SP5 — llama ~3.9 + `rt-asr-gpu` (turbo) ~1.3 + `rt-mt-gpu` (NLLB CT2) ~0.9 ≈ 6.1 GiB ≤ 90 % → ASR+MT можуть бути co-located з llama.
- *Приклад* CPU-бокс із вільними ядрами: `rt-tts-cpu` (Kokoro ORT, ~1.0–1.3 GB RAM).
- *Приклад* ROCm iGPU (gfx90c): CPU-варіанти, доки spike не дав GO.

Edge (HM) може бути на будь-якій машині з мікрофоном; `rt-translator.endpoints.*` — URL обраних хостів (loopback, якщо сервіс на тому ж хості). Обґрунтування для latency: ASR — найчутливіший до RTT, тож бажано на edge-хості або direct-peer; TTS толерантний до relay.

## 8. Rollout

### 8.1 Порядок (кожен крок — окремий commit у swarm-nix)
1. **Передумова:** підпроєкт 2 закомічено й запушено в `github:DohuyaMa/realtime-translation` (git — єдиний канал, overview §4.5a); `nix flake show github:DohuyaMa/realtime-translation` показує `overlays.default`, `nixosModules.rt-speech`, `lib.rtSpeech.envLines`; вимоги R1–R5 виконано.
2. clusterops-виправлення (5.5) + тести → restart `obsidian-django.service` на cyborg і note27 (код Django не в store, nix.md §3).
3. Прибирання застарілого (§10) — незалежне, можна паралельно з 2.
4. `flake.nix` input+overlay, generic env hook, llama-міграція → **перевірка drvPath до/після** → commit.
5. `mkRtSpeechApplication.nix`, 9 (або менше, 5.4) `applications/services/*.nix`, `common/modules/rt-speech.nix` → commit.
5a. CUDA substituter (§5.8) на cyborg і SKTOP-NOTE27-NIXOS → `nixos-rebuild switch` / `colmena apply` → перевірка `nix config show substituters` → commit. **До** будь-якого `rt-*-gpu` деплою.
6. Per-host `rt-speech.nix` + імпорти для першого `<host>` (приклад: cyborg, `rt-asr`) → `nix build …toplevel` → commit → `import_infra`.
7. Service `rt-asr-gpu` + target через API → `generate_infra --regenerate applications/registry.nix` → `nix flake check --no-build` → commit → `import_infra`.
8. Перший деплой на `<host>` (з контролера після `git pull` swarm-nix): `nix flake update` → commit+push `flake.lock` → `nix flake check` → `sudo nixos-rebuild switch --flake .#cyborg` (self-deploy, nix.md §5; для віддалених хостів — `colmena apply --on <node>`) → `deploy-rs --dry-activate` → `deploy-rs` (CLI або clusterops API deploy) → smoke.
9. Наступні сервіси/хости — за процедурою §7.4 (admission check §7.3).
10. Фаза B — токен (8.3).
11. Edge HM на cyborg (5.7), e2e.
12. Документація swarm-nix (AGENTS.md machines-таблиця, `machines/README.md`, `.claude/rules/nix.md` §2/§5 «зараз лише llm-*», `context/tasks.md`).

### 8.2 Colmena перед deploy-rs
Colmena створює юзера `rt-<svc>`, `/var/lib/rt-<svc>`, юніт з `ConditionPathExists`. deploy-rs до першого colmena — `Permission denied`/відсутній юніт (nix.md §7 «не роби» 1). Зміна лише значень `services.rtSpeech.*` (модель, мова) далі доходить deploy-rs-only пушем через env-merge, як у llama; зміна структури юніта (нова опція модуля, токен) — потребує colmena.

### 8.3 Фаза B — токен
1. Для кожного хоста з rt-сервісом переконатися, що в clusterops є рядок `<node>-ssh-host-ed25519-public` (без нього `generate_secrets` повертає `None`, F12).
2. `POST /api/clusterops/secrets/` — `name="<node>-rt-api-token"` (ім'я глобально унікальне), `target_path="/run/secrets/rt-api-token"`, `file_mode="0400"`, `file_owner="root"`, однакове значення для всього флоту (edge використовує один токен на три endpoint-и).
3. Регенерація секретів ноди (UI `node_regenerate_secrets` / `generate_secrets`), імпорт `./secrets-module.nix` хостом (`lib.optional (builtins.pathExists ./secrets-module.nix)`), `apiTokenFile = "/run/secrets/rt-api-token"` у `rt-speech.nix` → colmena → deploy-rs.
4. На edge-машині — окремий Secret з `file_owner = "<user>"` для HM-клієнта.
5. Ротація: новий value → регенерація → colmena; `restart_units` через API не задається (serializer) — після ротації `systemctl restart rt-asr rt-mt rt-tts` (або задати `restart_units` в UI).

### 8.4 Перший деплой і rollback (F1)
При першому деплої профілю попередньої генерації немає: якщо health check впаде, auto-rollback deploy-rs сам завершиться помилкою, лишивши `current` на зламаний профіль. План відновлення: `systemctl stop rt-<svc>; rm /var/lib/rt-<svc>/current; systemctl reset-failed rt-<svc>` → виправити → повторити. Зменшення ризику: `--dry-activate` і `nix build` профілю на цільовій машині перед першим справжнім деплоєм; `systemctl reset-failed rt-<svc>` перед деплоєм (nix.md §7 «не роби» 3).

## 9. Перевірка (testing)

- **Django:** `nix develop ./flake --command bash -c "cd django_api && python -m pytest clusterops/tests/test_nixgen.py clusterops/tests/test_deploy_service.py clusterops/tests/test_hostname_rename.py -v"` — нові тести на importer (rt не перекласифіковується), `_profile_name`, `regenerate_generated_file`, ключ `applications/registry.nix`.
- **Nix eval:** `nix flake check --no-build`; `nix eval --raw .#deploy.nodes.<host>.profiles.<p>.path.drvPath` для п'яти llama-профілів — ідентичні до/після; `nix eval .#nixosConfigurations.<host>.config.networking.firewall.allowedTCPPorts` — без 8091–8093; навмисно невідповідний `device` дає очікуваний `throw` (перевірка через `nix eval` у scratch-гілці, не коміт).
- **Build:** `nix build .#nixosConfigurations.<host>.config.system.build.toplevel` для кожного зміненого хоста; профіль rt будується на таргеті (`--remote-build`).
- **Deploy:** `nix run .#deploy-rs -- .#cyborg.rt-asr-gpu --dry-activate`, потім справжній; `systemctl is-active rt-asr`; `journalctl -u rt-asr -n 50`.
- **Smoke по Tailscale (з іншої машини):** `/health` = 200; `/status` `ready:true` (з очікуванням завантаження моделі); ASR `POST /v1/audio/transcriptions` з wav-файлом; MT `POST /v1/translate`; TTS `POST /v1/audio/speech` → валідний WAV; з LAN-IP — з'єднання відхилено/таймаут; у фазі B — без `Authorization` → 401, з токеном → 200, `/health` без токена → 200.
- **VRAM/RAM:** на кожному хості з rt-сервісами — живий вимір з завантаженою llama і всіма co-located сервісами під навантаженням ≤ 90 % пристрою (§7.3; той самий поріг, що SP5 `system`-gate).

## 10. Прибирання застарілого

| Артефакт | Дія |
|---|---|
| `machines/cyborg/modules/services/realtime-translation.nix` | видалити (не імпортується; посилається на неіснуючий `../../../flake-global/prod/packages.nix`) |
| `machines/sktop-note27-nixos/modules/realtime-translation.nix` + рядки в `machines/sktop-note27-nixos/README.md:16`, `modules/README.md:11` | видалити файл і згадки |
| `common/modules/services/wyoming-faster-whisper.nix` + `#./modules/services/wyoming-faster-whisper.nix` у `common/nixos-config.nix` | видалити (Wyoming відхилено як основний шлях, overview §2) |
| `machines/cyborg/system.nix` закоментовані `rt-translator` рядки | замінити робочим імпортом у Task edge (5.7); до того — видалити |
| `system-conf/todo/todo.md` | видалити **лише** секцію «real-time-transletor на skotop-note27-nixos» (path-input, клонування репо на кожну машину, rsync/USB-варіанти) — вона суперечить git-only розповсюдженню (§5.1); Yggdrasil-секцію лишити |
| `system-conf/README.md:262-263` (`nix flake lock --update-input realtime-translation` як крок self-deploy cyborg) | замінити на release flow (§5.1): оновлення input — лише `nix flake update realtime-translation` + commit/push lock, не на кожен rebuild; у розділ про deploy-rs додати rt-приклад |
| `system-conf/context/tasks.md` Wave 2c RTT-1..4 | замінити посиланням на цей spec/plan (RTT-1/2/3 покриваються, RTT-4 = edge HM) |
| `django_api/context/technical-debt.md` TD-119 | закрити як superseded (Wyoming не деплоїться; CUDA для speech — всередині `pkgs.rtSpeech`, overlay `common/overlays/cuda.nix` лишається як є під TASK-CLUSTER-07) |
| `common/nixos-base.nix:12-13` коментар «e.g. wyoming-faster-whisper, TD-119» | переписати без Wyoming |

Паркан Честертона: усі файли перевірено — жоден не імпортується активно (`grep` imports), функціональність замінюється rt-speech; `cuda.nix` overlay **не** видаляється (є окремий власник — TASK-CLUSTER-07).

## 11. Deviations from overview

1. **`pkgs.rtSpeech.lib.envLines`** — overlay додатково експонує ту саму функцію, що `lib.rtSpeech.envLines` (alias), бо registry-файли бачать лише `{ pkgs, lib }` (той самий аргумент, з якого overview обрав overlay для пакетів). Сигнатура: `envLines :: svc -> cfg -> [string]` (svc ∈ `asr|mt|tts`, cfg = `config.services.rtSpeech.<svc>`).
2. **`openFirewall = false` на всіх хостах** — опція модуля лишається за контрактом, але інфра її не вмикає: `tailscale0` уже trusted fleet-wide (F11), другий механізм не додаємо.
3. **Не всі 9 варіантів отримують Service** — лише ті, що мають target (розміщення — дані clusterops, §7); app-файли — для всіх експортованих варіантів.
4. Overview §2 називає `mergeEnvFromLlamaService` «треба узагальнити» — узагальнення зроблено hook-функцією `envLinesFor`, а не опцією в registry.

## 12. Вимоги до підпроєкту 2 (інтерфейс, від якого залежить цей план)

- **R1** `overlays.default` лише додає `rtSpeech` (`{asr,mt}.{cpu,gpu,rocm}`, `tts.{cpu,gpu}` + `lib.envLines`); CUDA — окремий `cudaSupport`-інстанс, створений усередині пакета з `pkgs.path` (SP2 `variant-pkgs.nix`), ROCm — override CT2 усередині пакета; жодних глобальних `python313`/`ctranslate2`/`torch` override (llama byte-identical).
- **R2** `envLines svc cfg` завжди емітить `RT_HOST`, `RT_PORT`, `RT_DEVICE`; значення без секретів (лише шляхи).
- **R3** `apiTokenFile` читається через `LoadCredential=api-token:<apiTokenFile>`; у env — **літеральний** шлях `RT_API_TOKEN_FILE=/run/credentials/rt-<svc>.service/api-token` (systemd не розгортає `%d` у вмісті `EnvironmentFile`). Так один `root 0400` sops-файл обслуговує три system-users.
- **R4** `/health` відповідає ≤ ~10 с після `systemctl restart` на найслабшому CPU-хості (важкі імпорти torch/ctranslate2 — після старту uvicorn).
- **R5** Юніт має `unitConfig.ConditionPathExists = "${deployRsPath}/bin/rt-<svc>"`; модуль не додає порт у глобальний `allowedTCPPorts` при `openFirewall = false` незалежно від `host`.
- **R6** Ім'я юніта/юзера/stateDir — точно за overview §4.1 (`rt-asr`, `/var/lib/rt-asr`), бо їх хардкодить `mkRtSpeechApplication`.
- **R7** rt-flake збирається з чистого git-checkout (overview §4.5a): `nix build github:DohuyaMa/realtime-translation/<rev>#rt-asr-cpu` проходить без локальних файлів.

## 13. Ризики

| Ризик | Мітігація |
|---|---|
| Повний `nix flake check` будує rt-бандли локально через deploy-rs activate-check (F14) — torch/CUDA години | Для eval — `nix flake check --no-build`; рішення про повний check — Q4 |
| Збірка CUDA/torch на таргеті довга (прецедент llm-gpu timeout) | `--remote-build`; перший деплой — CLI з ручним моніторингом, не clusterops-кнопка |
| VRAM OOM на хості з co-located llama + rt-* | Admission check §7.3 до і після деплою; драбина SP5 §4.6 (менша модель → CPU → інший хост); llama `idleUnloadTimeout` |
| Збірка CUDA-бандлів з сорців (немає substituter на таргеті) | §5.8 / Task 7a до першого `rt-*-gpu` деплою |
| CPU-конкуренція rt-*-cpu з llama на спільному хості (R-CPU) | Admission check §7.3 (RTF), моніторинг; перенесення за §7.4 |
| relay-латентність до хостів через DERP (приклад: sktop-nixos-top) | ASR бажано на edge-хості/direct-peer; TTS толерантний |
| Контролери розходяться в ревізії rt (один не зробив `git pull`) | lock у git — єдине джерело; перевірка `locks.nodes."realtime-translation".locked.rev` у плані перед кожним деплоєм; clusterops `deploy()` уже фіксує `git_rev` swarm-nix у `DeployEvent` |
| `config={}` clobber у `import_all` (знайдено попутно) | Виправляється тим самим пунктом 5.5.1 (не перезаписувати існуючі) |

## 14. Відкриті питання (рішення прийняті як припущення; власник може перевизначити)

- **Q1** ~~Чи репо публічне?~~ Вирішено (overview §4.5a): публічне, лише `github:`-input. Відкрито лише: чи вводити обов'язкові теги `rt-vX.Y.Z` (`?ref=`) — припущення: ні, достатньо lock-піну.
- **Q2** Невідомий VRAM/модель GPU на деяких хостах (приклад: SKTOP-NOTE27-NIXOS) — вирішується не тут, а admission check §7.3 при спробі розмістити там сервіс.
- **Q3** ~~Чи збирається CTranslate2-ROCm~~ — збірка штатна (nixpkgs `rocmSupport`, C1); відкрито лише, чи стабільно працює на gfx90c під `HSA_OVERRIDE_GFX_VERSION=9.0.0` (SP5 Task 22). Припущення: ROCm-хости використовують `-cpu` до GO.
- **Q4** Чи прийнятно, що повний `nix flake check` (правило власника перед деплоєм) тепер будує rt-бандли? Припущення: для rt-деплоїв достатньо `--no-build` + `--remote-build`; потрібне підтвердження власника.
- **Q5** Чи має збереження/зміна Service чи target у clusterops **автоматично** перегенеровувати `applications/registry.nix` (зараз — ніхто, лише hostname rename)? Припущення: поки ручний виклик `generate_infra --regenerate`; автоматизація — окрема задача clusterops.
- **Q6** MagicDNS vs Tailscale-IP для edge-endpoint-ів. Припущення: IP, як у llama-конфігах.
- **Q7** Edge — будь-яка машина з мікрофоном і HM (приклади: cyborg, kira-kiosk); не фіксується.
- **Q8** Чи вмикати токен (фаза B) до того, як sops-поставка має бодай один робочий прецедент у флоті? Припущення: так, rt — перший споживач; якщо хост не має pubkey-рядка — лишається у фазі A явно.
