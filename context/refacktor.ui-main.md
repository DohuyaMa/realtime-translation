Нижче — **чіткі, технічні рекомендації** по твоєму коду з урахуванням:

* поточної реалізації на **PyQt6**
* майбутнього переходу на **Kirigami / Qt6 / Wayland**
* модульної архітектури
* інтеграції з systemd/socket-backend

Без «переписуй усе» — тільки те, що реально потрібно змінити.

---

## 1. Головна архітектурна проблема (критична)

### ❌ UI напряму керує `TranslationSystem`

```python
self.translation_system = TranslationSystem(...)
self.translation_system.start()
self.translation_system.stop_service(...)
```

Це  **ламає модульність** .

### ✔ Правильно

UI  **НЕ має знати** , як:

* стартує сервіс
* що таке capture / whisper / tts
* які процеси чи systemd-юніти

UI має говорити  **одному абстрактному API** :

```text
UI → Controller / IPC client → backend
```

### Мінімальний рефакторинг

Винеси  **інтерфейс управління** :

```python
class TranslatorController(Protocol):
    def start_pipeline(self): ...
    def stop_pipeline(self): ...
    def start_service(self, name: str): ...
    def stop_service(self, name: str): ...
    def get_status(self) -> dict: ...
```

UI залежить **тільки** від цього.

---

## 2. Таймер 100 ms — погано (Wayland + Qt)

```python
self.update_timer.start(100)
```

❌ Постійний polling
❌ Погано для батареї
❌ Погано для Wayland compositor

### ✔ Рішення

* UI оновлюється **по подіях**
* Timer тільки fallback (500–1000 ms)

```python
self.update_timer.start(500)
```

Ідеально:

* backend → IPC → event → UI

---

## 3. Статус бар як лог — архітектурна помилка

```python
self.statusBar().showMessage('Speech detected')
```

❌ statusBar не лог
❌ повідомлення перетираються
❌ UI виглядає «нервовим»

### ✔ Рішення

* statusBar → **короткий state**
* події → **notification / log view**

Рекомендація:

* `QListView` або Kirigami `InlineMessage`

---

## 4. Service Status Panel — логіка в UI (погано)

```python
service_connected = stats.get(f'{service}_connected', False)
```

❌ UI знає **внутрішню модель backend**
❌ жорстка привʼязка до назв

### ✔ Рішення

Backend має віддавати **уніфікований формат**

```json
{
  "services": {
    "capture": { "state": "running", "latency": 12 },
    "whisper": { "state": "down" }
  }
}
```

UI просто рендерить.

---

## 5. Lambda в циклі — добре, але небезпечно

```python
control_btn.clicked.connect(
    lambda checked, s=service_key: self.toggle_service(s)
)
```

✔ тут ТИ ПРАВИЛЬНО зробив `s=service_key`
❗ але для масштабування — краще явно

```python
def make_handler(name):
    return lambda _: self.toggle_service(name)
```

---

## 6. Audio devices — UI робить важку роботу

```python
devices = self.translation_system.get_audio_devices()
```

❌ UI сам фільтрує
❌ UI знає формат devices

### ✔ Рішення

Controller API:

```python
get_input_devices() -> List[Device]
set_input_device(id)
```

UI  **ніколи не парсить backend-структури** .

---

## 7. `sys.exit(0)` — небезпечно для Qt

```python
sys.exit(0)
```

❌ не дає Qt коректно завершитись
❌ може зламати Wayland cleanup

### ✔ Рішення

```python
QApplication.quit()
```

---

## 8. PyQt6 + Kirigami — НЕ СУМІСНІ

Це ключове.

Твій код:

```python
from PyQt6.QtWidgets import QMainWindow
```

❌ **Kirigami = QML**
❌ QWidget = legacy
❌ PyQt6 гірше підтримує Kirigami

### ✔ Стратегія

| Зараз      | Пізніше            |
| --------------- | ------------------------- |
| PyQt6 Widgets   | PySide6 + QML             |
| `MainWindow`  | `ApplicationWindow.qml` |
| Python UI logic | Python backend adapter    |

---

## 9. Як підготувати код до Kirigami (без переписування)

### Зараз

```python
MainWindow
 ├─ UI widgets
 └─ calls TranslationSystem
```

### Правильно

```text
ui/
 ├─ widgets/ (тимчасово)
 ├─ controller.py   ← ВСЯ логіка
 └─ adapters/
      ├─ ipc.py
      └─ direct.py  ← зараз
```

Пізніше:

* `widgets/` → ❌
* `qml/` → ✅
* `controller.py` → без змін

---

## 10. Що залишити — добре зроблено

✔ Tray icon логіка
✔ Service panel UX
✔ Separation на методи
✔ Стани кнопок
✔ Loguru

Це  **хороший код для прототипу** .

---

## Резюме (коротко)

### Що терміново змінити

* UI ≠ TranslationSystem
* менше polling
* прибрати `sys.exit`
* абстрагувати service/status

### Що планувати

* PySide6 + QML
* Kirigami pages
* IPC-first UI

---

Якщо хочеш:

* покажу **як цей код розбити на controller + UI**
* або **як виглядатиме Kirigami аналог твого Service Status Panel**
* або **як правильно зробити IPC client для UI**

Скажи напрям.
