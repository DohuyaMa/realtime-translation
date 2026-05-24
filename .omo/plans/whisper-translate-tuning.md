# Whisper + Translation Tuning (Medium Scope)

## TL;DR

> **Quick Summary**: Connect existing config keys to actual whisper/translate calls, add tuning parameters (beam_size, temperature, initial_prompt, num_beams, repetition_penalty, max_length), add Settings UI controls, and add post-processing to translation output.
>
> **Deliverables**:
> - Whisper service accepts `--beam-size`, `--temperature`, `--initial-prompt` CLI args and passes them to `model.transcribe()`
> - Translate service accepts `--num-beams`, `--repetition-penalty`, `--max-length` CLI args and passes them to `model.generate()`
> - Translation post-processing: capitalize, punctuate, trim
> - DirectAdapter reads tuning params from config and passes as CLI args
> - Settings UI has sliders/fields for all tuning params
> - Config defaults updated
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 3 waves
> **Critical Path**: Config → Whisper → Translate → Adapter → UI

---

## Context

### Original Request
User wants Medium-level improvements: connect config to actual model calls, add sliders in Settings, add Whisper `initial_prompt`, and add translation post-processing.

### Key Architecture Facts
- **Whisper service** (`whisper_service.py`, `hybrid_whisper_service.py`) reads CLI args in `main()`, passed to `run_server()`, and used in `model.transcribe()`. Currently passes only `language` and `vad_filter=True`.
- **Translate service** (`translate_service.py`) reads CLI args in `main()`, passed to `TranslationService.__init__()`, and used in `model.generate()`. Currently passes **no** generation params (defaults).
- **Config** has `translation.beam_size: 5` and `translation.temperature: 0.3` in `config/default.yml` and `ConfigManager._default_config` — **but these are never read by any service**.
- **DirectAdapter** (`direct_adapter.py`) spawns whisper and translate as subprocesses with CLI args. Also has `_ensure_essential_services()` method.
- **Systemd services** have hardcoded args set at build time in `home-manager-module.nix`. Tuning params from config won't affect systemd services (production defaults). Only DirectAdapter-spawned services get runtime config.
- **UI settings flow**: `SettingsDialog.accept()` → `_save_config()` writes to config → `main_window.on_general_settings_changed()` handles Wyoming/language changes. Tuning params are saved to config but NOT propagated to running services. User needs to restart pipeline to pick up new params.

---

## Work Objectives

### Core Objective
Add configurable tuning parameters (beam_size, temperature, initial_prompt for Whisper; num_beams, repetition_penalty, max_length for Translate) with Settings UI controls, and connect them end-to-end to the actual model calls.

### Must Have
- Whisper `model.transcribe()` receives `beam_size`, `temperature`, `initial_prompt` from config
- Translate `model.generate()` receives `num_beams`, `repetition_penalty`, `max_length` from config
- Application restart or pipeline restart picks up new values
- Settings UI has sliders/fields for all tuneable params
- New params persist across app restarts (saved to config file)
- Translation output has basic post-processing (capitalize, punctuate, trim)

### Must NOT Have
- No live-reload of params on running services (must restart pipeline)
- No changes to systemd service definitions (home-manager-module.nix stays as-is)
- No changes to Wyoming mode (params only affect local whisper)
- No GPU memory management, no model quantization changes
- No fine-tuning or training

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Automated tests**: None (python import tests used for verification)
- **Infrastructure**: No test framework needed
- **Agent-Executed QA**: Every task verified via CLI tests (import/ping/assert)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — parallel):
├── Task 1: Config defaults (default.yml + ConfigManager._default_config) [quick]
├── Task 2: Whisper service — add CLI args + transcribe() params [quick]
└── Task 3: Hybrid-whisper service — same CLI args + transcribe() params [quick]

Wave 2 (Core logic — parallel):
├── Task 4: Translate service — add CLI args + generate() params + _post_process() [unspecified-high]
└── Task 5: DirectAdapter — read config + pass as CLI args when spawning [quick]

Wave 3 (UI — depends on config keys existing):
├── Task 6: settings_dialog _load_config/_save_config — add new keys [quick]
└── Task 7: Settings UI — add sliders/fields for all tuning params [quick]

