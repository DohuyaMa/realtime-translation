#!/usr/bin/env python3

import os
import sys
import subprocess
import shutil
from pathlib import Path
import yaml
from loguru import logger

def setup_logging():
    """Configure logging."""
    logger.remove()
    logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | {level} | {message}")
    logger.add("setup.log", rotation="1 MB")

def check_requirements():
    """Check system requirements."""
    try:
        import torch
        logger.info(f"PyTorch version: {torch.__version__}")
        if torch.cuda.is_available():
            logger.info(f"CUDA available: {torch.cuda.get_device_name(0)}")
        else:
            logger.warning("CUDA not available, will use CPU")
    except ImportError:
        logger.error("PyTorch not found, please install it first")
        sys.exit(1)

def clone_kokoro():
    """Clone Kokoro repository."""
    kokoro_dir = Path("kokoro-tts")
    
    if kokoro_dir.exists():
        logger.info("Kokoro directory already exists, updating...")
        cmd = ["git", "submodule", "update", "--remote", "kokoro-tts"]
    else:
        logger.info("Cloning Kokoro repository...")
        cmd = ["git", "submodule", "add", "https://github.com/thewh1teagle/kokoro-onnx.git", "kokoro-tts"]
    
    try:
        subprocess.run(cmd, check=True)
        logger.success("Kokoro repository setup completed")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to setup Kokoro repository: {e}")
        sys.exit(1)

def create_config():
    """Create Kokoro configuration."""
    config_dir = Path("config/tts")
    config_dir.mkdir(parents=True, exist_ok=True)
    
    config = {
        'kokoro': {
            'model': {
                'path': str(Path('kokoro-tts/models').absolute()),
                'type': 'en_US',
                'device': 'cuda' if torch.cuda.is_available() else 'cpu'
            },
            'voice': {
                'speed': 1.0,
                'pitch': 0.0,
                'energy': 1.0
            },
            'optimization': {
                'batch_size': 32,
                'num_threads': 4,
                'use_cache': True,
                'cache_dir': str(Path.home() / '.cache' / 'kokoro')
            }
        }
    }
    
    config_file = config_dir / 'kokoro.yml'
    with open(config_file, 'w') as f:
        yaml.safe_dump(config, f, default_flow_style=False)
    
    logger.info(f"Created configuration file: {config_file}")

def setup_cache():
    """Set up cache directory."""
    # Use user's home directory for cache to avoid issues with Nix environment
    cache_dir = Path.home() / 'real-time-translator-cache' / 'kokoro'
    cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Cache directory created: {cache_dir}")

def download_models():
    """Download required models."""
    try:
        # Check if we're in a Nix environment
        if os.environ.get('NIX_PYTHON') or os.environ.get('NIX_ENV') or os.path.exists('/etc/nixos') or os.environ.get('NIX_STORE'):
            logger.info("In Nix environment, skipping model download (should be provided by Nix package)")
            return
        else:
            sys.path.append('kokoro-tts')
            from kokoro import download_models
            
            models_dir = Path('kokoro-tts/models')
            models_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info("Downloading models...")
            download_models(str(models_dir))
            logger.success("Models downloaded successfully")
    except Exception as e:
        logger.error(f"Failed to download models: {e}")
        # In Nix environment, this is expected, so we don't exit
        if not (os.environ.get('NIX_PYTHON') or os.environ.get('NIX_ENV') or os.path.exists('/etc/nixos') or os.environ.get('NIX_STORE')):
            sys.exit(1)

def setup_development():
    """Set up development environment."""
    # Check if we're in a Nix environment
    if os.environ.get('NIX_PYTHON') or os.environ.get('NIX_ENV') or os.path.exists('/etc/nixos') or os.environ.get('NIX_STORE'):
        logger.info("In Nix environment, skipping pip install (packages provided by Nix)")
        logger.success("Development environment setup completed (using Nix packages)")
        return
        
    try:
        # Install Kokoro requirements
        subprocess.run(
            ["pip", "install", "-r", "kokoro-tts/requirements.txt"],
            check=True
        )
        
        # Install package in development mode
        subprocess.run(
            ["pip", "install", "-e", "kokoro-tts"],
            check=True
        )
        
        logger.success("Development environment setup completed")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to setup development environment: {e}")
        sys.exit(1)

def main():
    """Main setup process."""
    setup_logging()
    logger.info("Starting Kokoro TTS setup...")
    
    try:
        check_requirements()
        clone_kokoro()
        create_config()
        setup_cache()
        download_models()
        setup_development()
        
        logger.success("Kokoro TTS setup completed successfully!")
    except Exception as e:
        logger.error(f"Setup failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()