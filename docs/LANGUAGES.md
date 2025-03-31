# Language Configuration Guide

## Supported Languages

This system is specifically configured for:
- Source Languages: 
  * Ukrainian (uk)
  * Polish (pl)
- Target Language:
  * English (en)

## Language-Specific Configuration

### Ukrainian to English

```yaml
# config/languages/uk-en.yml
translation:
  source:
    language: "uk"
    country: "UA"
    whisper_model: "large"  # Better accuracy for Ukrainian
    
  target:
    language: "en"
    country: "US"
    
  models:
    whisper:
      language: "uk"
      task: "translate"
      beam_size: 5
      
    translator:
      model: "mistral"
      prompt_template: "Translate from Ukrainian to English: {text}"
      temperature: 0.3  # More precise translation
      
    tts:
      engine: "kokoro"  # Best quality for English synthesis
      voice: "en_US"
      speed: 1.0
      transcription:
        enabled: true
        import_path: "data/transcriptions/"  # Directory for transcription files
      
  customization:
    vocabulary:
      path: "custom/uk_vocab.txt"  # Custom Ukrainian terminology
    fine_tuning:
      enabled: true
      dataset: "data/uk_en_parallel.json"
```

### Polish to English

```yaml
# config/languages/pl-en.yml
translation:
  source:
    language: "pl"
    country: "PL"
    whisper_model: "large"  # Better accuracy for Polish
    
  target:
    language: "en"
    country: "US"
    
  models:
    whisper:
      language: "pl"
      task: "translate"
      beam_size: 5
      
    translator:
      model: "mistral"
      prompt_template: "Translate from Polish to English: {text}"
      temperature: 0.3  # More precise translation
      
    tts:
      engine: "kokoro"  # Best quality for English synthesis
      voice: "en_US"
      speed: 1.0
      transcription:
        enabled: true
        import_path: "data/transcriptions/"  # Directory for transcription files
      
  customization:
    vocabulary:
      path: "custom/pl_vocab.txt"  # Custom Polish terminology
    fine_tuning:
      enabled: true
      dataset: "data/pl_en_parallel.json"
```

## Required Project Structure

```
real-time-translator/
├── src/
│   ├── audio/             # Audio processing components
│   │   ├── __init__.py
│   │   ├── capture.py    # Audio capture
│   │   ├── routing.py    # PipeWire routing
│   │   └── process.py    # Audio processing
│   │
│   ├── models/           # AI model integration
│   │   ├── __init__.py
│   │   ├── whisper.py    # Whisper integration
│   │   ├── translator.py # Translation model
│   │   └── tts.py       # Text-to-speech
│   │
│   ├── ui/              # Qt UI components
│   │   ├── __init__.py
│   │   ├── main.py      # Main window
│   │   └── widgets/     # Custom widgets
│   │
│   └── utils/           # Shared utilities
│       ├── __init__.py
│       └── config.py    # Configuration handling
│
├── config/              # Configuration files
│   ├── default.yml     # Default settings
│   ├── audio.yml       # Audio settings
│   └── languages/      # Language-specific configs
│       ├── uk-en.yml   # Ukrainian to English
│       └── pl-en.yml   # Polish to English
│
├── custom/             # Custom resources
│   ├── uk_vocab.txt    # Ukrainian vocabulary
│   └── pl_vocab.txt    # Polish vocabulary
│
├── data/               # Training data
│   ├── uk_en_parallel.json  # Ukrainian-English pairs
│   ├── pl_en_parallel.json  # Polish-English pairs
│   └── transcriptions/      # Transcription files for better synthesis
│       ├── uk/             # Ukrainian transcriptions
│       └── pl/             # Polish transcriptions
│
├── models/            # AI model storage
│   ├── whisper/      # Whisper models
│   ├── translator/   # Translation models
│   └── tts/         # TTS models
│
├── tests/            # Test suite
│   ├── __init__.py
│   ├── test_audio.py
│   ├── test_translation.py
│   └── test_ui.py
│
├── docs/             # Documentation
├── scripts/         # Development scripts
├── requirements.txt  # Python dependencies
└── run.sh           # Launch script
```

## Model Configuration Tips

### Ukrainian Language Model

The Ukrainian language model requires specific attention to:
- Correct handling of Ukrainian characters
- Recognition of Ukrainian word boundaries
- Understanding of Ukrainian sentence structure
- Proper handling of Ukrainian-specific idioms

Configuration adjustments:
```yaml
whisper:
  model_params:
    uk:
      language_code: "uk"
      token_threshold: 0.85  # Higher threshold for Ukrainian
      silence_threshold: 0.5
      segment_length: 30     # Longer segments for better context
```

### Polish Language Model

The Polish language model needs to account for:
- Polish character set and diacritics
- Complex Polish grammar structure
- Polish word order variations
- Polish-specific expressions

Configuration adjustments:
```yaml
whisper:
  model_params:
    pl:
      language_code: "pl"
      token_threshold: 0.82  # Adjusted for Polish
      silence_threshold: 0.4
      segment_length: 25     # Balanced for Polish sentence length
```

## Fine-tuning Data

For optimal translation quality, provide domain-specific training data:

### Ukrainian-English Dataset
```json
{
  "pairs": [
    {
      "uk": "Український текст для навчання",
      "en": "Ukrainian training text"
    }
  ]
}
```

### Polish-English Dataset
```json
{
  "pairs": [
    {
      "pl": "Polski tekst treningowy",
      "en": "Polish training text"
    }
  ]
}
```

## Custom Vocabulary

Add domain-specific terms to improve translation accuracy:

### Ukrainian Vocabulary
```text
# custom/uk_vocab.txt
технічний_термін=technical_term
спеціальний_вираз=special_expression
```

### Polish Vocabulary
```text
# custom/pl_vocab.txt
termin_techniczny=technical_term
wyrażenie_specjalne=special_expression
```

## Performance Optimization

Language-specific optimizations:
```yaml
optimization:
  uk:
    beam_size: 5
    batch_size: 32
    context_length: 512
    
  pl:
    beam_size: 4
    batch_size: 32
    context_length: 448