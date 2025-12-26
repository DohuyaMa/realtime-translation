Відповідь по пунктах, без «води», з архітектурною оцінкою.

---

## 1. Посилання на `/tmp/rt-*.sock` — чи це правильно

**Коротко:** так, але з уточненнями.
**Правильно лише для dev / user-mode. Для production — ні.**

### Поточна ситуація

```python
IPCClient("/tmp/rt-playback.sock")
```

Це:

* працює в `nix develop`
* працює для user-level systemd (`systemctl --user`)
* зручно для локальної розробки

### Проблеми `/tmp`

* очищується при ребуті
* не namespace-aware
* погано контролюється з точки зору security
* не відрізняє user / system інстанси

### Рекомендована модель

| Режим    | Socket path                              |
| ------------- | ---------------------------------------- |
| dev / user    | `$XDG_RUNTIME_DIR/rt/rt-playback.sock` |
| prod / system | `/run/rt-translator/rt-playback.sock`  |

#### Чому `/run`

* tmpfs
* керується systemd
* гарантовано існує
* стандарт для system-сервісів

### Як це має виглядати в коді

```python
import os

RUNTIME_DIR = os.environ.get(
    "RT_RUNTIME_DIR",
    os.environ.get("XDG_RUNTIME_DIR", "/run/rt-translator")
)

SOCKET_BASE = os.path.join(RUNTIME_DIR, "rt")

self.playback_client = IPCClient(f"{SOCKET_BASE}/rt-playback.sock")
```

А в systemd:

```ini
Environment=RT_RUNTIME_DIR=/run/rt-translator
```

---

## 2. systemd-файли: де їм місце

Твоя інтуїція  **абсолютно правильна** .

> ❝ додати ті файли systemd в flake-global/parts ❞
> ✔ **Так і треба**

### Чому НЕ залишати їх «як є»

* зараз це **opaque assets**
* Nix не контролює їх lifecycle
* немає параметризації (paths, user/system, env)

---

## 3. Як правильно інтегрувати systemd у flake-parts

### Структура (рекомендована)

```text
flake-global/
  parts/
    systemd/
      default.nix
      rt-capture.nix
      rt-whisper.nix
      rt-translate.nix
      rt-tts.nix
      rt-playback.nix
      rt-app.nix
```

### Приклад `rt-playback.nix`

```nix
{ lib, pkgs, config, ... }:

{
  systemd.user.services.rt-playback = {
    Unit = {
      Description = "RT Translator Playback Service";
      After = [ "pipewire.service" ];
    };

    Service = {
      ExecStart = "${pkgs.real-time-translator}/bin/rt-playback";
      Restart = "on-failure";
      Environment = [
        "RT_RUNTIME_DIR=%t/rt"
      ];
    };

    Install.WantedBy = [ "default.target" ];
  };

  systemd.user.sockets.rt-playback = {
    Socket = {
      ListenStream = "%t/rt/rt-playback.sock";
    };
    Install.WantedBy = [ "sockets.target" ];
  };
}
```

> `%t` = `$XDG_RUNTIME_DIR`
> **це systemd-native, а не «хак»**

---

## 4. flake-parts: як це зібрати

### `flake-global/parts/systemd/default.nix`

```nix
{
  imports = [
    ./rt-capture.nix
    ./rt-whisper.nix
    ./rt-translate.nix
    ./rt-tts.nix
    ./rt-playback.nix
    ./rt-app.nix
  ];
}
```

### Підключення в flake-parts

```nix
perSystem = { config, ... }: {
  imports = [
    ./parts/systemd
  ];
};
```

---

## 5. Home Manager vs NixOS — чіткий поділ

### User-mode (HM)

* dev
* desktop
* PipeWire
* UI
* sockets у `$XDG_RUNTIME_DIR`

### System-mode (NixOS)

* headless
* production
* `/run/rt-translator`
* GPU / CUDA
* isolation

👉 **Ті самі `parts`, але два entrypoints**

---

## 6. Публічний репозиторій

