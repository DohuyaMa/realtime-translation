# Testing Plan for Systemd Update

## Purpose

Create comprehensive testing strategy to ensure the new systemd architecture works correctly with all components properly integrated.

## Scope

This plan covers unit, integration, and system testing for the new architecture. It ensures all components work together after the migration from hardcoded paths and monolithic environment to modular, properly packaged services.

## Entry Points

- tests/systemd-update/build-test.nix
- tests/systemd-update/runtime-test.py
- tests/systemd-update/socket-test.py
- tests/unit/
- tests/integration/
- tests/systemd/

## Planned Changes

- [ ] Create build tests for all new packages
- [ ] Create runtime configuration tests
- [ ] Create socket communication tests
- [ ] Create integration tests for full pipeline

## Test Coverage

- Verify each buildPythonApplication builds → nix flake check
- Verify runtime config creates proper paths → unit test
- Verify socket communication works → integration test
- Verify full pipeline functionality → integration test
- Verify systemd service activation → integration test

## Dependencies

### Hard Dependencies

- blocks: all other plan files (testing validates all changes)

### Soft Dependencies

- should be reviewed after: all other plan files are implemented

## Obsolete / To Be Removed

- Old testing approaches that don't match new architecture
- Tests that rely on hardcoded paths

## Architectural Invariants

- tests validate new architecture
- no hardcoded /tmp paths in tests
- tests work with new XDG-compliant paths

## Target Audience

- developers
- maintainers

## Per-File Focus

tests/systemd-update/build-test.nix:

- Build validation for packages

tests/systemd-update/runtime-test.py:

- Runtime configuration validation

tests/systemd-update/socket-test.py:

- Socket communication validation

## Completion Criteria

- all Planned Changes checked
- all tests pass
- test coverage includes all architectural changes

## Post-Implementation Notes

- What changed from original plan
- What was removed as obsolete
- What should be refactored later

## Completed / Coordinating with Parallel Execution

### Completed Items

- [ ] Create build tests for all new packages — Not yet started
- [ ] Create runtime configuration tests — Not yet started
- [ ] Create socket communication tests — Not yet started
- [ ] Create integration tests for full pipeline — Not yet started

### Notes for Parallel Execution

- This plan depends on all other plan files being implemented first
- Tests will validate the new architecture after all changes are complete
- Runtime configuration tests should align with src/core/runtime.py implementation
- Socket communication tests should work with the new XDG-compliant paths

**flake-parts debug + nix-unit варто інтегрувати як окремий шар тестування** , але  **не змішувати їх із Python/systemd runtime-тестами** . Нижче — чітка рекомендація *як саме* і *де* це місце в архітектурі тестів.

---

## 1. Розділи тести на 3 чіткі рівні

### Рівень 1 — **Nix / Flake integrity (static)**

**Інструменти:**

* flake-parts `debug = true`
* `nix repl`
* `nix-unit`
* `nix flake check`

**Що перевіряється:**

* структура flake
* значення options
* perSystem / allSystems
* наявність derivations, checks, packages
* відсутність implicit magic

👉  **Це “цілісне тестування flake”** , про яке ти питаєш.

---

### Рівень 2 — **Build / packaging**

**Інструменти:**

* `nix flake check`
* build-тести (`build-test.nix`)

**Що перевіряється:**

* `buildPythonApplication` збирається
* entrypoints існують
* залежності ізольовані

---

### Рівень 3 — **Runtime / system**

**Інструменти:**

* pytest
* systemd tests
* socket tests

**Що перевіряється:**

* IPC
* audio
* TTS
* UI
* реальні сервіси

---

## 2. Куди саме додати flake-parts debug

### ❌ Не сюди

* **не** в pytest
* **не** в systemd tests
* **не** в Python test runner

### ✅ Сюди

У  **Testing Plan** , як  **окремий підрозділ** :

```md
## Nix / Flake Integrity Tests (flake-parts)

### Purpose
Validate flake structure, options, perSystem consistency, and module wiring
independently of runtime behavior.

### Tools
- flake-parts debug
- nix repl
- nix-unit
- nix flake check
```

---

## 3. Як практично це реалізувати

### 3.1 flake-parts debug — **інтерактивний контроль**

У flake:

```nix
{
  debug = true;
  systems = [ "x86_64-linux" ];
}
```

Це дає:

* трасування `options.*`
* джерела значень
* перевірку perSystem wiring

📌 **Рекомендація**
Задокументуй у `testing-plan.md`  *типові repl-запити* , не автоматизуй їх.

```md
### Manual Inspection (REPL)

- currentSystem.allModuleArgs.pkgs.stdenv.hostPlatform.system
- currentSystem.options.packages
- debug.options.systemd.services.declarations
```

---

### 3.2 nix-unit — **автоматичні flake-тести**

Це  **ідеальний кандидат для nix flake check** .

#### Приклад логічних тестів для твого проєкту:

```nix
flake.tests = {
  "all python services are defined" = {
    expr = builtins.attrNames self.packages.x86_64-linux;
    expected = [
      "capture"
      "whisper"
      "translate"
      "tts"
      "playback"
    ];
  };

  "systemd services exist" = {
    expr = builtins.attrNames self.nixosModules.systemd.services;
    expected = [
      "rt-whisper"
      "rt-translate"
      "rt-tts"
    ];
  };
};
```

📌 Це:

* не runtime
* не Python
* **структурна гарантія**

---

## 4. Як це узгодити з існуючим Python test runner

Твій `main test runner` —  **коректний** , але:

### Важливо:

* він **не повинен запускатися з nix flake check**
* він **runtime-only**

### Рекомендована інтеграція:

| Контекст    | Що запускати         |
| ------------------- | ------------------------------- |
| `nix flake check` | nix-unit + build tests          |
| `nix develop`     | pytest runner                   |
| CI (full)           | `nix flake check`→`pytest` |

📌 **Не намагайся “загнати pytest у nix-unit”** — це архітектурно неправильно.

---

## 5. Як оновити твій Testing Plan

### Додай нову секцію:

```md
## Nix / Flake Integrity Tests

### Scope
Validate flake-parts structure, option wiring, perSystem correctness,
and service/package declarations.

### Tools
- flake-parts debug
- nix repl
- nix-unit
- nix flake check

### Test Coverage
- Verify all services are declared in flake outputs
- Verify perSystem packages exist
- Verify systemd module wiring
- Verify no implicit PYTHONPATH or shared env

### Execution
- Automated: nix flake check
- Manual: nix repl (debug)
```

---

Висновок (ключове)

* ✅ **Так, це правильне місце для цілісного тестування**
* 🔒 flake-parts + nix-unit = **архітектурна безпека**

---

## Nix / Flake Integrity Tests

### Scope
Validate flake-parts structure, option wiring, perSystem correctness,
and service/package declarations.

### Tools
- flake-parts debug
- nix repl
- nix-unit
- nix flake check

### Test Coverage
- Verify all services are declared in flake outputs
- Verify perSystem packages exist
- Verify systemd module wiring
- Verify no implicit PYTHONPATH or shared env

### Execution
- Automated: nix flake check
- Manual: nix repl (debug)

---

## Layer 1: Nix / Flake Integrity (static)

**Tools:**
- flake-parts `debug = true`
- `nix repl`
- `nix-unit`
- `nix flake check`

**What is verified:**
- Structure of flake
- Values of options
- perSystem / allSystems
- Existence of derivations, checks, packages
- Absence of implicit magic

👉 This is the "flake integrity testing" that ensures the build system is properly structured.

---

## Layer 2: Build / packaging

**Tools:**
- `nix flake check`
- build-тести (`build-test.nix`)

**What is verified:**
- `buildPythonApplication` builds successfully
- Entrypoints exist
- Dependencies are isolated

---

## Layer 3: Runtime / system

**Tools:**
- pytest
- systemd tests
- socket tests

**What is verified:**
- IPC
- audio
- TTS
- UI
- Real services

---

## Manual Inspection (REPL) - Typical Queries

For flake-parts debug inspection, these are typical REPL queries:
- currentSystem.allModuleArgs.pkgs.stdenv.hostPlatform.system
- currentSystem.options.packages
- debug.options.systemd.services.declarations
- debug.systems
- currentSystem.options.pre-commit.settings.files
- debug.options.system.files
- debug.options.systems.declarations

---

## Integration with Existing Python Test Runner

It's important to maintain separation between testing layers:

| Context | What to run |
| ------------------- | ------------------------------- |
| `nix flake check` | nix-unit + build tests |
| `nix develop` | pytest runner |
| CI (full) | `nix flake check` → `pytest` |

Do not try to mix pytest with nix-unit - they serve different architectural layers.

---

## Implementation Steps for Nix/Flake Integrity Tests

1. Add nix-unit to flake inputs:
   ```nix
   nix-unit = {
     url = "github:nix-community/nix-unit";
   };
   ```

2. Import nix-unit module in flake-parts:
   ```nix
   imports = [
     inputs.nix-unit.modules.flake.default
   ];
   ```

3. Enable debug mode for flake-parts:
   ```nix
   {
     debug = true;
     systems = [ "x86_64-linux" ];
   }
   ```

4. Define nix-unit tests in flake:
   ```nix
   flake.tests = {
     "all python services are defined" = {
       expr = builtins.attrNames self.packages.x86_64-linux;
       expected = [
         "capture"
         "whisper"
         "translate"
         "tts"
         "playback"
       ];
     };

     "systemd services exist" = {
       expr = builtins.attrNames self.nixosModules.systemd.services;
       expected = [
         "rt-whisper"
         "rt-translate"
         "rt-tts"
       ];
     };
   };
   ```

---

## Implementation Steps for Build Tests (build-test.nix)

1. Create tests/systemd-update/build-test.nix
2. Define package build tests using nix-unit
3. Verify that all Python applications build successfully
4. Test that entrypoints are properly configured

---

## Implementation Steps for Runtime Tests (runtime-test.py)

1. Create tests/systemd-update/runtime-test.py
2. Test runtime configuration validation
3. Verify XDG-compliant paths work correctly
4. Test environment variable configuration

---

## Implementation Steps for Socket Tests (socket-test.py)

1. Create tests/systemd-update/socket-test.py
2. Test socket communication between services
3. Verify systemd socket activation works
4. Test IPC mechanisms between components
* 🧪 pytest = **поведінкова перевірка**
* ⚠️ не змішувати рівні

Якщо хочеш — наступний крок можу:

* запропонувати **реальний nix-unit test suite саме під твій flake**
* або **переробити testing-plan.md в “gold standard” для CI**
