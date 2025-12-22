pactl - в іншому пакеті
pipewire
Server and user space API to deal with multimedia pipelines
Name: pipewireVersion: 1.4.9Outputs: out dev doc installedTests jack man🌐 Homepage📦 SourceLicense: MIT License
▲▲▲ Hide package details ▲▲▲
How to install pipewire?
nix-shell
NixOS Configuration
nix-env
A nix-shell will temporarily modify your $PATH environment variable. This can be used to try a piece of software before deciding to permanently install it.

nix-shell -p pipewire
Programs provided
pipewire pipewire-aes67 pipewire-avb pipewire-pulse pipewire-vulkan pw-cat pw-cli pw-config pw-container pw-dot pw-dsdplay pw-dump pw-encplay pw-jack pw-link pw-loopback pw-metadata pw-mididump pw-midiplay pw-midirecord pw-mon pw-play pw-profiler pw-record pw-reserve pw-top pw-v4l2 spa-acp-tool spa-inspect spa-json-dump spa-monitor spa-resample

Maintainers
Ilan Joselevich <personal@ilanjoselevich.com>
Ilya K. <me@0upti.me>
✉️ Mail to all maintainers
@Kranzes @K900
Platforms
aarch64-linux
i686-linux
x86_64-linux

pulseaudio
Sound server for POSIX and Win32 systems
Name: pulseaudioVersion: 17.0Outputs: out dev🌐 Homepage📦 SourceLicense: GNU Library General Public License v2 or later
▲▲▲ Hide package details ▲▲▲
PulseAudio is a sound server for POSIX and Win32 systems. A sound server is basically a proxy for your sound applications. It allows you to do advanced operations on your sound data as it passes between your application and your hardware. Things like transferring the audio to a different machine, changing the sample format or channel count and mixing several sounds into one are easily achieved using a sound server.

How to install pulseaudio?
nix-shell
NixOS Configuration
nix-env
A nix-shell will temporarily modify your $PATH environment variable. This can be used to try a piece of software before deciding to permanently install it.

nix-shell -p pulseaudio
Programs provided
pa-info pacat pacmd pactl padsp pamon paplay parec parecord pasuspender pulseaudio

Maintainers
Jason O'Conal <jason@oconal.id.au>
@lovek323
Platforms
aarch64-darwin
aarch64-linux
i686-linux
x86_64-darwin
x86_64-linux

pipewire-pulse - не існує в теперішніх репозиторіях



Зараз ситуація така: `pactl` більше не є частиною `pipewire` у Nixpkgs, а йде в пакеті  **pulseaudio** , або в `pulseaudio-utils`. В `pipewire` основні утиліти це `pw-*`, `pipewire-pulse` окремо може не існувати.

### Рекомендації для NixOS / flake

1. **Додати правильні пакети** у `environment.systemPackages` або `nix-shell`:

```nix
environment.systemPackages = with pkgs; [
  pipewire
  pipewire-alsa
  pipewire-jack
  pipewire-pulse  # якщо є, або pulseaudio
  pulseaudio      # для pactl
];
```

2. **Для скриптів, які викликають `pactl`** :

* На Wayland / Plasma6 в тебе повинен бути `pipewire-pulse` (або `pulseaudio` для сумісності).
* Якщо `pipewire-pulse` немає — використовуй  **`pactl` з пакету `pulseaudio`** , він все одно підключається до `pipewire` через socket.

2. **Перевірка на runtime** у Python:

```python
import shutil
if not shutil.which("pactl"):
    raise RuntimeError("pactl не знайдено, встановіть pulseaudio або pipewire-pulse")
```

4. **Nix flake devShell** :

```nix
devShells.default = pkgs.mkShell {
  buildInputs = [
    pkgs.pipewire
    pkgs.pulseaudio
  ];
};
```

5. **Після цього твій `ensure_pipewire_nodes()` буде працювати без змін** , бо `pactl` доступний в `$PATH`.

---

Якщо хочеш, я можу скласти  **готовий flake/devShell для твоєї RT-трансляційної системи** , щоб `pactl`, `pipewire`, `pw-*` були підключені одразу і скрипт працював автономно.


Так, можна, але з нюансами: у NixOS **pipewire** і пов’язані сервіси (`pipewire-pulse`, `pipewire-media-session` / `wireplumber`) керуються через **`services.pipewire.*`** у `configuration.nix`.

У flake це виглядатиме так:

```nix
{
  description = "Real-time translation system";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

  outputs = { self, nixpkgs }: {
    nixosConfigurations.cyborg = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        {
          services.pipewire.enable = true;
          services.pipewire.pulse.enable = true; # замінює pulseaudio
          services.pipewire.media-session.enable = true; # або wireplumber
        
          # аудіо бекенд ALSA / JACK / Bluetooth
          sound.enable = true;
          hardware.pulseaudio.enable = false; # вимкнути якщо використовуєш pipewire-pulse
        
          # додатково можна прописати джерела і sinks
          # наприклад virtual sinks можна робити через PipeWire конфіг:
          # /etc/pipewire/pipewire.conf або кастомні .conf у /etc/pipewire/media-session.d/
        }
      ];
    };
  };
}
```

**Особливості:**

1. **Virtual sinks** (`rt_virtual_input`, `rt_virtual_output`) не можна прямо декларативно створити через NixOS option. Потрібно або:
   * написати `.conf` для `pipewire` або `wireplumber` і кинути його через `environment.etc` у flake;
   * або створювати їх через startup-скрипт / systemd service (`pw-loopback`, `pw-link`, `pw-cat`).
2. **Flake + Home Manager** може містити додаткові утиліти (`pactl`, `pw-*`) через `packages`, щоб твій Python скрипт міг їх викликати.

---

Якщо хочеш, можу запропонувати  **готову структуру flake** , де:

* pipewire з virtual sinks автоматично піднімається;
* Python RT-система має всі утиліти в `$PATH`;
* можна підключати UI і systemd юніти прямо з flake.


Ні,  **`nix develop` не запускає systemd юніти** .

* `nix develop` створює **тимчасове середовище (shell)** із пакетами з flake.
* Systemd юніти піднімаються тільки через **`systemctl --user start <unit>`** або на старті користувача (якщо юніт прописаний у `wantedBy = ["default.target"]`).

Якщо хочеш, щоб юніт піднімався під час розробки без повної інтеграції у NixOS:

1. В flake додай юніт у `flake#userUnits`.
2. Вручну виконай:
   ```bash
   systemctl --user daemon-reload
   systemctl --user start rt-pipewire-sinks.service
   ```
3. Потім вже можна запускати додаток у `nix develop` — юніт буде доступний.

Іншими словами,  **розробницький shell і systemd юніти — окремі рівні** , вони не стартують автоматично один із одним.

Якщо хочеш, можу запропонувати **рішення для автоматичного старту юніта при `nix develop`** через wrapper-скрипт.
