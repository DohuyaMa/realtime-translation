# STOP — де ми зупинились

Дата: ~23 травня 2026
Останній коміт: `0b91c16 refUCK`

## Що зроблено (не закомічено)

### 1. Логування в усі сервіси
- `src/status_logger.py`: component_name + log_exception
- `src/common/ipc.py`: client_id, таймінги, детальні помилки
- `src/capture/capture_service.py`: PipeWire логи, frame stats
- `src/whisper/whisper_service.py`: таймінг моделі + RTFX
- `src/whisper/hybrid_whisper_service.py`: mode таймінг
- `src/whisper/wyoming_client.py`: retry, wire stats
- `src/translate/translate_service.py`: таймінг моделі + per-request
- `src/tts/tts_service.py`: таймінг синтезу + аудіо розмір
- `src/playback/playback_service.py`: underrun detection, stats
- `src/translation_system.py`: pipeline cycle таймінги, connectivity WARNING
- `src/adapters/direct_adapter.py`: detailed preflight, reconfigure diff

### 2. Service lifecycle (subprocess spawn/terminate)
- `src/adapters/direct_adapter.py`: `_spawn_service()`, `_ensure_essential_services()`, `_wait_for_services()`, `_stop_subprocesses()`
- `auto_spawn_services` параметр (default True)
- `tests/test_direct_adapter.py`: `auto_spawn_services=False`

### 3. Багфікси
- `src/translate/translate_service.py`: `self.status` перенесено перед `_initialize_model()` (був AttributeError)
- `flake-global/dev/devshell.nix`: додано `en_core_web_sm` Nix derivation (spaCy для kokoro)
- `flake-global/parts/python/tts.nix`: додано `en_core_web_sm` для production

### 4. Контекстні файли (created)
- `context/service-lifecycle.md` — документація lifecycle
- `context/model-downloads.md` — як качати моделі
- `plans/reFUCKtor.md` — план рефакторингу
- `plans/integration-swarm-nix.md` — інтеграція в swarm-nix

## Статус запуску

### Сервіси Spark (DirectAdapter)
- ✅ Start → spawn whisper / translate / tts + pipeline loop
- ✅ Stop → terminate subprocesses + cleanup
- ⬜ Wyoming server не запущено (`use_wyoming=true` в конфігу, але порт 10300 пустий)

### Моделі
- ✅ `en_core_web_sm` — додана в flake, працює
- ✅ `hexgrad/Kokoro-82M` — закешована
- ⬜ `Helsinki-NLP/opus-mt-uk-en` — НЕ закешована (translate_service впаде)
- ⬜ `faster-whisper small` — знадобиться якщо вимкнути Wyoming

## Що далі

1. **Скачати модель перекладу** — найкритичніше:
   ```bash
   nix develop --command python3 -c "from transformers import pipeline; pipeline('translation', model='Helsinki-NLP/opus-mt-uk-en')"
   ```

2. **Вирішити Wyoming** — або вимкнути в `~/.config/real-time-translator/config.yml`:
   ```yaml
   wyoming:
     use_wyoming: false
   ```
   Або запустити Wyoming server.

3. **Запустити end-to-end тест** — `python3 -m src.main`

## Файли для коміту

```bash
git add \
  src/status_logger.py \
  src/common/ipc.py \
  src/capture/capture_service.py \
  src/whisper/whisper_service.py \
  src/whisper/hybrid_whisper_service.py \
  src/whisper/wyoming_client.py \
  src/translate/translate_service.py \
  src/tts/tts_service.py \
  src/playback/playback_service.py \
  src/translation_system.py \
  src/adapters/direct_adapter.py \
  flake-global/dev/devshell.nix \
  flake-global/parts/python/tts.nix \
  tests/test_direct_adapter.py \
  context/service-lifecycle.md \
  context/model-downloads.md \
  plans/reFUCKtor.md \
  plans/integration-swarm-nix.md
```

## Імплементація не змінилась?

**Ні, не змінилась.** Останній коміт `0b91c16 refUCK` — всі наші зміни ще незакомічені. Жодних зовнішніх змін не було. Стан репозиторію той самий, що ми залишили.