**Рекомендація: ТАК**

### Чому

* flake-parts + colmena = очікують відкритий source
* простіше CI
* легше dependency pinning
* приватні речі винести в:
  * `flake.lock` (ок)
  * окремий private flake з secrets
  * runtime config (`/etc/rt-translator/config.yml`)

---

## 7. Інтеграція в Colmena / HM / root flake

### Як це виглядатиме ззовні

```nix
{
  inputs.rt-translator.url = "github:you/real-time-translator";

  outputs = { rt-translator, ... }: {
    colmena = {
      meta.nixpkgs = import nixpkgs { system = "x86_64-linux"; };

      cyborg = {
        imports = [
          rt-translator.nixosModules.virtual-sinks
          rt-translator.nixosModules.rt-services
        ];
      };
    };
  };
}
```

---

## 8. Архітектурний висновок

Ти  **вже мислиш правильно** , бракує лише одного переходу:

> ❌ «systemd-файли як артефакти»
> ✅ **systemd як Nix-модулі**

Після цього:

* sockets → декларативні
* paths → контрольовані
* IPC → стабільний
* Colmena → чистий
* dev/prod → однакові

---

Якщо потрібно — наступним кроком можу:

* запропонувати **єдиний socket registry**
* показати **перехід user → system без зміни коду**
* або **спростити pipeline через templated units**

  flake-global/home-manager-module.nix

  Коротко:  **це вже дуже сильний варіант** , близький до «правильного Nix-native сервісного стеку». Але є кілька **критичних зауважень** і кілька  **архітектурних покращень** , які варто зробити зараз, а не пізніше.

  Нижче — структурований розбір.

  ---

  ## Що тут зроблено правильно (і це важливо)

  ### 1. **User-mode systemd + socket activation**

  ✔ Абсолютно правильне рішення для desktop / PipeWire / UI.


  * `%t/rt-*.sock` — **еталон**
  * `systemd.user.sockets` + `requires/after` — коректно
  * socket-first дизайн добре лягає на IPC pipeline

  Це значно краще, ніж `/tmp`.

  ---

  ### 2. **Обгортки через `writeShellApplication`**

  ✔ Добре як перехідний етап.

  * чітка точка входу
  * контроль інтерпретатора
  * працює і в HM, і в devShell

  ---

  ### 3. **Розділення сервісів**

  ✔ Архітектурно правильно:

  * capture / whisper / translate / tts / playback
  * окремі sockets
  * orchestration винесена

  Це вже  **мікросервісна архітектура** , просто локальна.

  ---

  ### 4. **PipeWire інтеграція**

  ✔ Добре, що:

  * є systemd-unit для virtual sinks
  * є xdg.configFile для pipewire
  * сервіс `oneshot + RemainAfterExit`

  ---

  ## Критичні зауваження (важливі)

  ### ❌ 1. `python.withPackages` у runtime сервісах — **архітектурно неправильно**

  Це  **головний мінус** .

  ```nix
  pythonEnv = pkgs.python313.withPackages (...)
  ```
  #### Чому це погано

  * не reproducible на рівні derivation
  * важко кешується
  * важко розділяти сервіси
  * CUDA / torch / whisper — **моноліт**
  * будь-яка зміна → rebuild всього

  #### Правильний напрямок

  **Кожен сервіс = окремий derivation**
  або мінімум — **групи сервісів**

  Наприклад:

  * rt-capture-env
  * rt-whisper-env (CUDA)
  * rt-tts-env
  * rt-ui-env

  > Те, що ти зараз зробив — нормально для прототипу,
  > але не для production.
  >

  ---

  ### ❌ 2. `PYTHONPATH` хардкодиться — це симптом

  ```nix
  Environment = [
    "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
  ]
  ```
  У  **правильному Nix-пакеті** :

  * `PYTHONPATH` **не потрібен**
  * entrypoint вже знає, де його залежності

  Це ще один сигнал, що код  **ще не «запакований»** , а лише «запущений».

  ---

  ### ❌ 3. `writeShellApplication` + `-m src.xxx` — технічний борг

  ```sh
  exec python -m src.whisper.whisper_service
  ```
  Це означає:

  * код не інстальований як пакет
  * немає `entry_points`
  * немає чіткої boundary між build і run

  **Наступний логічний крок:**

  * `buildPythonApplication`
  * `pyproject.toml`
  * console scripts:
    * `rt-whisper`
    * `rt-tts`
    * `rt-playback`

  Тоді:

  ```ini
  ExecStart=/nix/store/.../bin/rt-whisper --socket-path %t/rt/rt-whisper.sock
  ```
  ---

  ### ⚠ 4. `home.packages = [ pythonEnv … ]`

  Для сервісів:

  * **не потрібно**
  * достатньо, що вони доступні через systemd unit

  Краще:

  * `home.packages` → лише UI / CLI
  * сервіси → лише systemd

  ---

  ## Архітектурні покращення (рекомендовано)

  ### 1. Єдиний runtime dir

  Зараз:

  ```
  %t/rt-whisper.sock
  %t/rt-tts.sock
  ```
  Краще:

  ```
  %t/rt/whisper.sock
  %t/rt/tts.sock
  ```
  І в коді:

  ```python
  RT_RUNTIME = os.environ.get("RT_RUNTIME_DIR")
  ```
  ---

  ### 2. Винести socket names у конфіг

  ```yaml
  ipc:
    whisper: whisper.sock
    tts: tts.sock
  ```
  І тоді:

  * dev
  * prod
  * tests

  → **без змін коду**

  ---

  ### 3. Розбити цей файл

  Цей модуль  **занадто великий** .

  Мінімум:

  * `services/capture.nix`
  * `services/whisper.nix`
  * `services/tts.nix`
  * `services/playback.nix`
  * `pipewire.nix`
  * `python-env.nix`

  І зібрати через `imports`.

  ---

  ## Загальний висновок

  ### Статус

  * 🟢 **архітектурно правильний напрям**
  * 🟡 **реалізація ще dev/proto**
  * 🔴 **pythonEnv моноліт — вузьке місце**

  ### Якщо оцінювати жорстко

  > Це  **8/10 для dev** ,
  > **6/10 для production** ,
  > але  **9/10 за мислення** .
  >

  ---

  ### Рекомендований наступний крок (дуже конкретно)

  1. Зробити **один** сервіс через `buildPythonApplication`
  2. Прибрати `PYTHONPATH`
  3. Перевести **один** unit на чистий binary
  4. Подивитися, як це спрощує systemd

  **структуру parts/** для flake-parts саме під це



  Нижче —  **чітка, production-орієнтована структура `parts/` під flake-parts** , без абстракцій і без дублювання. Вона напряму лягає на твій проєкт (IPC, sockets, PipeWire, HM / NixOS).

  ---

  ## 1. Верхній рівень

  ```text
  flake-global/
  └── parts/
      ├── python/
      ├── services/
      ├── systemd/
      ├── pipewire/
      ├── runtime/
      └── defaults.nix
  ```
  `defaults.nix` — єдина точка зборки.

  ---

  ## 2. `parts/python/` — runtime derivations (НЕ env)

  ```text
  parts/python/
  ├── common.nix
  ├── capture.nix
  ├── whisper.nix
  ├── translate.nix
  ├── tts.nix
  ├── playback.nix
  └── ui.nix
  ```
  ### Призначення

  * **один файл = один сервіс**
  * `buildPythonApplication`
  * `console_scripts`

  ### Приклад `parts/python/whisper.nix`

  ```nix
  { pkgs }:

  pkgs.python313Packages.buildPythonApplication {
    pname = "rt-whisper";
    version = "0.1.0";
    src = ../../src;

    propagatedBuildInputs = with pkgs.python313Packages; [
      torch
      faster-whisper
      ctranslate2
      numpy
      soundfile
      loguru
    ];

    entryPoints = {
      console_scripts = {
        rt-whisper = "src.whisper.whisper_service:main";
      };
    };

    doCheck = false;
  }
  ```
  ---

  ## 3. `parts/runtime/` — paths, сокети, env

  ```text
  parts/runtime/
  ├── paths.nix
  ├── sockets.nix
  └── env.nix
  ```
  ### `paths.nix`

  ```nix
  {
    runtimeDir = "%t/rt";
  }
  ```
  ### `sockets.nix`

  ```nix
  {
    whisper = "whisper.sock";
    tts = "tts.sock";
    translate = "translate.sock";
    playback = "playback.sock";
    capture = "capture.sock";
  }
  ```
  ---

  ## 4. `parts/systemd/` — **тільки units**

  ```text
  parts/systemd/
  ├── user/
  │   ├── capture.nix
  │   ├── whisper.nix
  │   ├── translate.nix
  │   ├── tts.nix
  │   ├── playback.nix
  │   ├── app.nix
  │   └── default.nix
  └── sockets.nix
  ```
  ### `systemd/user/whisper.nix`

  ```nix
  { config, pkgs, lib, rt, ... }:

  {
    systemd.user.services.rt-whisper = {
      requires = [ "rt-whisper.socket" ];
      after = [ "rt-whisper.socket" ];

      serviceConfig = {
        ExecStart =
          "${rt.python.whisper}/bin/rt-whisper "
          + "--socket-path ${rt.runtime.dir}/${rt.sockets.whisper}";
        Restart = "always";
        Environment = rt.runtime.env;
      };

      wantedBy = [ "default.target" ];
    };
  }
  ```
  ---

  ## 5. `parts/systemd/sockets.nix`

  ```nix
  { rt, ... }:

  {
    systemd.user.sockets = {
      rt-whisper.socket = {
        socketConfig.ListenStream =
          "${rt.runtime.dir}/${rt.sockets.whisper}";
      };

      rt-tts.socket = {
        socketConfig.ListenStream =
          "${rt.runtime.dir}/${rt.sockets.tts}";
      };
    };
  }
  ```
  ---

  ## 6. `parts/pipewire/`

  ```text
  parts/pipewire/
  ├── config.nix
  └── service.nix
  ```
  ### `config.nix`

  ```nix
  {
    xdg.configFile."pipewire/pipewire.conf.d/30-rt.conf".text = ''
      context.modules = [
        { name = libpipewire-module-null-sink }
      ]
    '';
  }
  ```
  ---

  ## 7. `parts/services/` — логічні групи

  ```text
  parts/services/
  ├── core.nix        # capture → whisper → translate → tts → playback
  ├── ui.nix
  └── default.nix
  ```
  Тут —  **imports** , не логіка.

  ---

  ## 8. `defaults.nix` — єдина точка зборки

  ```nix
  {
    imports = [
      ./python
      ./runtime
      ./systemd
      ./pipewire
      ./services
    ];
  }
  ```
  ---

  ## 9. Як це підключається у flake-parts

  ```nix
  perSystem = { config, pkgs, ... }: {
    imports = [
      ./parts/defaults.nix
    ];

    _module.args.rt = {
      python = {
        whisper = import ./parts/python/whisper.nix { inherit pkgs; };
        tts = import ./parts/python/tts.nix { inherit pkgs; };
      };

      runtime = {
        dir = "%t/rt";
        env = [ "RT_RUNTIME_DIR=%t/rt" ];
      };

      sockets = import ./parts/runtime/sockets.nix;
    };
  };
  ```
  ---

  ## 10. Підсумок

  Ця структура дає:

  * **чіткі межі**
  * відсутність `PYTHONPATH`
  * заміну `/tmp` → `%t/rt`
  * готовність до Colmena
  * мінімальні diff при розширенні

  Це  **production-grade flake-parts layout** , а не «гарна структура».

  Якщо потрібно — наступним кроком можу:

  * повністю зібрати **один сервіс end-to-end**
  * або адаптувати це під **NixOS system services** без дублювання
