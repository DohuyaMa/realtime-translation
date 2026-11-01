# Бенчмарк моделей для live-перекладача uk→en (cyborg, 2026-11-01)

Хост: cyborg, RTX 4060 Laptop 8 GB. Живі сервіси під час тесту займали 4977 MiB VRAM
(LLM-воркер Qwen3.5-4B 3754 MiB, rt-asr turbo 1170 MiB); вільно ≈3.2 GB. Усі прогони йшли
**поруч** із живими сервісами (власник ними користувався) — це шум для латентності.
Нічого в системі не змінювалося; жодного сервера на порту не запускалося (див. «Відхилення»).

## Дані
- FLEURS `uk_ua` test: 40 речень (кожне 8-ме унікальне `id`, перший запис), 421.7 с аудіо;
  англійські референси — FLEURS `en_us` test за тим самим `id` (FLORES).
- Переклад, 100 входів: `fleurs_gold` (40, еталонний uk-текст), `fleurs_asr` (40, вихід
  whisper-turbo з цих кліпів), `live_noisy` (10 реальних фраз з живого тесту, без референсу),
  `it` (10 IT-речень, мої референси — `data/tests.json`).
- Шумний ASR-сет: ті самі кліпи, RMS → −41 dBFS + білий/рожевий шум, SNR 15 dB (seed 0).
  Без нормалізації рівня (у rt-app вона є) — тобто гірший випадок.

## 1. ASR: whisper-large-v3 vs large-v3-turbo (поточний)

Налаштування = `whisper_engine.stream_decode` у продакшні: uk, beam 1, temperature 0,
`condition_on_previous_text=False`, `word_timestamps=True`, без VAD, cuda `int8_float16`
(продакшн-дефолт). Моделі — копії кешу rt-asr (`Systran/faster-whisper-large-v3`,
`dropbox-dash/faster-whisper-large-v3-turbo`). Нормалізація для WER/CER: lower, без пунктуації, апострофи уніфіковано (jiwer).

| Метрика | turbo (поточна) | large-v3 |
|---|---|---|
| WER чисто, % | 8.1 | **6.7** |
| CER чисто, % | 1.5 | **1.3** |
| WER −41 dBFS + білий шум 15 dB, % | 14.6 | **11.7** |
| WER −41 dBFS + рожевий шум 15 dB, % | 10.7 | **8.1** |
| CER білий / рожевий, % | 3.4 / 2.4 | 3.3 / 2.0 |
| Порожні гіпотези | 0 | 0 |
| Декод цілого кліпу (сер. 10.5 с), с | **0.36** | 0.91 |
| RTF | **0.034** | 0.086 |
| Декод вікна 5 с (тік стрімінгу), сер. / p90, с | **0.33 / 0.33** | 0.64 / 0.74 |
| VRAM процесу після завантаження / пік, MiB | **1086 / 1170** | 1918 / 2034 |
| Завантаження, с | 2.3 | 3.4 |

Висновок: large-v3 дає −1.4 п.п. WER чисто і −2.6…−2.9 п.п. на тихому шумному аудіо, але
декод 5-секундного вікна 0.64 с (p90 0.74 с) **довший за тік 500 мс** — стрімінг LocalAgreement-2
не встигатиме (черга тіків росте), а VRAM +850 MiB. Різниця WER на 40 кліпах ≈ 5–8 помилкових слів
з ~600 — помітна, але невелика.

## 2. LLM-перекладач: Index-Translate-2B vs Qwen3.5-4B (поточний)

- Qwen3.5-4B Q4_K_M — живий воркер `http://127.0.0.1:8080` (production-шлях, Token-auth),
  промпт `prod` = `DEFAULT_SYSTEM_PROMPT` + `DEFAULT_TERMS` + `TEXT: …` з `src/core/prompt.py`,
  без контексту попередніх фраз, без NEXT.
- Index-Translate-2B (GGUF Q8_0 і Q4_K_M) — in-process через той самий llama-cpp-python 0.3.36
  (CUDA) з env воркера, повний offload, thinking вимкнено (`<think>\n\n</think>` як у chat template моделі).
  Промпти: `prod` (наш), `card` (training-aligned з картки: `请将以下乌克兰语文本翻译为英语，直接输出翻译结果，不要进行任何解释。\n\n{text}`),
  `inst` (instTrans з картки: IT-глосарій як 【硬性要求】 + «текст з ASR, фрагменти перекладати як є» як 【注意】).
- Скрізь temperature 0 (продакшн 0.1 — взято 0 заради відтворюваності), `max_tokens = min(512, 32+3·слів)` як у продакшні.

