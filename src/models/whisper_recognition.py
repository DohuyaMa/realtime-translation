import numpy as np
from typing import Optional, Dict, List, Union
import json
import subprocess
import tempfile
import soundfile as sf
from loguru import logger
import threading
import queue
import time
import os
import torch

class WhisperRecognizer:
    """Speech recognition using Whisper AI through ollama."""
    
    def __init__(
        self,
        model_size: str = "medium",
        source_lang: str = "auto",
        target_lang: str = "en",
        device: str = "cpu",
        max_queue_size: int = 10,
        use_gpu: bool = True,
        confidence_threshold: float = 0.6,
        cache_dir: Optional[str] = None
    ):
        """Initialize Whisper recognizer."""
        self.model_size = model_size
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.device = "cuda" if use_gpu and self._check_gpu() else "cpu"
        self.confidence_threshold = confidence_threshold
        # Use user's home directory for cache to avoid issues with Nix environment
        self.cache_dir = cache_dir or os.path.expanduser("~/real-time-translator-cache/whisper")
        
        # Create cache directory
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Processing queue and thread
        self.processing_queue = queue.Queue(maxsize=max_queue_size)
        self.is_running = False
        self.processing_thread: Optional[threading.Thread] = None
        
        # Results callback
        self.on_result: Optional[callable] = None
        
        # Start processing thread
        self._start_processing()
        
        logger.info(f"Whisper recognizer initialized: {model_size} model, {source_lang}->{target_lang}")

    def _start_processing(self):
        """Start the processing thread."""
        self.is_running = True
        self.processing_thread = threading.Thread(target=self._process_queue)
        self.processing_thread.daemon = True
        self.processing_thread.start()

    def _process_queue(self):
        """Process audio segments from the queue."""
        while self.is_running:
            try:
                # Get audio segment from queue
                audio_data = self.processing_queue.get(timeout=1.0)
                
                # Process audio
                result = self._recognize_audio(audio_data)
                
                # Call callback with result
                if self.on_result and result:
                    self.on_result(result)
                    
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing audio segment: {e}")

    def _recognize_audio(self, audio_data: np.ndarray) -> Optional[Dict]:
        """Recognize speech in audio segment using Whisper through ollama."""
        try:
            # Save audio to temporary file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                sf.write(temp_file.name, audio_data, 16000, format='WAV')
                
                # Set up environment
                env = os.environ.copy()
                if self.device == "cuda":
                    env["CUDA_VISIBLE_DEVICES"] = "0"

                # Base command
                base_cmd = ["ollama", "run", f"whisper:{self.model_size}"]
                
                # Add options carefully, handling unsupported flags
                options = []
                
                # Add language option only if specified and not auto
                if self.source_lang and self.source_lang != "auto":
                    options.extend(["--language", self.source_lang])
                
                # Add task option based on source/target languages
                task = "translate" if self.target_lang != self.source_lang else "transcribe"
                options.extend(["--task", task])
                
                # Add other options
                options.extend([
                    "--device", self.device,
                    "--cache-dir", self.cache_dir,
                    "--threads", str(self._get_optimal_threads()),
                    temp_file.name
                ])

                # Run recognition with better error handling for unsupported flags
                start_time = time.time()
                cmd = base_cmd + [opt for opt in options if opt]
                
                try:
                    process = subprocess.Popen(
                        cmd,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    
                    stdout, stderr = process.communicate()
                    
                    if process.returncode != 0:
                        # Check if it's an unsupported flag error
                        if "unknown flag" in stderr.lower() or "unrecognized" in stderr.lower():
                            logger.warning(f"Ollama command failed due to unsupported flags: {stderr}")
                            
                            # Try with minimal options
                            minimal_cmd = base_cmd + [temp_file.name]
                            logger.info(f"Retrying with minimal command: {minimal_cmd}")
                            
                            process = subprocess.Popen(
                                minimal_cmd,
                                env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True
                            )
                            
                            stdout, stderr = process.communicate()
                            
                            if process.returncode != 0:
                                logger.error(f"Whisper recognition failed with minimal command: {stderr}")
                                return None
                        else:
                            logger.error(f"Whisper recognition failed: {stderr}")
                            return None
                except Exception as e:
                    logger.error(f"Error running Whisper recognition command: {e}")
                    return None
                
                # Parse results
                try:
                    results = json.loads(stdout)
                except json.JSONDecodeError:
                    # If not JSON, assume plain text output
                    results = {
                        'text': stdout.strip(),
                        'language': self.source_lang,
                        'segments': [{
                            'text': stdout.strip(),
                            'start': 0,
                            'end': len(audio_data) / 16000
                        }]
                    }
                
                process_time = time.time() - start_time
                logger.debug(f"Recognition completed in {process_time:.2f}s")
                
                # Validate language if not auto
                if self.source_lang != "auto":
                    confidence = self.validate_language(results['text'], self.source_lang)
                    if confidence < self.confidence_threshold:
                        logger.warning(f"Low language confidence: {confidence:.2f}")
                
                return {
                    'text': results['text'],
                    'language': results.get('language', self.source_lang),
                    'segments': results.get('segments', []),
                    'processing_time': process_time,
                    'device': self.device
                }
                
        except Exception as e:
            logger.error(f"Error in speech recognition: {e}")
            return None

    def process_audio(self, audio_data: np.ndarray):
        """Add audio segment to processing queue."""
        try:
            if self.processing_queue.full():
                logger.warning("Processing queue full, dropping audio segment")
                return
                
            self.processing_queue.put(audio_data)
            
        except Exception as e:
            logger.error(f"Error queueing audio segment: {e}")

    def set_languages(self, source_lang: str, target_lang: str = "en"):
        """Set source and target languages."""
        self.source_lang = source_lang
        self.target_lang = target_lang
        logger.info(f"Languages updated: {source_lang}->{target_lang}")

    def set_callback(self, callback: callable):
        """Set callback for recognition results."""
        self.on_result = callback

    def stop(self):
        """Stop the recognizer."""
        self.is_running = False
        if self.processing_thread:
            self.processing_thread.join()
        logger.info("Whisper recognizer stopped")

    def _check_gpu(self) -> bool:
        """Check if GPU is available and compatible."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    def _get_optimal_threads(self) -> int:
        """Get optimal number of threads based on CPU cores."""
        import multiprocessing
        return max(1, multiprocessing.cpu_count() - 1)

    def validate_language(self, text: str, lang_code: str) -> float:
        """Validate detected language against expected language."""
        try:
            # Use langdetect or similar for validation
            # This is a placeholder - implement actual language detection
            return 1.0 if lang_code == "auto" else 0.8
        except Exception as e:
            logger.error(f"Language validation error: {e}")
            return 0.0

    def optimize_model(self):
        """Apply model optimizations."""
        try:
            cmd = ["ollama", "pull", f"whisper:{self.model_size}"]
            subprocess.run(cmd, check=True)
            logger.info("Model optimization complete")
        except Exception as e:
            logger.error(f"Model optimization failed: {e}")

    def __del__(self):
        """Cleanup on deletion."""
        self.stop()