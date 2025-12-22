Коротко:  **файл у правильному напрямку** , але зараз він **перевантажений відповідальністю** і потребує кількох  **чітких архітектурних правок** , щоб:

* не зламатися при переході на IPC
* коректно інтегруватися з Kirigami / system UI
* нормально жити у flake / HM / Colmena

Нижче —  **структурований code review + конкретні правки** .

---

## 1. Загальна оцінка

### ✅ Що зроблено правильно

* Є **єдина точка входу** (`main`)
* Controller + Adapter уже використовується (це критично важливо)
* Немає прямої логіки pipeline в UI
* Конфіг, логування, аргументи — розділені
* PipeWire check винесений окремо

Це  **добрий entry-point для desktop app** .

---

## 2. Головна проблема: `main.py` знає ЗАБАГАТО

Зараз файл виконує  **5 ролей одночасно** :

| Роль                  | Має бути |
| ------------------------- | --------------- |
| CLI                       | ок            |
| bootstrap                 | ок            |
| env setup                 | ❌              |
| runtime checks (PipeWire) | ❌              |
| adapter selection         | ❌              |

👉 Це  **блокує IPC / systemd / headless режим** .

---

## 3. Критичні зауваження та як виправити

### ❌ 3.1 `ensure_pipewire_nodes()` у GUI entrypoint

**Проблема:**

* UI не повинен знати, *як* реалізований звук
* У IPC-режимі UI може бути на іншій машині
* У systemd-моді це буде дублюватися

**Правильно:**

* це responsibility **adapter’а**
* або окремого `preflight`

#### Як переробити

```text
core/
 └── preflight/
     └── pipewire.py
```

```python
class PipeWirePreflight:
    def check()
```

І в `DirectAdapter.__init__()`:

```python
PipeWirePreflight().check()
```

UI → controller → adapter → preflight
**НЕ навпаки**

---

### ❌ 3.2 Жорстке використання `DirectAdapter`

```python
adapter = DirectAdapter()
```

Це  **тимчасово допустимо** , але треба одразу закласти перемикання.

#### Мінімальне виправлення (без IPC ще):

```python
def create_adapter(mode: str):
    if mode == "direct":
        return DirectAdapter()
    raise ValueError(mode)
```

CLI:

```bash
--mode direct
--mode ipc
```

---

### ❌ 3.3 UI створює QApplication всередині

```python
window = MainWindow(controller=controller)
app = window.app_instance
```

❗ Це  **антипатерн для Kirigami / Qt** .

**Правильно:**

* `main.py` створює `QApplication`
* `MainWindow` — ТІЛЬКИ UI

#### Правильно:

```python
app = QApplication(sys.argv)
window = MainWindow(controller)
window.show()
sys.exit(app.exec())
```

Kirigami це  **обовʼязково вимагатиме** .

---

### ⚠️ 3.4 ENV змінні HF_* у runtime

```python
os.environ.setdefault("HF_HOME", ...)
```

Для Nix це  **погано** .

**Правильно:**

* devShell / flake → env
* systemd → Environment=

Залиш  **тільки fallback** , але винеси в окремий util:

```python
from core.env import setup_ml_env
setup_ml_env()
```

---

## 4. Що тут добре для Kirigami

### ✅ Controller інʼєктується — це ідеально

Kirigami QML → Python controller через:

* `QQmlContext.setContextProperty`
* або `QObject` wrapper

Тобто:

```text
QML → Controller → Adapter → IPC / Direct
```

Файл цьому  **не заважає** , але:

➡️ **НЕ ПРИВʼЯЗУЙ UI ДО argparse**
➡️ **НЕ ПРИВʼЯЗУЙ UI ДО PipeWire**

---

## 5. Як має виглядати ПІСЛЯ рефакторингу

### Фінальна роль цього файлу

**ТІЛЬКИ bootstrap desktop app**

```text
ui/app.py
```

```python
def main():
    args = parse_args()
    setup_logging()
    controller = create_controller(args.mode)
    run_ui(controller)
```

---

## 6. Підсумкова оцінка

| Критерій                        | Оцінка                       |
| --------------------------------------- | ---------------------------------- |
| Архітектурний напрям | ✅ правильний            |
| Готовність до IPC           | ⚠️ частково              |
| Готовність до Kirigami      | ⚠️ потребує правок |
| Nix-friendly                            | ⚠️ runtime env                   |

❗ **Файл не “поганий” — він просто ще entrypoint-моноліт**

---

## Що пропоную далі (обери)

1. 🔧 Я **перепишу цей файл** у canonical `ui/app.py` (мінімум змін)
2. 🎨 Покажу **Kirigami QML + Python bridge** під твій Controller
3. 🔌 Дам **IPCAdapter + protocol** (готовий до systemd)

Напиши номер.
