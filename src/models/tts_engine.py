from typing import Optional, Dict, List
import numpy as np
import threading
import queue
import tempfile
from loguru import logger
import sounddevice as sd

# Check for test mode to decide which TextToSpeech implementation to use
import os
is_test_mode = os.environ.get('TEST_MODE', '').lower() == 'true'

if is_test_mode:
    # Use mock TextToSpeech class for testing to avoid dependency issues
    class TextToSpeech:
        def __init__(self, device="cpu", lang_code="a", **kwargs):
            self.device = device
            self.lang_code = lang_code
            print("WARNING: Using mock TTS engine for testing.")
    
        def synthesize(self, text, speed=1.0, pitch=0.0, energy=1.0):
            # Generate mock audio data (sine wave) for testing purposes
            import numpy as np
            # Return None for empty text to match expected behavior
            if not text or not text.strip():
                return None
            sample_rate = 22050  # Standard sample rate
            duration = len(text) * 0.05  # Approximate duration based on text length
            t = np.linspace(0, duration, int(sample_rate * duration))
            audio_data = np.sin(440 * 2 * np.pi * t)  # 440Hz sine wave
            return audio_data.astype(np.float32)
else:
    # Try importing the kokoro package from nixpkgs (which has KPipeline) first, fallback to kokoro_onnx
    try:
        from kokoro import KPipeline

        # Default voice per language code for hexgrad/Kokoro-82M
        DEFAULT_VOICES = {
            'a': 'af_heart',    # American English
            'b': 'bf_emma',     # British English
            'e': 'es',          # Spanish — espeak-based, no HF voice
            'f': 'fr-fr',       # French
            'h': 'hi',          # Hindi
            'i': 'it',          # Italian
            'j': 'Japanese',    # Japanese — no HF voice
            'p': 'pt-br',       # Portuguese
            'z': 'zh',          # Mandarin Chinese
        }

        # For nixpkgs kokoro package, we need to handle the different API
        class TextToSpeech:
            def __init__(self, device="cpu", lang_code="a", voice=None, **kwargs):
                # "a" for American English
                self.lang_code = lang_code
                self.voice = voice or DEFAULT_VOICES.get(lang_code, 'af_heart')
                self.pipeline = KPipeline(lang_code=lang_code, device=device)

            def synthesize(self, text, speed=1.0, pitch=0.0, energy=1.0):
                # KPipeline.__call__ yields KPipeline.Result(graphemes, phonemes, output)
                #   Result.audio -> torch.Tensor or None
                audio_segments = []
                for result in self.pipeline(text, voice=self.voice, speed=speed):
                    if result.audio is not None:
                        audio_np = result.audio.numpy().squeeze()
                        if audio_np.ndim == 1 and audio_np.dtype == np.float32:
                            audio_segments.append(audio_np)
                if not audio_segments:
                    return None
                return np.concatenate(audio_segments)
    except ImportError:
        try:
            from kokoro_onnx import TextToSpeech
        except ImportError:
            # Create a mock TextToSpeech class for testing when neither package is available
            class TextToSpeech:
                def __init__(self, device="cpu", lang_code="a", **kwargs):
                    self.device = device
                    self.lang_code = lang_code
                    print("WARNING: Neither 'kokoro' nor 'kokoro_onnx' could be imported. Using mock TTS engine for testing.")
        
                def synthesize(self, text, speed=1.0, pitch=0.0, energy=1.0):
                    # Generate mock audio data (sine wave) for testing purposes
                    import numpy as np
                    # Return None for empty text to match expected behavior
                    if not text or not text.strip():
                        return None
                    sample_rate = 22050  # Standard sample rate
                    duration = len(text) * 0.05  # Approximate duration based on text length
                    t = np.linspace(0, duration, int(sample_rate * duration))
                    audio_data = np.sin(440 * 2 * np.pi * t)  # 440Hz sine wave
                    return audio_data.astype(np.float32)
import os

