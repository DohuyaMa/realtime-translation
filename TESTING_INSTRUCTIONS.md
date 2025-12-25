# Testing Instructions

## Running Tests with Output to Markdown File

To run the tests and output results to `tests/last-full-test.md` as requested:

### Script to Run Tests

```bash
#!/bin/bash

# Script to run tests and output results to tests/last-full-test.md
# This script clears the output file first and then runs the specified tests

# Clear the output file
OUTPUT_FILE="tests/last-full-test.md"
> "$OUTPUT_FILE"

echo "Running tests and outputting to $OUTPUT_FILE..."

# Run the tests with nix develop and redirect output to the file
nix develop --command python -m pytest \
    tests/test_ai_models.py \
    tests/test_audio.py \
    tests/test_direct_adapter.py \
    tests/test_ipc_communication.py \
    tests/test_tts_engine.py \
    -v \
    --tb=short \
    2>&1 | tee -a "$OUTPUT_FILE"

echo "Tests completed. Results saved to $OUTPUT_FILE"
```

### Manual Command

Alternatively, you can run the command directly:

```bash
# Clear the file first
> tests/last-full-test.md

# Run tests and append output
nix develop --command python -m pytest tests/test_ai_models.py tests/test_audio.py tests/test_direct_adapter.py tests/test_ipc_communication.py tests/test_tts_engine.py -v 2>&1 | tee -a tests/last-full-test.md
```

## Model Configuration

To use smaller, faster models as specified:

1. The tests currently use 'small' model for WhisperRecognizer which is already one of the smaller models
2. The actual Whisper service in src/whisper/whisper_service.py defaults to 'medium' model, change this to 'tiny' or 'base' for faster execution
3. For testing purposes, you can modify the whisper_recognizer fixture in test_ai_models.py to use 'tiny' model instead of 'small'

Example modification for faster testing:

```python
@pytest.fixture
def whisper_recognizer():
    """Create Whisper recognizer instance with tiny model for faster testing."""
    return WhisperRecognizer(
        model_size="tiny",  # Use tiny instead of small for faster tests
        use_gpu=False  # Use CPU for testing
    )
```

## Running the Specific Tests

To run the specific tests mentioned in the task:

```bash
# Clear the output file first
> tests/last-full-test.md

# Run the specific tests with output to the markdown file
nix develop --command python -m pytest \
    tests/test_ai_models.py \
    tests/test_audio.py \
    tests/test_direct_adapter.py \
    tests/test_ipc_communication.py \
    tests/test_tts_engine.py \
    -v \
    2>&1 | tee -a tests/last-full-test.md
```

Note: Due to the SpaCy issue mentioned above, some tests may fail. To run tests with smaller models and avoid some issues, you may want to temporarily modify the test fixtures to use 'tiny' models instead of 'small'.

## Verifying Test Output

After running the tests, verify the output was written to `tests/last-full-test.md`:

```bash
# Check if the file has been updated
ls -la tests/last-full-test.md

# View the end of the file to see the latest test results
tail -200 tests/last-full-test.md
```

## Addressing the SpaCy Issue

The current test output shows that SpaCy is trying to install models via pip, which fails in the Nix environment. This needs to be addressed by either:

1. Pre-installing required SpaCy models in the Nix environment
2. Configuring SpaCy to work without dynamic downloads
3. Mocking the SpaCy functionality in tests

The error occurs because kokoro (which uses SpaCy for grapheme-to-phoneme conversion) is trying to download the en_core_web_sm model at runtime. In the Nix environment, pip is not available, causing the failure.

## Caching Recommendations

Based on `context/whisper-test-update.md`, ensure these environment variables are set to avoid writing to `/nix/store`:

```bash
export HF_HOME="$HOME/.cache/huggingface"
export TRANSFORMERS_CACHE="$HOME/.cache/transformers"
export HF_HUB_CACHE="$HF_HOME/hub"
mkdir -p "$HF_HOME/hub" "$TRANSFORMERS_CACHE"
```

These should be set in your devShell's shellHook in flake.nix to ensure models and transformers cache are stored in user space rather than the Nix store.

## PipeWire Virtual Sinks

For testing, ensure virtual sinks are created:

```bash
if ! pactl list sinks short | grep -q rt_virtual_input; then
  pactl load-module module-null-sink sink_name=rt_virtual_input sink_properties=device.description="RT-Virtual-Input" || true
fi

if ! pactl list sinks short | grep -q rt_virtual_output; then
  pactl load-module module-null-sink sink_name=rt_virtual_output sink_properties=device.description="RT-Virtual-Output" || true
fi
```

```bash
export HF_HOME="$HOME/.cache/huggingface"
export TRANSFORMERS_CACHE="$HOME/.cache/transformers"
export HF_HUB_CACHE="$HF_HOME/hub"
mkdir -p "$HF_HOME/hub" "$TRANSFORMERS_CACHE"
```

## Addressing the SpaCy Issue

The current test output shows that SpaCy is trying to install models via pip, which fails in the Nix environment. This needs to be addressed by either:

1. Pre-installing required SpaCy models in the Nix environment
2. Configuring SpaCy to work without dynamic downloads
3. Mocking the SpaCy functionality in tests
