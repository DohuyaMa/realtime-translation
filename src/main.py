#!/usr/bin/env python3

import sys
import os
import argparse
from loguru import logger
import yaml
from PySide6.QtWidgets import QApplication

from .controller.translator_controller import ConcreteTranslatorController
from .adapters import DirectAdapter
from .ui.widgets.main_window import MainWindow
from .ui.controller.ui_controller import UIController


def create_adapter(mode: str = "direct", **kwargs):
    """Create the backend adapter (only 'direct' exists)."""
    if mode != "direct":
        raise ValueError(f"Unsupported adapter mode: {mode}")
    from .core.config import get_config_manager
    from .core.runtime import get_runtime_config

    cfg = get_config_manager()
    return DirectAdapter(
        source_lang=cfg.get("translation.source_lang", "auto"),
        target_lang=cfg.get("translation.target_lang", "en"),
        tts_voice=cfg.get("speech.tts_voice"),
        tts_speed=cfg.get("speech.tts_speed"),
        settings=get_runtime_config(),
        **kwargs,
    )


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
    from .core.config import get_config_manager
    config_manager = get_config_manager()
    return config_manager.get_all()


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
        choices=["direct"],
        help="Adapter mode (only direct)"
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
        adapter = create_adapter(args.mode)
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