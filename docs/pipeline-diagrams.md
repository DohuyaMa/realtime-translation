# Повний ланцюжок перекладу — діаграми

Стан на 2026-10-31 (rt-edge: стріми rt-asr/rt-vosk, одночасний переклад
одиницями, гейт Vosk на одиницю —
[специфікація](superpowers/specs/2026-10-31-streaming-asr-simultaneous-translation.md),
[`live-test-2026-10-31.md`](../context/live-test-2026-10-31.md) §10, §13).
Детальний опис налаштувань — [`USAGE.md`](USAGE.md).

## 1. Компоненти та зв'язки

```mermaid
flowchart LR
  subgraph PC["cyborg — ПК користувача"]
    MIC["Мікрофон<br/>(parec, вибране джерело)"]
    subgraph APP["rt-app (translator-ui)"]
      PIPE["Конвеєр<br/>стріми ASR/Vosk → одиниці → LLM → TTS → відтворення"]
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

## 2. Шлях однієї одиниці перекладу

```mermaid
sequenceDiagram
  autonumber
  participant M as Мікрофон (parec, 48 кГц)
  participant E as Edge: рівень + 16 кГц
  participant WH as rt-asr WS (whisper, LocalAgreement-2)
  participant VK as rt-vosk WS (Kaldi)
  participant U as Стадія одиниць (SourceLedger)
  participant L as LLM-воркер
  participant G as Перевірка відповіді
  participant T as rt-tts (Kokoro)
  participant P as Відтворення (pacat)

  M->>E: блоки аудіо
  Note over E: нормалізація рівня (до −20 dBFS, ≤ +24 дБ, лімітер);<br/>тихо ≥ 3 с мови → «Мікрофон тихий»
  par ті самі кадри по 100 мс в обидва стріми
    E->>WH: PCM16
    WH-->>U: final (зафіксовані слова з часом, лише дописуються),<br/>partial (незафіксований хвіст), endpoint (тиша ≥ 500 мс)
  and
    E->>VK: PCM16
    VK-->>U: final / partial зі словами (conf, start, end),<br/>idle{at, silent_from} (у Kaldi немає слова і аудіо без голосу — тиша)
  end
  Note over U: хвіст після курсора + partial — сірим в UI, не озвучується
  U->>U: одиниця: кінець речення / endpoint /<br/>≥ 12 слів на межі клаузи (,;:) / 20 слів<br/>(крапка + мала літера без паузи - не кінець; шматок 1 слово < 0.25 с - до сусіда;<br/>речення в кінці зафіксованого чекає ≤ 0.5 с)
  U->>U: чекати ≤ 300 мс, доки Vosk покриє кінець одиниці<br/>(інакше лише whisper — vosk_late)
  alt у тексті немає літер
    U-->>U: пропуск (asr_no_words)
  else Vosk покрив кінець (словами або idle - тиша) і: жодного слова ≥ 2 літер (no_word)<br/>або ≥ 2 слова ≥ 3 літер і жодного збігу (disagree)
    U-->>U: відкинуто до LLM (vosk_gate_dropped), ledger просто йде далі
  else Vosk без слів 2 одиниці поспіль (≥ 3 слів whisper)
    U-->>U: «Vosk не чує — звірка вимкнена до відновлення»,<br/>далі лише whisper, поки не повернуться слова Vosk
  end
  opt «Записувати сесію для аналізу»
    U-->>U: seg-N.wav + рядок phrases.jsonl
  end
  U->>L: system: інструкції; контекст — попередні пари<br/>user: TEXT: одиниця / HINT: слова Vosk її відрізка /<br/>NEXT (do not translate): решта зафіксованого + partial
  L-->>G: переклад (частинами: речення, довге — по клаузах ≥ 4 слів)
  alt ≤ 3 слів оригіналу
    G-->>G: дочекатися всієї відповіді
  end
  Note over G: один INFO-рядок на одиницю: whisper, Vosk (conf, agree),<br/>гейт, режим LLM, NEXT, переклад
  alt задовго, кирилиця або витік NEXT
    G-->>U: відхилено (llm_rejected, подія в журналі),<br/>у контекст не йде
  else норма
    G->>T: частина за частиною
    T->>P: аудіо по порядку (тиша по краях обрізана: 50 / 150 мс)
    P->>P: вибраний вихід (віртуальний мікрофон)<br/>+ навушники, якщо увімкнено
  end
```

Одиниці не перетинаються: що озвучено, те не виправляється і не
повторюється (рішення власника 2/3). Затримки на одиницю — у
`get_stats()["latency"]` (p50/p90: `commit_ms` кінець слова → фіксація,
`llm_first_ms`, `tts_first_ms`, `ready_ms` кінець мови → звук готовий,
`e2e_ms` кінець мови → початок відтворення) і в INFO-рядку
`speech end -> …`; офлайн-стенд — `python -m scripts.pipeline_bench`
(USAGE §8).

TEXT (whisper) перекладається повністю; HINT (Vosk) лише виправляє
слова, які whisper розчув неправильно, а слова HINT поза TEXT не
перекладаються. На старті конвеєра — прогрів усіх сервісів паралельно:
відкриття стрімів `WS /v1/audio/stream` rt-asr і rt-vosk (аудіо тим часом
чекає в кільцевому буфері стріму, ≤ 30 с), LLM і TTS — по одному короткому
запиту. Без Vosk (вимкнено / недоступний — видно в UI) LLM отримує лише
текст whisper зі звичайним промптом, а відсікання за Vosk не діє. rt-vosk тримає
одну модель однієї мови: фраза іншою мовою — недоступний «немає моделі для
«<мова>»» (лише whisper); модель перемикає тільки користувач (вкладка
«Звірка (Vosk)», «Завантажити модель для «<мова>»»).

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
  R3 --> R4["Старт перекладу:<br/>нова сесія + прогрів стрімів, LLM і TTS"]
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