Wave FINAL:
├── Task F1: Build + verify compilation
├── Task F2: Deploy via sys-rebuild
└── Task F3: Verify with offscreen Qt test
```

---

## TODOs

- [ ] 1. **Config: Add tuning default keys**

  **What to do**:
  - Edit `config/default.yml`: replace the old `beam_size: 5` / `temperature: 0.3` under `translation:` with properly namespaced keys under new `models:` hierarchy
  - Edit `src/core/config.py` `ConfigManager._default_config` to match the new keys

  **New config structure to add**:
  ```yaml
  models:
    whisper:
      beam_size: 5
      temperature: 0.0
      initial_prompt: ""
    translate:
      num_beams: 4
      repetition_penalty: 1.2
      max_length: 200
  ```

  **ConfigManager._default_config** updates:
  - Under `models.whisper`: add `'beam_size': 5`, `'temperature': 0.0`, `'initial_prompt': ''`
  - Under `models.translate` (new sub-dict): add `'num_beams': 4`, `'repetition_penalty': 1.2`, `'max_length': 200`

  **Acceptance Criteria**:
  - [ ] `config/default.yml` has the new keys under `models.whisper.*` and `models.translate.*`
  - [ ] `ConfigManager._default_config` has matching keys
  - [ ] `python3 -c "from src.core.config import get_config_manager; c=get_config_manager(); print(c.get('models.whisper.beam_size'))"` prints `5`

  **QA Scenarios**:
  ```
  Scenario: Verify new config defaults
    Tool: python3 -c
    Steps:
      1. Import ConfigManager
      2. Get default values for each new key
    Expected Result: beam_size=5, temperature=0.0, initial_prompt='', num_beams=4, repetition_penalty=1.2, max_length=200
    Evidence: .omo/evidence/task-1-config-defaults.txt
  ```

  **Wave**: 1

- [ ] 2. **Whisper service: add CLI args + transcribe params**

  **What to do**:
  - Edit `src/whisper/whisper_service.py`:
    - Add `--beam-size` (int, default=5), `--temperature` (float, default=0.0), `--initial-prompt` (str, default="") to argparse
    - Pass to `run_server()` function signature
    - Store in module-level or connection-level variables
    - Pass to `model.transcribe()` call: `beam_size=beam_size`, `temperature=temperature`, `initial_prompt=initial_prompt` (only if non-empty string)

  **Specific code changes**:

  In `main()`, add args after `--compute-type`:
  ```python
  parser.add_argument("--beam-size", type=int, default=5, help="Beam search width")
  parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature (0=deterministic)")
  parser.add_argument("--initial-prompt", type=str, default="", help="Prompt text to bias recognition")
  ```

  In `run_server()`, add params and pass to the transcribe call:
  ```python
  def run_server(socket_path, model_name, device, compute_type, beam_size=5, temperature=0.0, initial_prompt=""):
  ```

  In the transcribe call (line ~112-116):
  ```python
  transcribe_kwargs = dict(
      language=session.language,
      vad_filter=True,
      beam_size=beam_size,
      temperature=temperature,
  )
  if initial_prompt:
      transcribe_kwargs["initial_prompt"] = initial_prompt
  segments, info = model.transcribe(audio, **transcribe_kwargs)
  ```

  **Must NOT do**:
  - Do NOT change Wyoming mode behavior
  - Do NOT pass these params to Wyoming

  **Wave**: 1

- [ ] 3. **Hybrid-whisper service: same CLI args + transcribe params**

  **What to do**:
  - Edit `src/whisper/hybrid_whisper_service.py` with identical changes as Task 2
  - Add `--beam-size`, `--temperature`, `--initial-prompt` to argparse
  - Pass to `run_server()` and to the local (non-Wyoming) transcribe call

  **Note**: Only the `else` branch (local processing) inside `cmd == "stop"` needs these params. Wyoming mode forwards to external server.

  **Wave**: 1

- [ ] 4. **Translate service: add CLI args + generate params + _post_process()**

  **What to do**:

  **A. Add CLI args** to `main()`:
  ```python
  parser.add_argument("--num-beams", type=int, default=4, help="Beam search width for translation")
  parser.add_argument("--repetition-penalty", type=float, default=1.2, help="Repetition penalty (1.0=none)")
  parser.add_argument("--max-length", type=int, default=200, help="Maximum output tokens per segment")
  ```

  **B. Store in TranslationService.__init__():**
  ```python
  self._num_beams = num_beams
  self._repetition_penalty = repetition_penalty
  self._max_length = max_length
  ```

  **C. Use in _handle_translate_text()** (replace the bare `model.generate(**inputs, **gen_kwargs)`):
  ```python
  gen_kwargs = {}
  if getattr(self, '_forced_bos_token_id', None) is not None:
      gen_kwargs['forced_bos_token_id'] = self._forced_bos_token_id
  # Add tuning params
  if self._num_beams > 1:
      gen_kwargs['num_beams'] = self._num_beams
  gen_kwargs['repetition_penalty'] = self._repetition_penalty
  gen_kwargs['max_length'] = self._max_length
  gen_kwargs['early_stopping'] = True
  tokens = self._model.generate(**inputs, **gen_kwargs)
  ```

  **D. Add _post_process() method:**
  ```python
  import re

  def _post_process(self, text: str) -> str:
      text = text.strip()
      if not text:
          return text
      # Capitalize first letter
      text = text[0].upper() + text[1:]
      # Ensure sentence ends with punctuation
      if text[-1] not in '.!?':
          text += '.'
      return text
  ```

  Call after `batch_decode`:
  ```python
  translated_text = self._tokenizer.batch_decode(tokens, skip_special_tokens=True)[0]
  translated_text = self._post_process(translated_text)
  ```

  **Must NOT do**:
  - Do NOT change `_load_nllb` or `_load_marian` model loading
  - Post-processing must be simple — no AI, no grammar checking

  **Wave**: 2

- [ ] 5. **DirectAdapter: read config + pass as CLI args**

  **What to do**:
  - Edit `src/adapters/direct_adapter.py`
  - In `_ensure_essential_services()` method, read whisper/translate params from config and append them to the CLI args list

  **For whisper** (local mode, around line 196-197):
  ```python
  whisper_args = ['--socket-path', whisper_socket]
  # Read tuning params from config
  from ..core.config import get_config_manager
  cfg = get_config_manager()
  beam_size = cfg.get('models.whisper.beam_size', 5)
  temperature = cfg.get('models.whisper.temperature', 0.0)
  initial_prompt = cfg.get('models.whisper.initial_prompt', '')
  whisper_args.extend(['--beam-size', str(beam_size), '--temperature', str(temperature)])
  if initial_prompt:
      whisper_args.extend(['--initial-prompt', initial_prompt])
  ```

  **For translate** (around line 202-203):
  ```python
  translate_args = ['--socket-path', translate_socket]
  num_beams = cfg.get('models.translate.num_beams', 4)
  repetition_penalty = cfg.get('models.translate.repetition_penalty', 1.2)
  max_length = cfg.get('models.translate.max_length', 200)
  translate_args.extend([
      '--num-beams', str(num_beams),
      '--repetition-penalty', str(repetition_penalty),
      '--max-length', str(max_length),
  ])
  ```

  **Must NOT do**:
  - Do NOT modify systemd services (home-manager-module.nix stays unchanged)
  - Do NOT modify Wyoming mode args

  **Wave**: 2

- [ ] 6. **settings_dialog _load_config/_save_config: add new keys**

  **What to do**:
  - Edit `src/ui/widgets/settings_dialog.py`
  - In `_load_config()`, add:
    ```python
    "beam_size": cfg.get("models.whisper.beam_size", 5),
    "temperature": cfg.get("models.whisper.temperature", 0.0),
    "initial_prompt": cfg.get("models.whisper.initial_prompt", ""),
    "num_beams": cfg.get("models.translate.num_beams", 4),
    "repetition_penalty": cfg.get("models.translate.repetition_penalty", 1.2),
    "max_length": cfg.get("models.translate.max_length", 200),
    ```
  - In `_save_config()`, add:
    ```python
    cfg.set("models.whisper.beam_size", settings.get("beam_size", 5))
    cfg.set("models.whisper.temperature", settings.get("temperature", 0.0))
    cfg.set("models.whisper.initial_prompt", settings.get("initial_prompt", ""))
    cfg.set("models.translate.num_beams", settings.get("num_beams", 4))
    cfg.set("models.translate.repetition_penalty", settings.get("repetition_penalty", 1.2))
    cfg.set("models.translate.max_length", settings.get("max_length", 200))
    ```

  **Wave**: 3

- [ ] 7. **Settings UI: add sliders/fields for all tuning params**

  **What to do**:
  - Edit `src/ui/widgets/settings_dialog.py` in `_GeneralTab._build()`
  - Add a new `QGroupBox("Recognition Tuning")` after the TTS group with:
    - `beam_size`: `QSpinBox` range 1-10, suffix " beams"
    - `temperature`: `QDoubleSpinBox` range 0.0-1.0, step 0.1, suffix " temp"
    - `initial_prompt`: `QLineEdit` placeholder "e.g., conversation about technology"
  - Add a new `QGroupBox("Translation Tuning")` after that with:
    - `num_beams`: `QSpinBox` range 1-10, suffix " beams"
    - `repetition_penalty`: `QDoubleSpinBox` range 1.0-2.0, step 0.1, suffix " rep"
    - `max_length`: `QSpinBox` range 50-500, step 10, suffix " tokens"
  - Add corresponding class attributes and `get_settings()` returns
  - Add `setValue()` in `_build()` to load from `self._cfg`

  **Note**: Increase `setMinimumHeight` to ~700 to fit new content.

  **Wave**: 3

---

## Final Verification Wave

- [ ] F1. **Build + verify compilation** — `quick`
  Run `nix build .#ui`. Verify no build errors. Check the built store path contains the new CLI args (grep the wrapped scripts).
  Output: `Build [PASS/FAIL]`

