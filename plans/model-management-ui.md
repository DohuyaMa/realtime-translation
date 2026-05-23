# План: Model Management UI

## Проблема

Моделі качаються "магічно" — при першому запуску сервісу, без будь-якого UI контролю.
Користувач не бачить:
- які моделі є / відсутні
- скільки місця займають
- чи можна перемкнутися на іншу модель (whisper small→medium, Wyoming→local)
- чи йде скачування
- помилки при завантаженні

## 1. Розширення ConfigManager

Додати в `src/core/config.py` нову секцію `models`:

```yaml
models:
  cache_dir: "~/.cache/real-time-translator/models"
  whisper:
    backend: "local"         # local, wyoming
    model: "small"           # tiny, base, small, medium, large (для local)
  translate:
    model: "Helsinki-NLP/opus-mt-uk-en"
  tts:
    engine: "kokoro"
    model: "hexgrad/Kokoro-82M"
    voice: "en_US"
```

Методи:
- `get_model_config()` / `set_model_config()`
- `set_whisper_backend(backend, model_name)`

## 2. ModelManager — `src/core/models.py`

```python
@dataclass
class ModelInfo:
    id: str                    # "whisper", "translate", "tts"
    display_name: str          # "Whisper (ASR)"
    model_name: str            # "small" або "Helsinki-NLP/opus-mt-uk-en"
    status: ModelStatus        # cached, missing, downloading, error
    size_bytes: Optional[int]
    cache_path: str
    backend_options: List[str] # для whisper: "local", "wyoming"

class ModelManager:
    """Tracking + downloading + validation models."""

    MODEL_REGISTRY = {
        "translate": ModelSpec(
            model_id="Helsinki-NLP/opus-mt-uk-en",
            type="huggingface",
            cache_var="HF_HUB_CACHE",
            default_path="~/real-time-translator-cache/huggingface/hub/"
        ),
        "whisper": ModelSpec(
            model_id="small",
            type="faster-whisper",
            cache_var="WHISPER_CACHE",
            default_path="~/.cache/whisper/"
        ),
        "tts": ModelSpec(
            model_id="hexgrad/Kokoro-82M",
            type="huggingface",
            cache_var=None,  # default HF_HOME
            default_path="~/.cache/huggingface/hub/"
        ),
        "en_core_web_sm": ModelSpec(
            model_id="en_core_web_sm",
            type="spacy-nix",  # Керується Nix — не треба качати
            nix_managed=True,
        ),
    }

    def get_status(self, model_id: str) -> ModelStatus:
        """Check if model exists on disk."""

    def download(self, model_id: str, callback: Callable[[int], None]):
        """Download model (subprocess з прогресом)."""

    def get_cache_size(self) -> int:
        """Total size of all cached models."""

    def clear_cache(self, model_id: str):
        """Delete cached model files."""
```

### Як визначити чи модель закешована?

| Модель | Перевірка |
|---|---|
| Helsinki-NLP/opus-mt-uk-en | `os.path.isdir("~/real-time-translator-cache/huggingface/hub/models--Helsinki-NLP--opus-mt-uk-en/")` |
| hexgrad/Kokoro-82M | `os.path.isdir("~/.cache/huggingface/hub/models--hexgrad--Kokoro-82M/")` |
| faster-whisper small | `os.path.isdir("~/.cache/whisper/small/")` |
| en_core_web_sm | `import spacy; spacy.load("en_core_web_sm")` — завжди є в Nix |

### Прогрес скачування

QThread з сигналом прогресу:
- Для transformers моделей — `huggingface_hub.snapshot_download()` з callback
- Або spawn субпроцес `python -c "..."` і парсити stdout (tqdm)

## 3. Схема UI

Додати **вкладку "Models"** в існуючий `SettingsDialog` через `QTabWidget`:

