"""Translation service using UNIX sockets for IPC."""

from loguru import logger
from typing import Dict, Any, Optional
import threading
import time
import sys

from ..common.ipc import IPCServer
from ..status_logger import StatusManager
from transformers import pipeline, AutoTokenizer, AutoModelForSeq2SeqLM


class TranslationService:
    """Translation service for the real-time translation system."""
    
    def __init__(
        self,
        socket_path: str,
        source_lang: str = "uk",
        target_lang: str = "en"
    ):
        """Initialize translation service.
        
        Args:
            socket_path: Path to the UNIX socket for IPC
            source_lang: Source language code
            target_lang: Target language code
        """
        self.socket_path = socket_path
        self.source_lang = source_lang
        self.target_lang = target_lang
        
        # Initialize translation model
        self._initialize_model()
        
        # IPC setup
        self.ipc_server = IPCServer(socket_path)
        self.ipc_server.register_handler('translate_text', self._handle_translate_text)
        self.ipc_server.register_handler('get_status', self._handle_get_status)
        self.ipc_server.register_handler('set_languages', self._handle_set_languages)
        
        # State
        self.is_running = False
        self.processing_lock = threading.Lock()
        
        # Status manager
        self.status = StatusManager()
        
        logger.info(f"Translation service initialized: {source_lang}->{target_lang}")
        self.status.log_info(f"Translation service initialized: {source_lang}->{target_lang}")
        self.status.set_status("Initializing translation model...")
    
    def _initialize_model(self):
        """Initialize the translation model."""
        try:
            # Set up HuggingFace environment variables to avoid PEP 68 issues in Nix
            import os
            os.environ.setdefault("HF_HOME", os.path.expanduser("~/real-time-translator-cache/huggingface"))
            os.environ.setdefault("TRANSFORMERS_CACHE", os.path.expanduser("~/real-time-translator-cache/transformers"))
            os.environ.setdefault("HF_HUB_CACHE", os.path.expanduser("~/real-time-translator-cache/huggingface/hub"))
            
            # Initialize translation pipeline
            # Using a more general model for demonstration
            # In practice, you might want to use language-specific models
            model_name = f"Helsinki-NLP/opus-mt-{self.source_lang}-{self.target_lang}"
            self.translator = pipeline(
                "translation",
                model=model_name,
                tokenizer=model_name
            )
            
            logger.info(f"Translation model loaded: {model_name}")
            self.status.log_info(f"Translation model loaded: {model_name}")
            self.status.set_status("Translation model loaded")
            
        except Exception as e:
            logger.warning(f"Could not load specific translation model: {e}")
            self.status.log_warning(f"Could not load specific translation model: {e}")
            logger.info("Using default translation model")
            self.status.log_info("Using default translation model")
            # Fallback to a general model
            try:
                self.translator = pipeline("translation", model="Helsinki-NLP/opus-mt-en-de")
            except Exception as e2:
                logger.error(f"Could not load fallback translation model: {e2}")
                self.status.log_error(f"Could not load fallback translation model: {e2}")
                raise
    
    def start(self):
        """Start the translation service."""
        self.ipc_server.start()
        self.is_running = True
        logger.info("Translation service started")
        self.status.set_status("Ready for translation...")
        self.status.log_info("Translation service started")
    
    def stop(self):
        """Stop the translation service."""
        self.is_running = False
        self.ipc_server.stop()
        logger.info("Translation service stopped")
    
    def _handle_translate_text(self, message: Dict) -> Dict[str, Any]:
        """Handle text translation request from IPC."""
        with self.processing_lock:
            try:
                text = message.get('data', {}).get('text', '')
                if not text:
                    return {"status": "error", "message": "No text provided"}
                
                self.status.set_status("Translating text...")
                self.status.log_info(f"Original text: {text}")
                
                # Perform translation
                result = self.translator(text)
                translated_text = result[0]['translation_text'] if isinstance(result, list) else result.get('translation_text', '')
                
                self.status.log_info(f"Translated text: {translated_text}")
                
                return {
                    "status": "success",
                    "data": {
                        "original_text": text,
                        "translated_text": translated_text,
                        "source_language": self.source_lang,
                        "target_language": self.target_lang
                    }
                }
                
            except Exception as e:
                logger.error(f"Error translating text: {e}")
                self.status.log_error(f"Error translating text: {e}")
                return {"status": "error", "message": str(e)}
    
    def _handle_get_status(self, message: Dict) -> Dict[str, Any]:
        """Handle get status request from IPC."""
        return {
            "status": "success",
            "data": {
                "running": self.is_running,
                "source_language": self.source_lang,
                "target_language": self.target_lang
            }
        }
    
    def _handle_set_languages(self, message: Dict) -> Dict[str, Any]:
        """Handle set languages request from IPC."""
        try:
            source_lang = message.get('data', {}).get('source_lang', self.source_lang)
            target_lang = message.get('data', {}).get('target_lang', self.target_lang)
            
            self.source_lang = source_lang
            self.target_lang = target_lang
            
            # Reinitialize model with new languages
            self._initialize_model()
            
            return {
                "status": "success",
                "message": f"Languages updated: {source_lang}->{target_lang}"
            }
            
        except Exception as e:
            logger.error(f"Error setting languages: {e}")
            return {"status": "error", "message": str(e)}


def main():
    """Main entry point for the translation service."""
    import argparse
    import os
    import signal
    
    parser = argparse.ArgumentParser(description="Translation Service")
    parser.add_argument("--socket-path", default="/tmp/rt-translate.sock", 
                       help="Path to UNIX socket for IPC")
    parser.add_argument("--source-lang", default="uk", 
                       help="Source language code")
    parser.add_argument("--target-lang", default="en", 
                       help="Target language code")
    
    args = parser.parse_args()
    
    # Create temporary directory if needed
    socket_dir = os.path.dirname(args.socket_path)
    os.makedirs(socket_dir, exist_ok=True)
    
    service = TranslationService(
        socket_path=args.socket_path,
        source_lang=args.source_lang,
        target_lang=args.target_lang
    )
    
    def signal_handler(signum, frame):
        logger.info("Received shutdown signal")
        service.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    service.start()
    
    try:
        # Keep the service running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        service.stop()


if __name__ == "__main__":
    main()