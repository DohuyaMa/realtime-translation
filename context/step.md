Нижче — **чіткий набір завдань (backlog)** для  **швидкого прототипу** , з пріоритетами та результатом кожного кроку.
Це дозволяє **паралельно** рухатись і **не ламати майбутню інтеграцію** з flake / HM / Colmena.

---

## EPIC 0 — Базова ціль прототипу

**Мета:**
UI (Kirigami) керує та відображає стан realtime-pipeline  **через абстрактний controller** , без прямого доступу до backend-реалізації.

---

# PHASE 1 — Розбиття поточного коду (критично)

### Task 1.1 — Виділити Controller (MVP)

**Завдання:**

* Винести всю логіку керування pipeline з UI
* UI не імпортує `TranslationSystem`

**Результат:**

```text
ui/
 ├── controller.py
 ├── adapters/
 │    └── direct.py   ← тимчасово
 └── main_window.py
```

**Controller API (мінімум):**

```python
class Controller:
    def start()
    def stop()
    def start_service(name)
    def stop_service(name)
    def get_status() -> dict
```

---

### Task 1.2 — DirectAdapter (тимчасовий)

**Завдання:**

* Створити adapter, який **обгортає твій існуючий TranslationSystem**
* Вся логіка залишається як є, але UI її не бачить

**Результат:**

```python
controller = Controller(adapter=DirectAdapter())
```

> Це дозволить **пізніше замінити adapter на IPC без переписування UI**

---

# PHASE 2 — IPC клієнт (ключ до модульності)

### Task 2.1 — Визначити IPC протокол (MVP)

**Формат:** JSON
**Транспорт:** Unix socket
**Стиль:** request/response

```json
{ "cmd": "status" }
{ "cmd": "start_service", "name": "whisper" }
```

**Відповідь:**

```json
{
  "services": {
    "capture": { "state": "running", "latency": 12 },
    "whisper": { "state": "down" }
  }
}
```

---

### Task 2.2 — IPCAdapter (UI-side)

**Завдання:**

* Реалізувати `IPCAdapter`
* Підтримка:
  * connect
  * send(cmd)
  * receive(status)

**Результат:**

```text
ui/adapters/ipc.py
```

UI  **не знає** , чи це direct чи IPC.

---

### Task 2.3 — Перемикач adapter’ів

**Завдання:**

* ENV або аргумент запуску

```bash
rt-ui --mode=direct
rt-ui --mode=ipc
```

---

# PHASE 3 — Kirigami UI (видимий результат)

### Task 3.1 — Мінімальний Kirigami shell

**Завдання:**

* `ApplicationWindow`
* `pageStack`
* одна сторінка `Dashboard`

**Результат:**

* Порожній, але Plasma-native UI
* Wayland-safe

---

### Task 3.2 — Kirigami Service Status Panel (аналог твого)

**Завдання:**

* Список сервісів
* Іконка стану
* Кнопка Start/Stop

**Компоненти Kirigami:**

* `Kirigami.ListItem`
* `Kirigami.Icon`
* `InlineMessage`

**Модель:**

```qml
ListModel {
  ListElement { name: "capture"; state: "running" }
}
```

---

### Task 3.3 — Binding UI → Controller

**Завдання:**

* QML викликає Python через context property
* Python повертає status → QML model

---

# PHASE 4 — UX мінімум (швидкий win)

### Task 4.1 — Event-based update

**Завдання:**

* При дії користувача → одразу оновлення
* Timer тільки fallback (500–1000 ms)

---

### Task 4.2 — Status ≠ Log

**Завдання:**

* Короткий глобальний статус
* Окремий лог/події (scrollable)

---

# PHASE 5 — Nix-ready, але автономно

### Task 5.1 — Окремий flake для UI

**Завдання:**

* `packages.ui`
* без systemd
* без HM

UI запускається так:

```bash
nix run .#ui
```

---

### Task 5.2 — Підготовка до HM / Colmena

**Завдання:**

* `.desktop` файл
* іконка
* без hardcoded paths

---

## Рекомендований порядок (швидко)

1. **Task 1.1 + 1.2** — 1–2 години
2. **Task 2.1 + 2.2** — 2–3 години
3. **Task 3.1 + 3.2** — візуальний результат
4. **Task 5.1** — готовність до інтеграції

---

## Що робити ДАЛІ (обери)

Можу  **наступним кроком** :

1. 🔧 показати **реальний refactor твого коду → controller + adapter**
2. 🎨 намалювати **Kirigami QML для Service Status Panel**
3. 🔌 дати **готовий IPC client (Python) для UI**

Напиши номер (1 / 2 / 3).
