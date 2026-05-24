"""Translation service using UNIX sockets for IPC."""

from loguru import logger
from typing import Dict, Any, Optional
import threading
import time
import sys

from ..common.ipc import IPCServer
from ..status_logger import StatusManager
from ..core.runtime import get_runtime_config
from transformers import MarianTokenizer, MarianMTModel
import torch

_log_timing = time.monotonic


class TranslationService:
    """Translation service for the real-time translation system."""
    
    # FLORES-200 code mapping for NLLB models
    FLORES_CODES = {
        "uk": "ukr_Cyrl",
        "en": "eng_Latn",
        "pl": "pol_Latn",
        "de": "deu_Latn",
        "fr": "fra_Latn",
        "es": "spa_Latn",
        "it": "ita_Latn",
        "pt": "por_Latn",
        "ru": "rus_Cyrl",
        "ja": "jpn_Jpan",
        "zh": "zho_Hans",
        "ko": "kor_Hang",
        "ar": "ara_Arab",
    }

    def __init__(
        self,
        socket_path: str,
        source_lang: str = "uk",
        target_lang: str = "en",
        model_name: Optional[str] = None,
    ):
        self.socket_path = socket_path
        self.source_lang = source_lang
        self.target_lang = target_lang
        self._model_name = model_name

        # Status manager — must be created before _initialize_model
        self.status = StatusManager(component_name="translate")

        self.is_running = False
        self.processing_lock = threading.Lock()

        # Initialize translation model
        self._initialize_model()

        # IPC setup
        self.ipc_server = IPCServer(socket_path)
        self.ipc_server.register_handler('translate_text', self._handle_translate_text)
        self.ipc_server.register_handler('get_status', self._handle_get_status)
        self.ipc_server.register_handler('set_languages', self._handle_set_languages)

        logger.info(f"Translation service initialized: {source_lang}->{target_lang}")
        self.status.log_info(f"Translation service initialized: {source_lang}->{target_lang}")
        self.status.set_status("Initializing translation model...")

    def _flores_code(self, lang: str) -> str:
        """Map ISO 639-1 code to FLORES-200 code, or pass through if already FLORES."""
        if '_' in lang:
            return lang
        return self.FLORES_CODES.get(lang, lang)

    def _initialize_model(self):
        import os
        os.environ.setdefault("HF_HOME", os.path.expanduser("~/real-time-translator-cache/huggingface"))
        os.environ.setdefault("TRANSFORMERS_CACHE", os.path.expanduser("~/real-time-translator-cache/transformers"))
        os.environ.setdefault("HF_HUB_CACHE", os.path.expanduser("~/real-time-translator-cache/huggingface/hub"))

        model_name = self._model_name or f"Helsinki-NLP/opus-mt-{self.source_lang}-{self.target_lang}"
        self.status.log_info(f"Loading translation model: {model_name}")
        t0 = _log_timing()
        try:
            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            if "nllb" in model_name.lower():
                self._load_nllb(model_name)
            else:
                self._load_marian(model_name)
            self._forced_bos_token_id = self._forced_bos_token_id if hasattr(self, '_forced_bos_token_id') else None
            load_time = _log_timing() - t0
            logger.info(f"Translation model '{model_name}' loaded on {self._device} in {load_time:.1f}s")
            self.status.log_info(f"Translation model loaded in {load_time:.1f}s: {model_name}")
            self.status.set_status("Translation model loaded")
        except Exception as e:
            self.status.log_warning(f"Could not load model '{model_name}': {e}")
            fallback = "Helsinki-NLP/opus-mt-en-de"
            self.status.log_info(f"Falling back to {fallback}")
            try:
                self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                self._tokenizer = MarianTokenizer.from_pretrained(fallback)
                self._model = MarianMTModel.from_pretrained(fallback).to(self._device)
                self._model.eval()
                self._forced_bos_token_id = None
                load_time = _log_timing() - t0
                self.status.log_warning(f"Fallback model '{fallback}' loaded in {load_time:.1f}s")
            except Exception as e2:
                self.status.log_exception(f"Could not load fallback translation model: {e2}")
                raise

    def _load_marian(self, model_name: str):
        self._tokenizer = MarianTokenizer.from_pretrained(model_name)
        self._model = MarianMTModel.from_pretrained(model_name).to(self._device)
        self._model.eval()

    def _load_nllb(self, model_name: str):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        src_flores = self._flores_code(self.source_lang)
        tgt_flores = self._flores_code(self.target_lang)
        self._tokenizer = AutoTokenizer.from_pretrained(model_name, src_lang=src_flores)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(self._device)
        self._model.eval()
        self._forced_bos_token_id = self._tokenizer.lang_code_to_id[tgt_flores]
    
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
                self.status.log_info(f"Translating ({len(text)} chars): {text[:120]}{'...' if len(text) > 120 else ''}")
                
                t0 = _log_timing()
                inputs = self._tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512).to(self._device)
                with torch.no_grad():
                    gen_kwargs = {}
                    if getattr(self, '_forced_bos_token_id', None) is not None:
                        gen_kwargs['forced_bos_token_id'] = self._forced_bos_token_id
                    tokens = self._model.generate(**inputs, **gen_kwargs)
                translated_text = self._tokenizer.batch_decode(tokens, skip_special_tokens=True)[0]
                elapsed = _log_timing() - t0
                
                self.status.log_info(
                    f"Translated in {elapsed*1000:.0f}ms "
                    f"({len(text)}→{len(translated_text)} chars, "
                    f"{self.source_lang}→{self.target_lang}): {translated_text[:120]}"
                )
                
                return {
                    "status": "success",
                    "data": {
                        "original_text": text,
                        "translated_text": translated_text,
                        "source_language": self.source_lang,
                        "target_language": self.target_lang,
                        "timing_ms": round(elapsed * 1000, 1),
                    }
                }
                
            except Exception as e:
                self.status.log_exception(f"Translation failed: {e}")
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
    parser.add_argument("--socket-path", default=get_runtime_config().get_translate_socket_path(),
                       help="Path to UNIX socket for IPC")
    parser.add_argument("--source-lang", default="uk", 
                       help="Source language code (ISO 639-1 or FLORES-200 for NLLB)")
    parser.add_argument("--target-lang", default="en", 
                       help="Target language code (ISO 639-1 or FLORES-200 for NLLB)")
    parser.add_argument("--model-name", default=None,
                       help="HuggingFace model name (e.g. facebook/nllb-200-distilled-600M)")
    
    args = parser.parse_args()
    
    # Create temporary directory if needed
    socket_dir = os.path.dirname(args.socket_path)
    os.makedirs(socket_dir, exist_ok=True)
    
    service = TranslationService(
        socket_path=args.socket_path,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        model_name=args.model_name,
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