# User Interface Guide

## Main Window Layout

### 1. Language Settings Panel
```
┌─ Language Settings ─────────────┐
│ Source: [Ukrainian ▼]          │
│        [Polish    ▼]          │
│ Target: English               │
└─────────────────────────────────┘
```
- Source language dropdown
- Quick language switch button
- Current language indicator

### 2. Audio Controls
```
┌─ Audio Settings ──────────────┐
│ Input:  [Device List ▼]      │
│ Output: [Device List ▼]      │
│                              │
│ Levels: [██████----] Input   │
│         [████------] Output  │
└──────────────────────────────┘
```
- Input/Output device selection
- Real-time level meters
- Mute/Unmute buttons

### 3. Translation Controls
```
┌─ Translation ────────────────┐
│ [Start Translation]         │
│ Mode: [Real-time ▼]        │
│ [✓] Auto-detect language   │
└─────────────────────────────┘
```
- Start/Stop translation
- Mode selection (real-time/batch)
- Auto-detection toggle

### 4. Application Integration
```
┌─ Integration ───────────────┐
│ Route to: [Teams    ▼]     │
│           [Zoom     ▼]     │
│           [Browser  ▼]     │
│ [Test Connection]          │
└─────────────────────────────┘
```
- Application selection
- Audio routing setup
- Connection test

### 5. Settings Menu
```
Settings
├── Audio
│   ├── Buffer Size
│   ├── Sample Rate
│   └── Latency Options
├── Translation
│   ├── Model Selection
│   ├── Quality/Speed Balance
│   └── Language Pairs
├── Voice
│   ├── Voice Selection
│   ├── Speed Control
│   └── Pitch Adjustment
└── System
    ├── GPU Usage
    ├── Log Level
    └── Cache Settings
```

### 6. Log Window
```
┌─ Logs ────────────────────────┐
│ [Clear] [Save] [Filter ▼]    │
│                              │
│ 10:54:35 Translation started │
│ 10:54:36 Audio detected      │
│ 10:54:37 Processing speech   │
└──────────────────────────────┘
```
- Log level selection
- Search/filter options
- Export logs feature

## Keyboard Shortcuts

```
Global Shortcuts:
Ctrl+Shift+T  - Start/Stop Translation
Ctrl+Shift+M  - Mute/Unmute
Ctrl+Shift+L  - Switch Source Language
Ctrl+`        - Show/Hide Log Window
```

## Configuration Files

### 1. Main Configuration (config/main.yml)
```yaml
ui:
  theme: system    # light, dark, system
  always_on_top: false
  minimize_to_tray: true
  show_logs: true

audio:
  buffer_size: 1024
  sample_rate: 16000
  use_virtual_devices: true

language:
  default_source: auto
  target: en
  pairs:
    - source: uk
      target: en
    - source: pl
      target: en
```

### 2. Integration Settings (config/integration.yml)
```yaml
teams:
  input_device: virtual_output
  enable_auto_routing: true

zoom:
  input_device: virtual_output
  enable_auto_routing: true

browser:
  input_device: virtual_output
  enable_auto_routing: true
```

## Log Configuration

### Log Window Settings (config/logging.yml)
```yaml
window:
  show_on_startup: false
  position: bottom
  max_lines: 1000

format:
  time: true
  level: true
  component: true

levels:
  - DEBUG
  - INFO
  - WARNING
  - ERROR

filters:
  default:
    - translation
    - audio
    - system
```

## Testing Interface

```
┌─ Test Panel ──────────────────┐
│ [Run All Tests]              │
│ [Unit Tests]                 │
│ [Integration Tests]          │
│                              │
│ Progress: [█████-----] 50%   │
│                              │
│ Details:                     │
│ ✓ Audio routing             │
│ ✓ Speech recognition        │
│ ✗ TTS synthesis            │
└──────────────────────────────┘
```

- Test selection
- Real-time progress
- Detailed results
- Export test report