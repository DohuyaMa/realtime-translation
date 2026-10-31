# Повний ланцюжок перекладу — діаграми

Стан на 2026-10-31 (rt-edge `2756390`). Детальний опис налаштувань —
[`USAGE.md`](USAGE.md).

## 1. Компоненти та зв'язки

```mermaid
flowchart LR
  subgraph PC["cyborg — ПК користувача"]
    MIC["Мікрофон<br/>(parec, вибране джерело)"]
    subgraph APP["rt-app (translator-ui)"]
      PIPE["Конвеєр<br/>VAD → ASR → LLM → TTS → відтворення"]
      UI["Вікно + трей<br/>стан моделей, рубильник"]
    end
    subgraph LOCAL["Локальні сервіси, 127.0.0.1"]
      ASR["rt-asr :8091<br/>whisper (GPU)"]
      VOSK["rt-vosk :8092<br/>Vosk (CPU)"]
      TTS["rt-tts :8093<br/>Kokoro (CPU)"]
      W1["LLM-воркер :8080<br/>Qwen3.5-4B"]
    end
    DJ1["Django цього хоста<br/>/api/llm/app-services/"]
    VM["RT Переклад (мікрофон)<br/>віртуальний мікрофон"]
    HP["Навушники<br/>(галочка)"]
  end

  subgraph SK["Інші вузли — через tailnet (cluster allowlist)"]
    ASR2["rt-asr"]
    VOSK2["rt-vosk"]
    TTS2["rt-tts"]
    W2["LLM-воркер :8080"]
  end

  DJ1 -- "список сервісів + токени<br/>(кеш на диску, кожні 5 хв)" --> APP
  MIC --> PIPE
  PIPE <--> ASR & VOSK & TTS
  PIPE <-- "app-session: воркер не бере<br/>завдання Django, поки сесія жива" --> W2
  PIPE -. "резерв, якщо локальний недоступний" .-> ASR2 & VOSK2 & TTS2 & W1
  PIPE --> VM
  PIPE -.-> HP
  VM --> OTHER["Zoom / Teams / браузер<br/>чують переклад"]
```

Вибір вузла для кожного виду сервісу — `services.use.<kind>` (auto /
конкретний вузол; для Vosk ще «вимкнено»). Локальні сервіси — завжди
127.0.0.1; tailnet-адреси — лише для інших вузлів.

## 2. Шлях однієї фрази

```mermaid
sequenceDiagram
  autonumber
  participant M as Мікрофон (parec)
  participant S as VAD-сегментатор
  participant A as ASR-стадія (rt-app)
  participant WH as rt-asr (whisper)
  participant VK as rt-vosk
  participant L as LLM-воркер
  participant G as Перевірка відповіді
  participant T as rt-tts (Kokoro)
  participant P as Відтворення (pacat)

  M->>S: аудіо 16 кГц
  S->>A: сегмент (кінець — пауза ≥ 700 мс,<br/>або розріз 6 с м'який / 12 с жорсткий)
  par паралельно
    A->>WH: аудіо + мова + prompt {{previous_text}}
    WH-->>A: текст (rt-asr уже відкинув тишу<br/>та відомі галюцинації — лічильник)
  and
    A->>VK: те саме аудіо + мова
    VK-->>A: слова + впевненість
  end
  alt у тексті немає літер
    A-->>A: пропуск (asr_no_words)
  else у Vosk немає жодного слова ≥ 2 літер
    A-->>A: відкинуто до LLM (vosk_gate_dropped)
  else речення не закінчене (немає . ! ?, або «…»)
    A-->>A: притримати хвіст і склеїти з наступним сегментом<br/>(тиша після нього → відправити сам)
  end
  A->>L: system: інструкції (звірка двох текстів)<br/>user: EXACT: {{text_check}} / FLUENT: {{text}}
  L-->>G: переклад потоком, по реченнях
  alt ≤ 3 слів оригіналу
    G-->>G: дочекатися всієї відповіді
  end
  alt задовго (> max(8, 3 × слів)) або кирилиця
    G-->>A: відхилено (llm_rejected, подія в журналі),<br/>у контекст не йде
  else норма
    G->>T: речення за реченням
    T->>P: аудіо по порядку
    P->>P: вибраний вихід (віртуальний мікрофон)<br/>+ навушники, якщо увімкнено
  end
```

Без Vosk (вимкнено / недоступний — видно в UI) LLM отримує лише текст
whisper зі звичайним промптом, а відсікання за Vosk не діє.

## 3. Рубильник «Зупинити все й звільнити пам'ять»

```mermaid
flowchart TB
  STOP(["Зупинити все"]) --> S1["Зупинити запис і конвеєр"]
  S1 --> S2["Закрити app-session<br/>→ воркер повертається до Django"]
  S2 --> S3["Призупинити оновлення списку сервісів<br/>і перевірки стану"]
  S3 --> S4["POST /model/unload<br/>лише локальні rt-asr, rt-vosk, rt-tts"]
  S4 --> IDLE(["Трей сірий — система вільна"])

  START(["Запустити"]) --> R1["POST /model/load локально"]
  R1 --> R2["Дочекатися ready"]
  R2 --> R3["Відновити оновлення списку сервісів"]
  R3 --> R4["Старт перекладу:<br/>нова сесія + прогрів LLM"]
  R4 --> RUN(["Трей кольоровий"])

  NOTE["LLM на воркері не вивантажується:<br/>він обслуговує чергу Django"]:::n
  classDef n fill:#fff3cd,stroke:#d4a017,color:#000
```

## 4. Стани моделі в rt-asr / rt-vosk / rt-tts

```mermaid
stateDiagram-v2
  [*] --> unloaded
  unloaded --> downloading: load / зміна моделі<br/>(файлів немає на вузлі)
  unloaded --> loading: load (файли вже є)
  downloading --> loading: завантажено (без ліміту часу, з прогресом)
  loading --> ready: ≤ 300 с
  loading --> error: OOM / тайм-аут
  ready --> unloaded: unload / рубильник / простій 15 хв
  ready --> unloaded: POST /config нова модель<br/>(спершу вивантажити стару)
  error --> loading: load
  note right of ready
    /v1/audio/* поки не ready → одразу 503
    model_unloaded | downloading | loading | error
  end note
```