| Модель / промпт | chrF gold | chrF asr | chrF IT | BLEU gold | BLEU asr | BLEU IT | off-target | VRAM MiB | TTFT сер. с | total сер. / p90 с |
|---|---|---|---|---|---|---|---|---|---|---|
| **Qwen3.5-4B / prod (live)** | 61.7 | 59.5 | **79.4** | 35.9 | 32.5 | 59.7 | 0 | 3754 | =total¹ | 0.755 / 1.078 |
| Index-2B Q8 / prod | 60.4 | 58.3 | 78.2 | 33.8 | 31.4 | 59.9 | 0 | 2586 | 0.121 | 0.512 / 0.896 |
| Index-2B Q8 / card | 61.7 | **59.8** | 75.6 | 35.7 | **34.5** | 56.4 | 0 | 2586 | 0.033 | 0.369 / 0.612 |
| Index-2B Q8 / inst | 62.0 | 59.2 | 78.3 | 36.5 | 33.1 | 59.2 | 0 | 2586 | 0.059 | 0.373 / 0.540 |
| Index-2B Q4_K_M / prod | 60.5 | 58.6 | 77.5 | 34.8 | 32.7 | 56.7 | 0 | 1884 | 0.113 | 0.359 / 0.500 |
| Index-2B Q4_K_M / card | 61.8 | 59.1 | 73.6 | 36.1 | 33.9 | 51.2 | 0 | 1884 | 0.033 | 0.325 / 0.651 |
| Index-2B Q4_K_M / inst | **62.3** | **59.8** | 75.6 | **36.7** | 33.9 | 54.2 | 0 | 1884 | 0.056 | 0.293 / 0.420 |

¹ Живий воркер **не стрімить**: на `stream: true` віддає один JSON (rt-app падає у non-stream
гілку `_complete_stream`), тож перший токен клієнт отримує разом з усією відповіддю.

Off-target = порожньо / кирилиця / мета-відповідь («Translation:», «Here is…», «Please provide…») /
>3× довжини входу; у всіх варіантах 0 (на 10 live-фразах теж 0).

Paired bootstrap (1000×, 90 речень з референсом), Δ chrF відносно Qwen3.5-4B:
Q8 prod −1.2 [−2.7; +0.2], Q8 card −0.1 [−1.4; +1.2], Q8 inst −0.0 [−1.4; +1.5],
Q4 prod −1.1 [−2.6; +0.4], Q4 card −0.5 [−1.8; +0.9], Q4 inst +0.2 [−1.4; +1.9]
→ **статистично нічия за якістю**; жоден варіант Index не кращий за Qwen надійно.

Швидкість: ms на вихідний токен (total/токени) — Qwen live 33.1, Index Q8 15–22, Q4 12–15.
Латентність **не apples-to-apples**: Qwen іде через HTTP-воркер (+ навантаження живого
використання), Index — in-process. Завантажити Qwen3.5-4B in-process поруч із живими
сервісами не вдалося (`Failed to create llama_context`, нестача VRAM), тож чесного
порівняння на одному рушії немає. Порядок величин (2B vs 4B) — Index ≈1.5–2× швидший за токен.

### Якісні спостереження (ручний розбір)
- Index з нашим `prod` промптом робить **термінові помилки**: «пул реквест» → «request pool» /
  «pool of requests» (Q8 prod, Q8 card, Q4 prod); «сі-ай» → «CI/CD». Qwen обидва — правильно.
  `inst` з глосарієм виправляє «pull request», але **галюцинує терміни з глосарію в шумі**:
  «можешь плащить» → «you can just deploy it», «муствування хрома» → «deploying the chroma»,
  «велику модель не завантажимо» → «won't be deploying a large model».
- Index «домальовує» незавершені фрагменти («Ви можете ставити запитання про те,» →
  «…about that.», «відео або аудио» → «video or audio files»); Qwen лишає фрагмент обірваним —
  для стрімінгу правильніше (наш промпт це вимагає).
- Qwen має змістову помилку на FLEURS: «в 3 столітті до нашої ери» → «in 300 BC»; Index — правильно.

### 8 прикладів пліч-о-пліч

