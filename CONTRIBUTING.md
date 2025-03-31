# Contributing to Real-Time Translation System

Thank you for your interest in contributing to the Real-Time Translation System! This document provides guidelines and instructions for contributing to the project.

## Code of Conduct

Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md) to maintain a positive and inclusive environment.

## Development Setup

### 1. Fork and Clone

```bash
# Fork the repository on GitHub, then:
git clone https://github.com/your-username/real-time-translator
cd real-time-translator
```

### 2. Set Up Development Environment

Follow the [Installation Guide](docs/INSTALLATION.md) for basic setup, then:

```bash
# Create development branch
git checkout -b feature/your-feature-name

# Install development dependencies
pip install -r requirements-dev.txt
```

## Project Structure

```
real-time-translator/
├── src/
│   ├── audio/          # Audio processing components
│   ├── models/         # AI model integration
│   ├── ui/            # Qt UI components
│   └── utils/         # Shared utilities
├── tests/             # Test suite
├── docs/              # Documentation
├── config/            # Configuration files
└── scripts/          # Development scripts
```

## Development Workflow

### 1. Coding Standards

- Follow PEP 8 style guide
- Use type hints
- Write docstrings for classes and functions
- Keep functions focused and small
- Use meaningful variable names

### 2. Making Changes

1. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes following our coding standards

3. Write tests for new features

4. Update documentation as needed

5. Run tests locally:
   ```bash
   pytest tests/
   ```

### 3. Commit Guidelines

Use semantic commit messages:

```
type(scope): Brief description

Detailed description if needed

Fixes #123
```

Types:
- feat: New feature
- fix: Bug fix
- docs: Documentation changes
- style: Formatting, missing semi colons, etc
- refactor: Code restructuring
- test: Adding missing tests
- chore: Maintenance tasks

### 4. Testing

#### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_audio.py

# Run with coverage
pytest --cov=src tests/
```

#### Writing Tests

Example test structure:

```python
# tests/test_audio.py
import pytest
from src.audio import AudioManager

def test_audio_device_creation():
    manager = AudioManager()
    device = manager.create_virtual_device("test")
    assert device is not None
    assert device.name == "test"

@pytest.mark.parametrize("sample_rate", [16000, 44100, 48000])
def test_audio_sample_rates(sample_rate):
    manager = AudioManager()
    assert manager.set_sample_rate(sample_rate)
```

### 5. Documentation

- Update documentation for new features
- Include docstrings for new classes/functions
- Update configuration examples
- Add comments for complex logic

## Pull Request Process

1. **Before Submitting**
   - Run all tests
   - Update documentation
   - Format code with `black`
   - Run linting with `flake8`
   - Update requirements if needed

2. **Submit PR**
   - Use clear PR title
   - Reference related issues
   - Describe changes in detail
   - Include testing instructions

3. **Review Process**
   - Address review comments
   - Keep PR focused and small
   - Be responsive to feedback

4. **After Merge**
   - Delete your feature branch
   - Update your local main branch

## Development Tools

### Code Quality

```bash
# Format code
black src/ tests/

# Check types
mypy src/

# Lint code
flake8 src/ tests/
```

### Git Hooks

Install pre-commit hooks:

```bash
pip install pre-commit
pre-commit install
```

## Specialized Development

### 1. Audio Processing

- Test with various audio devices
- Consider latency impacts
- Handle device disconnections
- Document buffer configurations

### 2. AI Model Integration

- Test with different model sizes
- Document memory requirements
- Consider optimization options
- Handle failure gracefully

### 3. UI Development

- Follow Qt best practices
- Test with different themes
- Ensure accessibility
- Handle window states

## Release Process

1. Version Bump
   ```bash
   ./scripts/bump_version.sh
   ```

2. Update Changelog
   - Add new version section
   - Document all changes
   - Credit contributors

3. Create Release
   - Tag version in git
   - Create GitHub release
   - Update documentation

## Getting Help

- Join our Discord channel
- Check existing issues
- Read the [Technical Documentation](docs/TECHNICAL.md)
- Contact maintainers

## Recognition

Contributors will be:
- Added to CONTRIBUTORS.md
- Mentioned in release notes
- Credited in documentation

Thank you for contributing to making real-time translation accessible to everyone!