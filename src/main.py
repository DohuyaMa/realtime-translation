#!/usr/bin/env python3

import sys
import os
import argparse
from loguru import logger
import yaml
from PySide6.QtWidgets import QApplication

# Import the new architecture components
from .controller.translator_controller import ConcreteTranslatorController
from .adapters import DirectAdapter
from .ui.widgets.main_window import MainWindow
from .ui.controller.ui_controller import UIController


def create_adapter(mode: str = "direct", use_wyoming: bool = False, wyoming_host: str = "localhost", wyoming_port: int = 10300, **kwargs):
    """Create the appropriate adapter based on the mode.
    
    Args:
        mode: Adapter mode (direct or ipc)
        use_wyoming: Whether to use Wyoming whisper service
        wyoming_host: Wyoming service host
        wyoming_port: Wyoming service port
        **kwargs: Additional arguments to pass to the adapter
        
    Returns:
        An adapter instance
    """
    if mode == "direct":
        return DirectAdapter(
            use_wyoming=use_wyoming,
            wyoming_host=wyoming_host,
            wyoming_port=wyoming_port,
            **kwargs
        )
    raise ValueError(f"Unknown adapter mode: {mode}")

def setup_logging(log_level: str = "INFO"):
    """Set up logging configuration.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    logger.remove()  # Remove default handler
    
    # Add console handler
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level
    )
    
    # Add file handler
    log_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "real-time-translator", "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    logger.add(
        os.path.join(log_dir, "app.log"),
        rotation="10 MB",
        retention="1 week",
        level=log_level
    )


def load_config() -> dict:
    """Load application configuration.
    
    Returns:
        Dictionary containing configuration
    """
    config = {
        'audio': {
            'sample_rate': 16000,
            'chunk_size': 1024,
            'use_virtual_devices': True
        },
        'translation': {
            'source_lang': 'auto',
            'target_lang': 'en',
            'whisper_model': 'medium'
        },
        'ui': {
            'minimize_to_tray': True,
            'start_minimized': False
        }
    }
    
    # Load user config if exists
    config_dir = os.path.join(os.path.expanduser("~"), ".config", "real-time-translator")
    config_file = os.path.join(config_dir, "config.yml")
    
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                user_config = yaml.safe_load(f)
                if user_config:
                    # Update config with user settings
                    for section, values in user_config.items():
                        if section in config:
                            config[section].update(values)
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
    
    return config


def parse_args():
    """Parse command line arguments.
    
    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        description="Real-time speech translation system"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    
    parser.add_argument(
        "--minimize",
        action="store_true",
        help="Start minimized to system tray"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        help="Path to custom config file"
    )
    
    parser.add_argument(
        "--mode",
        type=str,
        default="direct",
        choices=["direct", "ipc"],
        help="Adapter mode (direct or ipc)"
    )
    
    parser.add_argument(
        "--use-wyoming",
        action="store_true",
        help="Use Wyoming whisper service instead of local model"
    )
    
    parser.add_argument(
        "--wyoming-host",
        type=str,
        default="localhost",
        help="Wyoming service host"
    )
    
    parser.add_argument(
        "--wyoming-port",
        type=int,
        default=10300,
        help="Wyoming service port"
    )
    
    return parser.parse_args()


def main():
    """Main application entry point."""
    # Parse command line arguments
    args = parse_args()
    
    # Set up logging
    setup_logging("DEBUG" if args.debug else "INFO")
    logger.info("Starting Real-Time Translator")
    
    try:
        # Load configuration
        config = load_config()
        
        # Override config with command line args
        if args.minimize:
            config['ui']['start_minimized'] = True
        
        # Create QApplication before creating the window
        app = QApplication(sys.argv)
        
        # Create and show main window with controller
        adapter = create_adapter(
            args.mode,
            use_wyoming=args.use_wyoming,
            wyoming_host=args.wyoming_host,
            wyoming_port=args.wyoming_port
        )
        backend_controller = ConcreteTranslatorController(adapter)
        ui_controller = UIController(backend_controller)
        window = MainWindow(controller=ui_controller)
        
        if args.config:
            try:
                with open(args.config, 'r') as f:
                    custom_config = yaml.safe_load(f)
                    if custom_config:
                        for section, values in custom_config.items():
                            if section in config:
                                config[section].update(values)
            except Exception as e:
                logger.error(f"Error loading custom config file: {e}")
        
        if not config['ui']['start_minimized']:
            window.show()
        
        # Start application event loop
        sys.exit(app.exec())
        
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()