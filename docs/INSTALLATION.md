# Installation Guide for NixOS

## Prerequisites

Before installing the Real-Time Translation System, ensure your NixOS system meets these requirements:

- NixOS 23.11 or later
- At least 8GB RAM (16GB recommended)
- 10GB free disk space
- Working audio setup
- Internet connection for initial setup

## 1. System Configuration

### Configure NixOS

Add the following to your `/etc/nixos/configuration.nix`:

```nix
{ config, pkgs, ... }:

{
  # Enable sound with pipewire
  sound.enable = true;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
    jack.enable = true;
  };

  # Required packages
  environment.systemPackages = with pkgs; [
    # Python and development tools
    python3
    python3Packages.pip
    python3Packages.virtualenv
    
    # Qt dependencies
    qt5.qtbase
    qt5.qtmultimedia
    
    # Audio tools
    pulseaudio
    pavucontrol
    
    # AI/ML dependencies
    cudaPackages.cudatoolkit
    cudaPackages.cudnn
    
    # Development tools
    git
    gcc
    cmake
    pkg-config
    
    # Docker (optional)
    docker
    docker-compose
  ];

  # Enable Docker if needed
  virtualisation.docker.enable = true;
  
  # Add user to required groups
  users.users.your-username = {
    extraGroups = [ "audio" "docker" ];
  };
}
```

After adding these configurations, rebuild NixOS:

```bash
sudo nixos-rebuild switch
```

## 2. Install Ollama

### Add Ollama Binary Cache

Add the following to your `/etc/nixos/configuration.nix`:

```nix
{
  nix = {
    settings = {
      substituters = [
        "https://ollama.cachix.org"
      ];
      trusted-public-keys = [
        "ollama.cachix.org-1:YOUR_KEY_HERE"
      ];
    };
  };
}
```

### Install and Start Ollama

```bash
# Install Ollama
nix-env -iA nixos.ollama

# Start Ollama service
sudo systemctl enable ollama
sudo systemctl start ollama

# Verify installation
ollama --version
```

## 3. Set Up Python Environment

### Create Virtual Environment

```bash
# Clone the repository
git clone https://github.com/your-repo/real-time-translator
cd real-time-translator

# Create virtual environment
python -m venv venv

# Activate virtual environment
source venv/bin/activate
```

### Install Python Dependencies

```bash
# Update pip
pip install --upgrade pip

# Install required packages
pip install -r requirements.txt
```

## 4. Configure Audio Devices

### Set Up Virtual Audio Devices

Create the file `~/.config/pipewire/virtual-devices.conf`:

```bash
mkdir -p ~/.config/pipewire
cat << EOF > ~/.config/pipewire/virtual-devices.conf
context.modules = [
    {   name = module-null-sink
        args = {
            sink_name = "virtual_input"
            audio.position = [ FL FR ]
        }
    }
    {   name = module-null-sink
        args = {
            sink_name = "virtual_output"
            audio.position = [ FL FR ]
        }
    }
]
EOF
```

Restart PipeWire:

```bash
systemctl --user restart pipewire
```

### Verify Audio Setup

```bash
# Check audio devices
pactl list sinks
pactl list sources

# Verify virtual devices
pactl list short sinks | grep virtual
```

## 5. Install Kokoro TTS

### Install Dependencies for Kokoro

```bash
# Add required dependencies to configuration.nix
environment.systemPackages = with pkgs; [
  dotnet-sdk
  ffmpeg
  soundfont-fluid
];
```

Rebuild NixOS to install dependencies:
```bash
sudo nixos-rebuild switch
```

### Install and Configure Kokoro

```bash
# Create directory for Kokoro
mkdir -p ~/.local/share/kokoro

# Download and install Kokoro
git clone https://github.com/johnsmith/kokoro-tts.git
cd kokoro-tts
./install.sh

# Configure Kokoro
mkdir -p ~/.config/kokoro
cp config.example.json ~/.config/kokoro/config.json

# Test Kokoro installation
kokoro --version
kokoro --test-voice
```

## 6. Download AI Models

### Set Up Models Directory

```bash
# Create models directory
mkdir -p models
cd models
# Download Whisper model
ollama pull whisper

# Download translation model
ollama pull mistral

# Install Kokoro ONNX for TTS
git clone https://github.com/thewh1teagle/kokoro-onnx.git
cd kokoro-onnx
pip install -r requirements.txt

# Download and setup models for Kokoro
python scripts/download_models.py

# Configure Kokoro
cp config.example.yml config/tts/kokoro.yml

# Install backup TTS (optional)
git clone https://github.com/coqui-ai/TTS
cd TTS
pip install -e .
pip install -e .
```

## 6. Application Configuration

### Create Configuration Directory

```bash
# Create config directory
mkdir -p config

# Copy default configurations
cp config/default.yml.example config/default.yml
cp config/audio.yml.example config/audio.yml
cp config/models.yml.example config/models.yml
```

### Configure Application

Edit `config/default.yml`:

```yaml
app:
  name: "Real-Time Translator"
  language: "en"

audio:
  input_device: "default"
  output_device: "virtual_output"
  sample_rate: 16000
  buffer_size: 1024

translation:
  source_language: "auto"
  target_language: "en"
  model: "whisper-medium"
```

## 7. Verify Installation

### Run System Check

```bash
# Run diagnostic script
./scripts/system_check.sh

# Check audio routing
./scripts/test_audio.sh

# Verify AI models
./scripts/test_models.sh
```

### Start the Application

```bash
# Activate virtual environment if not active
source venv/bin/activate

# Run the application
./run.sh
```

## 8. Troubleshooting

### Common Issues

1. **Audio Device Problems**
   ```bash
   # Reset PipeWire
   systemctl --user restart pipewire pipewire-pulse

   # Check audio permissions
   groups | grep audio
   ```

2. **Model Loading Issues**
   ```bash
   # Check Ollama status
   systemctl status ollama

   # Verify model downloads
   ollama list
   ```

3. **Python Dependencies**
   ```bash
   # Reinstall dependencies
   pip install --force-reinstall -r requirements.txt
   ```

### Getting Help

- Check the logs: `tail -f logs/app.log`
- Run diagnostics: `./scripts/diagnostics.sh`
- Join our Discord community
- Open an issue on GitHub

## 9. Next Steps

1. Configure your preferred languages
2. Set up profiles for different use cases
3. Test with communication applications
4. Read the [Configuration Guide](CONFIGURATION.md)
5. Explore [Technical Documentation](TECHNICAL.md)