| # | Вхід (uk) | Референс | Qwen3.5-4B prod | Index Q8 prod | Index Q8 card |
|---|---|---|---|---|---|
| 1 | Велика піраміда, побудована египтянами в 3 столітті до нашої ери… (ASR) | Built by the Egyptians in the third century BCE, the Great Pyramid… | …built by the Egyptians **in 300 BC**… | …in the 3rd century BCE… | …in the 3rd century BCE… |
| 2 | Під час роботи у шпиталі Лігінс почав у вільний час вивчати передчасні пологи. (ASR) | While he was working at the hospital Liggins began to investigate premature labor… | During his work at **Lings Hospital**, he began studying premature births… | While working at the **Ligins hospital**, he started studying preterm labor… | While working at the hospital, Ligins began studying premature labor… |
| 3 | Я створив пул реквест, подивись його, будь ласка. | I created a pull request, please take a look at it. | I created a pull request, please take a look at it. | I created a **request pool**… | I created a **pool of requests**… |
| 4 | Після мерджу в мейн тести на сі-ай впали. | After the merge into main, the tests on CI failed. | After the merge, main tests for CI failed. | After the merge, the CI/CD tests failed. | After the **measure**, the main **SI** tests dropped. |
| 5 | Не проси же, можешь плащить, тот зо нелепи. (live) | — (сміття ASR) | Don't ask, you can pay for it. | Don't ask, you can wear a cloak, it's absurd. | Don't ask, you can wear a cloak, that's absurd. |
| 6 | Провинити на муствування хрома. (live) | — (сміття ASR) | Prove Chrome on a Mac. | To blame the Chrome browser. | To blame on the chromaticism. |
| 7 | Ви можете ставити запитання про те, (live) | You can ask questions about | You can ask questions about | You can ask questions about **that.** | You can ask questions about that, |
| 8 | Що ввемо підтримую функцію. (live) | — (сміття ASR) | What we'll implement as a supported feature. | What we're implementing supports the feature. | What do we support? I support the function. |

Повні виходи всіх 100 входів × варіантів: `out/llm_*.json`; ASR-гіпотези: `out/asr_*.json`; метрики: `out/llm_metrics.json`, `out/asr_metrics.json`.

## 3. LiquidAI/LFM3-Audio-2B — пропущено
GGUF-заголовки (range-запит, без завантаження): `general.architecture = lfm3`, mmproj
`clip.audio.projector_type = lfm3a`. Системний llama.cpp (у llama-cpp-python 0.3.36) знає лише
`lfm2` / `lfm2a` — модель не завантажиться без нового llama.cpp. Окремого `llama-server` /
`llama-mtmd-cli` у системі немає. `general.languages` = en, ar, da, de, el, fi, fr, he, it, ja, ko,
nl, pl, pt, es, sv, tr, zh — **української немає**. Не завантажувалось.

## 4. Hetvi-004/my-llama2-chatbot — пропущено за брифом.

## Рекомендації
- **LLM-перекладач: лишити Qwen3.5-4B.** Якість — нічия (bootstrap CI перетинає 0), а на
  IT-термінах і стрімінгових фрагментах Qwen з нашим промптом поводиться правильніше; Index з
  глосарієм галюцинує терміни. Index виграє лише латентністю (~0.3–0.5 с vs 0.75 с, нечесне
  порівняння) і VRAM (−1.2…−1.9 GB). Що змінило б рішення: якщо латентність стане вузьким місцем —
  Index-2B Q4_K_M + `inst` без жорсткого глосарію (лише 【注意】 про IT/ASR) варто перевірити
  на живому A/B.
- **ASR: лишити large-v3-turbo.** large-v3 точніший (−1.4 п.п. WER, −2.6…−2.9 на тихому шумі),
  але 5-с вікно декодується 0.64 с > тік 500 мс на цій GPU поруч з LLM. Що змінило б рішення:
  тік ≥ 1 с або розвантажена GPU.
- **Окремо (дешевий виграш):** воркер ігнорує `stream: true` — увімкнення стрімінгу у воркері
  дало б TTFT ~0.1 с замість ~0.75 с при тій самій моделі.

## Файли моделей (лишені, рішення за власником)
| Шлях (у `modelbench/models/`) | Розмір |
|---|---|
| `index-translate-2b/Index-Translate-2B.Q8_0.gguf` | 2.08 GB |
| `index-translate-2b/Index-Translate-2B.Q4_K_M.gguf` | 1.31 GB |
| `whisper-large-v3/` (копія кешу rt-asr, не завантаження) | 3.09 GB |
| `whisper-large-v3-turbo/` (копія кешу rt-asr) | 1.62 GB |
| `../data/` (FLEURS uk test tar + 40 wav + tsv) | 433 MB |

Скрипти: `scripts/` (prep, asr_bench, asr_metrics, build_tests, llm_bench, llm_metrics, boot).
