Для інтеграції `wyoming-faster-whisper` у твій flake **тільки в дев-оточенні** (без systemd) раджу:

1. **Додати пакет у devShell** :

```nix
devShells.default.buildInputs = [
  ...
  pkgs.wyoming-faster-whisper
];
```

2. **Створити wrapper для запуску** (як у тебе з `serviceWrapper` для інших сервісів):

```nix
whisperWyomingService = pkgs.writeShellApplication {
  name = "rt-whisper-wyoming";
  runtimeInputs = [ pkgs.python313 ];
  text = ''
    exec ${pkgs.python313.interpreter} -m wyoming_faster_whisper --uri tcp://0.0.0.0:10300 --device cpu --model tiny-int8 "$@"
  '';
};
```

3. **Додати до shellHook** (опціонально), щоб легко стартувати:

```nix
shellHook = ''
  echo "Wyoming faster-whisper ready: run ./result/bin/rt-whisper-wyoming"
'';
```

4. **Не створювати systemd user services** , поки це лише дев-оточення. Пізніше, для продакшн, можна буде скопіювати конфіг в `systemd.user.services`.
5. **Кеші HuggingFace** можна спільно використовувати з іншими сервісами Whisper:

```bash
export HF_HOME=$HOME/.cache/huggingface
export TRANSFORMERS_CACHE=$HOME/.cache/transformers
```

Це дозволяє запускати `wyoming-faster-whisper` локально, із своїм Python env і pipewire, без зміни системних юнітів.

Якщо хочеш, можу зробити готовий snippet  **для твого flake** , який включає `wyoming-faster-whisper` у devShell з wrapper і shellHook.