```
┌────────────────────────────────────────────┐
│  Settings                              X   │
│  ┌──────────┬──────────────────────────┐   │
│  │ General  │[Models]  │ Audio │ ...  │   │
│  ├──────────┴──────────────────────────┤   │
│  │  ┌─ Model Status ────────────────┐  │   │
│  │  │  📦 Translation    ✅ cached  │  │   │
│  │  │     Helsinki-NLP/opus-mt-uk-en│  │   │
│  │  │     Size: 312MB               │  │   │
│  │  ├───────────────────────────────┤  │   │
│  │  │  🎤 Whisper (ASR)   ❌ missing│  │   │
│  │  │     ┌──────────────────┐      │  │   │
│  │  │     │ [small] ▼ │ ⬇️ DL│     │  │   │
│  │  │     └──────────────────┘      │  │   │
│  │  │     Progress: ████░░░ 40%    │  │   │
│  │  ├───────────────────────────────┤  │   │
│  │  │  🔊 TTS Kokoro      ✅ cached│  │   │
│  │  │     hexgrad/Kokoro-82M        │  │   │
│  │  │     Voice: [en_US ▼]         │  │   │
│  │  └───────────────────────────────┘  │   │
│  │                                     │   │
│  │  ┌─ Backend Toggle ──────────────┐  │   │
│  │  │  Whisper: [Local] [Wyoming]   │  │   │
│  │  │  Wyoming: host:port           │  │   │
│  │  └───────────────────────────────┘  │   │
│  │                                     │   │
│  │  [OK]                    [Cancel]   │   │
│  └─────────────────────────────────────┘   │
└────────────────────────────────────────────┘
```

## 4. Компоненти UI

### ModelCard (QFrame)
Блок для однієї моделі:
- Назва + статус (✅ cached / ⬇️ downloading / ❌ missing / ⏸ pending / 🔵 nix-managed)
- Розмір на диску
- Випадаючий список варіантів (small/medium/large для whisper)
- Кнопка Download / Re-download
- ProgressBar під час скачування

### ModelTab (QWidget)
- `QVBoxLayout` з ModelCard для кожної моделі
- `QGroupBox` "Whisper Backend" — перемикач Local / Wyoming з полями host:port

## 5. Зміна SettingsDialog

Поточна структура — одна плоска форма. Треба:
1. Перетворити `SettingsDialog` на `QTabWidget`:
   - **Tab "General"** — існуючі Wyoming + Audio + Translation групи
   - **Tab "Models"** — новий ModelTab
2. Зберегти зворотну сумісність — `settings_changed` сигнал має працювати

```python
class SettingsDialog(QDialog):
    settings_changed = Signal(dict)

    def __init__(self, current_settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(GeneralTab(current_settings), "General")
        tabs.addTab(ModelTab(current_settings), "Models")
        layout.addWidget(tabs)
        layout.addWidget(button_box)
```

## 6. Можливі проблеми

1. **Розмір моделі** — HuggingFace snapshot = директорія з 100+ файлами. `du -sb` через subprocess.
2. **Шлях до кешу** — DevShell використовує `~/real-time-translator-cache/huggingface/`, але Kokoro падає в `~/.cache/huggingface/`. ModelManager має знати обидва шляхи.
3. **Kokoro не snapshot** — не `snapshot_download`, а прямий download всередині бібліотеки. Статус перевіряти через `os.path.isdir` на `~/.cache/huggingface/hub/models--hexgrad--Kokoro-82M/`.
4. **Wyoming backend** — якщо `use_wyoming=true`, whisper модель не треба. UI каже "Not required (Wyoming)" замість Download.

## 7. Що НЕ робити

- ❌ Окреме вікно — таб в існуючому SettingsDialog
- ❌ Авто-скачування при старті — тільки по кнопці
- ❌ QML — весь проект на PySide6 Widgets
- ❌ Окремий сервіс для моделей — ModelManager це library-код
- ❌ Зайві абстракції — три ModelCard в QVBoxLayout це ~150 рядків коду