- [ ] F2. **Deploy via sys-rebuild** — `quick`
  Run `cd ~/repos/swarm-nix/system-conf && nix flake lock --update-input realtime-translation && sudo nixos-rebuild switch --flake .#cyborg`. Verify deployment success.

- [ ] F3. **Verify with offscreen Qt test** — `unspecified-high`
  Run `QT_QPA_PLATFORM=offscreen python3 -c "..."` to create SettingsDialog, verify all new widgets present, verify save/load roundtrip.
  Output: `Dialog OK | Controls OK | Roundtrip OK`

---

## Commit Strategy

- **Task 1**: `config(tuning): add whisper/translate tuning default keys`
- **Task 2-3**: `feat(whisper): add beam_size, temperature, initial_prompt CLI args`
- **Task 4**: `feat(translate): add tuning params, post-processing`
- **Task 5**: `feat(adapter): propagate tuning params from config to services`
- **Task 6-7**: `feat(ui): add tuning parameter controls to Settings`

---

## Success Criteria

### Verification Commands
```bash
cd /home/dmaslo/repos/real-time-transletor
nix develop -c python3 -c "
from src.core.config import get_config_manager
c = get_config_manager()
print('beam_size:', c.get('models.whisper.beam_size'))
print('temperature:', c.get('models.whisper.temperature'))
print('initial_prompt:', c.get('models.whisper.initial_prompt'))
print('num_beams:', c.get('models.translate.num_beams'))
print('repetition_penalty:', c.get('models.translate.repetition_penalty'))
print('max_length:', c.get('models.translate.max_length'))
"
```

### Final Checklist
- [ ] All config keys present with correct defaults
- [ ] Whisper service accepts and uses beam_size, temperature, initial_prompt
- [ ] Hybrid-whisper service (local mode) accepts and uses the same
- [ ] Translate service accepts and uses num_beams, repetition_penalty, max_length
- [ ] Translate output is capitalized, punctuated, trimmed
- [ ] DirectAdapter propagates config values when spawning services
- [ ] Settings UI has controls for all 6 params
- [ ] Settings save/load roundtrip works
- [ ] Build succeeds
- [ ] Deploy succeeds
