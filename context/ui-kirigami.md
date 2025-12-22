Нижче —  **практичні рекомендації** , як інтегрувати **Kirigami UI** у твою архітектуру так, щоб:

* ✔ працювало **зараз автономно**
* ✔ було **нативно для KDE Plasma 6 / Wayland**
* ✔ **безболісно інтегрувалося пізніше** в Home Manager / Colmena
* ✔ UI **не ламало daemon / systemd частину**

---

## 1. Ключове рішення (архітектурне)

**UI ≠ backend ≠ audio pipeline**

Kirigami UI має бути:

* ❌ не systemd-сервісом
* ❌ не частиною realtime-пайплайну
* ✅ **окремим desktop application**
* ✅ клієнтом до твоїх сокетів / API

Тобто:

```
[KDE Kirigami UI]
        │
        ▼
[ UNIX sockets / IPC / gRPC / HTTP ]
        │
        ▼
[ capture | whisper | translate | tts ]
```

Це  **єдиний правильний шлях** .

---

## 2. Технологічний вибір для Kirigami

### Рекомендовано (для твого кейсу)

**QML + Qt 6 + PySide6**

Причини:

* Kirigami = QML-first
* Plasma 6 = Qt 6
* PyQt6 ❌ (гірша інтеграція з Kirigami)
* PySide6 ✅ офіційний binding

### Мінімальний стек

```text
Qt 6
Kirigami 2
QML
PySide6
Wayland
```

---

## 3. Структура UI частини (окремий модуль)

```
ui/
├── main.py
├── qml/
│   ├── Main.qml
│   ├── pages/
│   │   ├── Dashboard.qml
│   │   ├── Audio.qml
│   │   ├── Whisper.qml
│   │   └── Settings.qml
│   └── components/
│       ├── StatusCard.qml
│       └── SocketIndicator.qml
└── icons/
```

UI  **не імпортує нічого з `src/`** , тільки IPC.

---

## 4. Мінімальний старт Kirigami (Python)

### `ui/main.py`

```python
import sys
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl

app = QGuiApplication(sys.argv)

engine = QQmlApplicationEngine()
engine.load(QUrl("qml/Main.qml"))

if not engine.rootObjects():
    sys.exit(-1)

sys.exit(app.exec())
```

---

## 5. Мінімальний Kirigami shell

### `ui/qml/Main.qml`

```qml
import QtQuick
import QtQuick.Controls
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root
    width: 900
    height: 600
    visible: true
    title: "RT Translator"

    pageStack.initialPage: Dashboard {}
}
```

---

## 6. IPC між UI та backend (ВАЖЛИВО)

### Рекомендований варіант

✔ **Unix sockets (stream / datagram)**
✔ JSON messages
✔ non-blocking

Причини:

* у тебе вже socket-архітектура
* systemd socket activation
* мінімальний latency
* без web stack

### Приклад

UI:

```
~/.run/rt/whisper.sock
```

UI читає:

* статус
* latency
* error states

UI пише:

* start / stop
* mode
* language
* device selection

---

## 7. Nix: як додати UI **без ламання runtime**

### `nix/ui.nix`

```nix
{ pkgs, rt-runtime }:

let
  py = pkgs.python312.withPackages (ps: with ps; [
    pyside6
  ]);
in

pkgs.stdenv.mkDerivation {
  pname = "rt-translator-ui";
  version = "0.1.0";

  src = ../ui;

  nativeBuildInputs = [ pkgs.makeWrapper ];

  installPhase = ''
    mkdir -p $out/share/rt-ui
    cp -r . $out/share/rt-ui

    makeWrapper ${py}/bin/python $out/bin/rt-translator-ui \
      --set PYTHONPATH $out/share/rt-ui \
      --add-flags "$out/share/rt-ui/main.py"
  '';
}
```

---

## 8. Desktop integration (Plasma-native)

### `.desktop` файл (пізніше HM)

```ini
[Desktop Entry]
Name=RT Translator
Exec=rt-translator-ui
Type=Application
Categories=Utility;Audio;
Icon=audio-input-microphone
StartupWMClass=rt-translator
```

Переваги:

* з’являється в меню KDE
* Wayland-safe
* Plasma розуміє app lifecycle

---

## 9. Що **НЕ** робити з Kirigami

❌ запускати UI з systemd
❌ UI як частину pipeline
❌ shared state через файли
❌ Qt widgets (QWidget)
❌ PyQt6 для Kirigami

---

## 10. Готовність до Home Manager / Colmena

Пізніше просто:

```nix
home.packages = [
  inputs.rt-translator.packages.${pkgs.system}.runtime
  inputs.rt-translator.packages.${pkgs.system}.ui
];
```

Systemd:

* тільки backend
* UI запускається користувачем

---

## Підсумок (коротко)

* Kirigami = **окремий UI-клієнт**
* PySide6 + QML
* IPC через sockets
* Wayland-native
* Nix: UI = окремий derivation
* Plasma 6 повністю підтримується

---

Якщо хочеш далі:

* показати **Kirigami page layout під твій pipeline**
* приклад **socket-status indicator в QML**
* або **WirePlumber control page**

Скажи, що саме.
