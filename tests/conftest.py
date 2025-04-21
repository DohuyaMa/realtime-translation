import pytest
import os
import sys
import logging

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Configure logging for tests
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

@pytest.fixture(autouse=True)
def setup_test_env():
    """Set up test environment before each test."""
    # Create necessary directories
    os.makedirs('tests/test_data', exist_ok=True)
    
    # Set environment variables for testing
    os.environ['PYTHONPATH'] = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    os.environ['TEST_MODE'] = 'true'
    
    yield
    
    # Cleanup after tests
    import shutil
    if os.path.exists('tests/test_data'):
        shutil.rmtree('tests/test_data')

@pytest.fixture
def audio_config():
    """Provide standard audio configuration."""
    return {
        'sample_rate': 16000,
        'channels': 1,
        'chunk_size': 1024,
        'format_type': 'float32'
    }

@pytest.fixture
def model_config():
    """Provide standard model configuration."""
    return {
        'whisper_model': 'small',
        'device': 'cpu',
        'use_gpu': False,
        'cache_dir': 'tests/test_data/cache'
    }

def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers",
        "gpu: mark test as requiring GPU"
    )

def pytest_collection_modifyitems(config, items):
    """Modify test collection."""
    # Skip GPU tests if no GPU available
    try:
        import torch
        if not torch.cuda.is_available():
            skip_gpu = pytest.mark.skip(reason="GPU not available")
            for item in items:
                if "gpu" in item.keywords:
                    item.add_marker(skip_gpu)
    except ImportError:
        skip_gpu = pytest.mark.skip(reason="torch not installed")
        for item in items:
            if "gpu" in item.keywords:
                item.add_marker(skip_gpu)