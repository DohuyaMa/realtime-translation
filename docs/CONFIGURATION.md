# Configuration Guide

## Quick Start

1. **Basic Configuration**
```yaml
# config/default.yml
app:
  name: "Real-Time Translator"
  language: "en"
  theme: "system"

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

## Detailed Configuration

### 1. Audio Configuration

#### Virtual Device Setup

```bash
# Create persistent virtual devices
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

#### Audio Quality Settings

```yaml
# config/audio.yml
audio:
  input:
    device: "default"
    sample_rate: 16000
    channels: 1
    buffer_size: 1024
    format: "float32"
    
  output:
    device: "virtual_output"
    sample_rate: 48000
    channels: 2
    buffer_size: 2048
    format: "float32"
    
  processing:
    noise_reduction: true
    echo_cancellation: true
    auto_gain: true
    volume_boost: 1.0
```

### 2. AI Model Configuration

#### Speech Recognition

```yaml
# config/models.yml
whisper:
  model_size: "medium"  # options: tiny, base, small, medium, large
  language: "auto"
  compute_type: "float16"  # options: float32, float16, int8
  beam_size: 5
  best_of: 5
```

#### Translation

```yaml
translator:
  model: "mistral"
  temperature: 0.7
  top_p: 0.95
  max_tokens: 100
  context_window: 2048
```

#### Text-to-Speech

```yaml
tts:
  engine: "coqui"
  model: "tts_models/en/ljspeech/tacotron2-DDC"
  speaker: "default"
  speed: 1.0
  pitch: 1.0
```

### 3. Profile Management

#### Creating Custom Profiles

```yaml
# config/profiles/meeting.yml
name: "Meeting Profile"
description: "Optimized for voice meetings"

audio:
  input:
    buffer_size: 512  # Lower latency
    noise_reduction: true
  output:
    buffer_size: 1024
    
models:
  whisper:
    model_size: "small"  # Faster processing
  translator:
    temperature: 0.5  # More precise translation
```

```yaml
# config/profiles/presentation.yml
name: "Presentation Profile"
description: "Balanced for presentations"

audio:
  input:
    buffer_size: 2048  # Better quality
    noise_reduction: false
  output:
    buffer_size: 4096
    
models:
  whisper:
    model_size: "medium"  # Higher accuracy
  translator:
    temperature: 0.7  # More natural translation
```

### 4. Performance Tuning

#### CPU Optimization

```yaml
# config/performance.yml
system:
  cpu:
    threads: 4
    priority: "high"
    
  memory:
    max_buffer: "2GB"
    model_cache: "4GB"
    
  gpu:
    enabled: true
    memory_limit: "2GB"
```

#### Latency Settings

```yaml
latency:
  target_ms: 200
  max_ms: 500
  buffer_adjustment: "dynamic"  # options: fixed, dynamic
  
processing:
  batch_size: 16
  parallel_jobs: 2
```

### 5. Application Settings

#### UI Configuration

```yaml
# config/interface.yml
ui:
  theme: "system"  # options: light, dark, system
  language: "en"
  font_size: 12
  
visualization:
  enabled: true
  type: "waveform"  # options: waveform, spectrum, none
  update_ms: 50
```

#### Hotkeys

```yaml
hotkeys:
  toggle_translation: "Ctrl+Shift+T"
  change_language: "Ctrl+Shift+L"
  mute: "Ctrl+Shift+M"
  show_hide: "Ctrl+Shift+H"
```

### 6. Integration Settings

#### Communication Apps

```yaml
# config/integration.yml
routing:
  teams:
    input: "virtual_output"
    output: "default"
    
  zoom:
    input: "virtual_output"
    output: "default"
```

## Advanced Configuration

### Custom Language Models

```yaml
# config/custom_models.yml
models:
  custom_whisper:
    path: "/path/to/custom/model"
    type: "whisper"
    config:
      # Model specific settings
      
  custom_tts:
    path: "/path/to/custom/voice"
    type: "coqui"
    config:
      # Voice specific settings
```

### Logging Configuration

```yaml
# config/logging.yml
logging:
  level: "INFO"
  file: "logs/app.log"
  max_size: "10MB"
  backup_count: 5
  
metrics:
  enabled: true
  collection_interval: 60
  storage_days: 30
```

## Environment Variables

```bash
# Required
export RT_CONFIG_PATH="/path/to/config"
export RT_MODELS_PATH="/path/to/models"

# Optional
export RT_LOG_LEVEL="DEBUG"
export RT_PERFORMANCE_MODE="high"
export RT_GPU_ENABLED="1"
```

## Configuration Tips

1. **Optimizing for Low Latency**
   - Reduce buffer sizes
   - Use smaller AI models
   - Enable GPU acceleration
   - Adjust batch processing

2. **Improving Translation Quality**
   - Use larger models
   - Increase context window
   - Adjust temperature settings
   - Fine-tune beam search

3. **Reducing Resource Usage**
   - Use smaller models
   - Limit buffer sizes
   - Disable unnecessary features
   - Optimize cache settings