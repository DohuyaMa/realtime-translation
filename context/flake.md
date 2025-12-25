Нижче —  **чіткий перелік помилок і некоректних рішень у поточній версії** , без повторення попереднього аналізу.

---

## 1. ❌ `homeManagerConfiguration` у `outputs` — **некоректно**

```nix
homeManagerConfiguration = {
  imports = [ home-manager.homeManagerModules.home-manager ];
  ...
};
```

### Проблема

* У flake `outputs` **немає такого стандартного ключа**
* `home-manager` очікує:
  * `homeManagerModules.<name>` **або**
  * `homeConfigurations.<user>@<host>`

### Як правильно

Один із варіантів:

```nix
homeManagerModules.rt-translator = { config, pkgs, ... }: {
  systemd.user.services = { ... };
};
```

Або:

```nix
homeConfigurations."user@host" = home-manager.lib.homeManagerConfiguration { ... };
```

Зараз цей блок  **ніколи не буде застосований** .

---

## 2. ❌ `home-manager` input **взагалі не оголошений**

```nix
imports = [ home-manager.homeManagerModules.home-manager ];
```

Але в `inputs`:

```nix
inputs = {
  nixpkgs = ...
  flake-utils = ...
};
```

### Наслідок

* evaluation error
* `home-manager` не знайдено

### Мінімальне виправлення

```nix
home-manager = {
  url = "github:nix-community/home-manager";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

---

## 3. ❌ `ExecStart = python -m src.*` **без PYTHONPATH**

```nix
ExecStart = "${pkgs.python313}/bin/python -m src.capture.capture_service ..."
```

### Проблема

* systemd  **не знає** , де `src`
* `pythonEnv` не використовується
* `PYTHONPATH` не задано

### Результат

`ModuleNotFoundError: No module named 'src'`

### Правильно

Або:

* wrapper з правильним `PYTHONPATH`
* або `Environment=PYTHONPATH=...`
* або `WorkingDirectory=...`

---

## 4. ❌ `pythonEnv` **не використовується сервісами**

```nix
home.packages = [ pythonEnv ];
```

але:

```nix
ExecStart = "${pkgs.python313}/bin/python ..."
```

### Наслідок

* залежності з `pythonEnv` **ігноруються**
* сервіси стартують у «голому» python

Це  **гарантований runtime-fail** .

---

## 5. ❌ Конфлікт `pulseaudio` / `pipewire` / `pactl`

```nix
pulseaudio
pipewire
...
${pkgs.pulseaudio}/bin/pactl load-module module-null-sink
```

### Проблема

* У PipeWire системі:
  * `pulseaudio` daemon **не використовується**
  * `module-null-sink` — PulseAudio-модуль
* після рестарту PipeWire → sinks зникають

### Правильно

* `pipewire-pulse` + `pactl` з нього
* або `pw-cli` / WirePlumber rules

---

## 6. ❌ `rt-playback` має неправильний module path

```nix
-m src.playback_service
```

У всіх інших:

```nix
-m src.playback.playback_service
```

### Це  **реальна помилка** , не стиль.

---

## 7. ❌ GUI-залежності в systemd user services

```nix
pyqt6
qt6.qtbase
qt6.qtwayland
```

Але:

* user service
* без `DISPLAY` / `WAYLAND_DISPLAY`
* без `graphical-session.target`

### Наслідок

* сервіс не стартує
* або зависає

GUI ≠ daemon.

---

## 8. ❌ `buildPythonPackage` усе ще використано неправильно

Ти:

* вимикаєш всі фази
* не ставиш package
* копіюєш repo

Це  **не Python package** , а runtime bundle.

Правильний рівень:

* `mkDerivation` **або**
* `python.withPackages` **або**
* `writeShellApplication`

---

## 9. ❌ `alsaUtils` оголошений, але не використовується

```nix
alsaUtils = pkgs.alsa-utils;
```

Мертва змінна.

---

## 10. ❌ `flake-utils.eachSystem` + HM конфіг

Home Manager конфіг:

* **не system-dependent**
* **не має бути всередині `eachSystem`**

Це ще одна архітектурна помилка.

---

## Короткий підсумок

Ця версія **все ще невалідна як flake** через:

* ❌ неправильний HM output
* ❌ відсутній `home-manager` input
* ❌ systemd сервіси без runtime environment
* ❌ pythonEnv не використовується
* ❌ audio stack конфліктний

---

## Рекомендований наступний крок

1️⃣ Винести  **тільки** :

```nix
homeManagerModules.rt-translator
```

2️⃣ Зробити **один правильний runtime wrapper**

3️⃣ Розділити:

* devShell
* runtime
* systemd
* audio infra

Якщо хочеш — можу дати  **мінімальний робочий skeleton flake** , без зайвого.