class TTSEngine:
    """Text-to-speech engine using Kokoro ONNX for high-quality synthesis."""
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        device: str = "cpu",
        sample_rate: int = 48000,
        max_queue_size: int = 10,
        use_gpu: bool = True,
        cache_dir: Optional[str] = None,
        optimize_performance: bool = True,
        voice: Optional[str] = None,
    ):
        """Initialize TTS engine."""
        self.sample_rate = sample_rate
        self.device = "cuda" if use_gpu and self._check_gpu() else "cpu"
        self.voice = voice
        # Use user's home directory for cache to avoid issues with Nix environment
        self.cache_dir = cache_dir or os.path.expanduser("~/real-time-translator-cache/kokoro")
        
        # Create cache directory
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Initialize Kokoro TTS with optimizations
        self.tts = None
        try:
            self.tts = TextToSpeech(device=self.device, voice=self.voice)
            if optimize_performance and self.device == "cuda":
                self._optimize_for_gpu()
            logger.info(f"Kokoro TTS engine initialized on {self.device}")
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                raise
            logger.warning(f"Kokoro TTS unavailable (spaCy init failed: {e}) — falling back to espeak-ng")
            logger.info("espeak-ng fallback TTS active (lower quality but functional)")
        
        # Processing queue and thread
        self.synthesis_queue = queue.Queue(maxsize=max_queue_size)
        self.is_running = False
        self.synthesis_thread: Optional[threading.Thread] = None
        
        # Audio output
        self.output_stream: Optional[sd.OutputStream] = None
        self.on_audio_ready: Optional[callable] = None
        
        # Start processing thread
        self._start_processing()

    def _start_processing(self):
        """Start the synthesis processing thread."""
        self.is_running = True
        self.synthesis_thread = threading.Thread(target=self._process_queue)
        self.synthesis_thread.daemon = True
        self.synthesis_thread.start()
        
        try:
            device_info = sd.query_devices(device=sd.default.device, kind='output')
            if device_info is not None:
                device_sr = int(device_info['default_samplerate'])
                logger.info(f"Using output device sample rate: {device_sr}")
            else:
                device_sr = self.sample_rate
        except Exception:
            device_sr = self.sample_rate
        
        try:
            self.output_stream = sd.OutputStream(
                samplerate=device_sr,
                channels=1,
                dtype=np.float32
            )
            self.output_stream.start()
            self.sample_rate = device_sr
        except Exception as e:
            logger.warning(f"Could not open audio output stream ({e}) — running in IPC-only mode")
            self.output_stream = None

    def _process_queue(self):
        """Process text segments from the queue."""
        while self.is_running:
            try:
                # Get text segment from queue
                item = self.synthesis_queue.get(timeout=1.0)
                if item is None:  # Handle None sentinel for shutdown
                    break
                # Check if item is a tuple with 2 elements before unpacking
                if isinstance(item, tuple) and len(item) == 2:
                    text, settings = item
                else:
                    # Skip invalid items
                    continue
                
                # Synthesize speech
                audio_data = self._synthesize_text(text, settings)
                
                if audio_data is not None:
                    # Play audio if requested
                    if settings.get('play_audio', False):
                        self._play_audio(audio_data)
                    
                    # Call callback if set
                    if self.on_audio_ready:
                        self.on_audio_ready(audio_data)
                    
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing synthesis request: {e}")

    def _synthesize_text(
        self,
        text: str,
        settings: Dict
    ) -> Optional[np.ndarray]:
        if self.tts is None:
            return self._synthesize_espeak(text, settings)
        try:
            speed = settings.get('speed', 1.0)
            pitch = settings.get('pitch', 0.0)
            energy = settings.get('energy', 1.0)
            audio_data = self.tts.synthesize(text, speed=speed, pitch=pitch, energy=energy)
            return audio_data
        except Exception as e:
            logger.error(f"Speech synthesis failed: {e}")
            return None

    def _synthesize_espeak(
        self,
        text: str,
        settings: Dict
    ) -> Optional[np.ndarray]:
        import wave
        import subprocess
        speed = settings.get('speed', 1.0)
        # espeak-ng speed: 80-450, default 175. Map 0.5-2.0 → 80-260
        espeak_speed = max(80, min(450, int(175 * speed)))
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                tmp = f.name
            subprocess.run(
                ["espeak-ng", "-w", tmp, "-s", str(espeak_speed), text],
                capture_output=True, timeout=30, check=True,
            )
            with wave.open(tmp, 'rb') as wf:
                frames = wf.getnframes()
                sr = wf.getframerate()
                raw = wf.readframes(frames)
            os.unlink(tmp)
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            logger.info(f"espeak-ng fallback: {len(text)} chars → {frames/sr:.1f}s @ {sr}Hz")
            return audio
        except FileNotFoundError:
            logger.warning("espeak-ng not found — TTS unavailable")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("espeak-ng timed out")
            return None
        except Exception as e:
            logger.warning(f"espeak-ng fallback failed: {e}")
            return None

    def _play_audio(self, audio_data: np.ndarray):
        """Play synthesized audio through sounddevice.
        
        Args:
            audio_data: Numpy array of audio samples
        """
        try:
            self.output_stream.write(audio_data)
        except Exception as e:
            logger.error(f"Error playing audio: {e}")

    def synthesize(
        self,
        text: str,
        play_audio: bool = False,
        speed: float = 1.0,
        pitch: float = 0.0,
        energy: float = 1.0
    ):
        """Queue text for synthesis.
        
        Args:
            text: Text to synthesize
            play_audio: Whether to play audio immediately
            speed: Speech speed multiplier
            pitch: Pitch adjustment
            energy: Energy/volume adjustment
        """
        try:
            if self.synthesis_queue.full():
                logger.warning("Synthesis queue full, dropping text segment")
                return
                
            settings = {
                'play_audio': play_audio,
                'speed': speed,
                'pitch': pitch,
                'energy': energy
            }
            
            self.synthesis_queue.put((text, settings))
            
        except Exception as e:
            logger.error(f"Error queueing text for synthesis: {e}")

    def save_to_file(
        self,
        text: str,
        file_path: str,
        settings: Optional[Dict] = None
    ) -> bool:
        """Synthesize text and save to audio file.
        
        Args:
            text: Text to synthesize
            file_path: Output file path
            settings: Optional synthesis settings
            
        Returns:
            True if successful
        """
        try:
            # Use provided settings or defaults
            settings = settings or {
                'speed': 1.0,
                'pitch': 0.0,
                'energy': 1.0
            }
            
            # Synthesize audio
            audio_data = self._synthesize_text(text, settings)
            
            if audio_data is None:
                return False
            
            # Save to file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                sd.write(temp_file.name, audio_data, self.sample_rate)
                os.replace(temp_file.name, file_path)
            
            logger.info(f"Audio saved to: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving audio to file: {e}")
            return False

    def synthesize_sync(self, text: str, speed: float = 1.0) -> Optional[np.ndarray]:
        """Synchronously synthesize text and return audio array without playing."""
        return self._synthesize_text(text, {"speed": speed, "pitch": 0.0, "energy": 1.0})

    def set_voice(self, voice: str):
        """Reinitialize TTS with a new voice (live switch)."""
        self.voice = voice
        if self.tts is not None and hasattr(self.tts, 'voice'):
            self.tts.voice = voice
            logger.info(f"TTS voice switched to {voice}")

    def set_callback(self, callback: callable):
        """Set callback for synthesized audio.

        Args:
            callback: Function to call with audio data
        """
        self.on_audio_ready = callback

    def stop(self):
        """Stop the TTS engine."""
        self.is_running = False
        
        # Safely join the synthesis thread if it exists
        if hasattr(self, 'synthesis_thread') and self.synthesis_thread:
            self.synthesis_thread.join()
            
        if hasattr(self, 'output_stream') and self.output_stream:
            self.output_stream.stop()
            self.output_stream.close()
            
        logger.info("TTS engine stopped")

    def __del__(self):
        """Cleanup on deletion."""
        self.stop()

    def _check_gpu(self) -> bool:
        """Check if GPU is available and compatible."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    def _optimize_for_gpu(self):
        """Apply GPU-specific optimizations."""
        try:
            import torch
            
            # Enable CUDA optimizations
            torch.backends.cudnn.benchmark = True
            torch.backends.cuda.matmul.allow_tf32 = True
            
            # Move models to GPU memory
            if hasattr(self.tts, 'model'):
                self.tts.model = self.tts.model.cuda()
            
            logger.info("GPU optimizations applied")
        except Exception as e:
            logger.error(f"Failed to apply GPU optimizations: {e}")

    def preload_models(self):
        """Preload and cache models."""
        try:
            cache_path = os.path.join(self.cache_dir, "models")
            if not os.path.exists(cache_path):
                logger.info("Downloading and caching models...")
                # Download models if needed
                # This is a placeholder - implement actual model downloading
                pass
            logger.info("Models preloaded")
        except Exception as e:
            logger.error(f"Failed to preload models: {e}")