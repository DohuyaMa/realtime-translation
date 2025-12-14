"""Pipeline orchestrator for the real-time translation system."""

import subprocess
import threading
import time
import signal
import sys
import os
from loguru import logger
from typing import List, Optional

from ..common.ipc import IPCClient


class PipelineOrchestrator:
    """Orchestrates the real-time translation pipeline."""
    
    def __init__(self):
        """Initialize the pipeline orchestrator."""
        # Define socket paths for each service
        self.socket_paths = {
            'capture': '/tmp/rt-capture.sock',
            'whisper': '/tmp/rt-whisper.sock', 
            'translate': '/tmp/rt-translate.sock',
            'tts': '/tmp/rt-tts.sock',
            'playback': '/tmp/rt-playback.sock'
        }
        
        # Service processes
        self.processes = {}
        
        # IPC clients
        self.clients = {}
        
        # State
        self.is_running = False
        
        logger.info("Pipeline orchestrator initialized")
    
    def start_services(self):
        """Start all pipeline services."""
        logger.info("Starting pipeline services...")
        
        # Start each service as a subprocess
        service_configs = [
            {
                'name': 'capture',
                'module': 'src.capture.capture_service',
                'args': ['--socket-path', self.socket_paths['capture']]
            },
            {
                'name': 'whisper', 
                'module': 'src.whisper.whisper_service',
                'args': ['--socket-path', self.socket_paths['whisper']]
            },
            {
                'name': 'translate',
                'module': 'src.translate.translate_service', 
                'args': ['--socket-path', self.socket_paths['translate']]
            },
            {
                'name': 'tts',
                'module': 'src.tts.tts_service',
                'args': ['--socket-path', self.socket_paths['tts']]
            },
            {
                'name': 'playback',
                'module': 'src.playback.playback_service',
                'args': ['--socket-path', self.socket_paths['playback']]
            }
        ]
        
        for config in service_configs:
            try:
                # Use python to run the module
                cmd = [sys.executable, '-m', config['module']] + config['args']
                process = subprocess.Popen(cmd)
                self.processes[config['name']] = process
                logger.info(f"Started {config['name']} service")
            except Exception as e:
                logger.error(f"Failed to start {config['name']} service: {e}")
        
        # Wait a bit for services to start
        time.sleep(2)
        
        # Initialize IPC clients
        for name, path in self.socket_paths.items():
            try:
                client = IPCClient(path)
                client.connect()
                self.clients[name] = client
                logger.info(f"Connected to {name} service")
            except Exception as e:
                logger.error(f"Failed to connect to {name} service: {e}")
        
        self.is_running = True
        logger.info("All pipeline services started")
    
    def stop_services(self):
        """Stop all pipeline services."""
        logger.info("Stopping pipeline services...")
        
        # Disconnect IPC clients
        for name, client in self.clients.items():
            try:
                client.disconnect()
            except:
                pass
        
        self.clients.clear()
        
        # Terminate processes
        for name, process in self.processes.items():
            try:
                process.terminate()
                process.wait(timeout=5)  # Wait up to 5 seconds
            except subprocess.TimeoutExpired:
                process.kill()  # Force kill if it doesn't terminate gracefully
            except Exception:
                pass  # Process might already be dead
        
        self.processes.clear()
        self.is_running = False
        logger.info("All pipeline services stopped")
    
    def process_audio_chunk(self, audio_data: bytes):
        """Process an audio chunk through the entire pipeline."""
        try:
            # Send audio to Whisper for recognition
            whisper_client = self.clients.get('whisper')
            if not whisper_client:
                logger.error("Whisper client not available")
                return
            
            # Encode audio data
            import base64
            audio_b64 = base64.b64encode(audio_data).decode('utf-8')
            
            # Get transcription
            result = whisper_client.send_message('process_audio', {
                'data': audio_b64,
                'format': 'float32',
                'sample_rate': 16000
            })
            
            if result and result.get('status') == 'success':
                text = result['data']['text']
                logger.info(f"Transcribed: {text}")
                
                # Translate the text
                translate_client = self.clients.get('translate')
                if translate_client:
                    translation_result = translate_client.send_message('translate_text', {
                        'text': text
                    })
                    
                    if translation_result and translation_result.get('status') == 'success':
                        translated_text = translation_result['data']['translated_text']
                        logger.info(f"Translated: {translated_text}")
                        
                        # Synthesize the translated text
                        tts_client = self.clients.get('tts')
                        if tts_client:
                            synthesis_result = tts_client.send_message('synthesize_text', {
                                'text': translated_text
                            })
                            
                            if synthesis_result and synthesis_result.get('status') == 'success':
                                audio_data_b64 = synthesis_result['data']['audio_data']
                                
                                # Play the synthesized audio
                                playback_client = self.clients.get('playback')
                                if playback_client:
                                    playback_client.send_message('play_audio', {
                                        'audio_data': audio_data_b64
                                    })
        
        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}")
    
    def run(self):
        """Run the orchestrator."""
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal")
            self.stop_services()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        self.start_services()
        
        try:
            # Keep the orchestrator running
            while self.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down orchestrator...")
        finally:
            self.stop_services()


def main():
    """Main entry point for the pipeline orchestrator."""
    orchestrator = PipelineOrchestrator()
    orchestrator.run()


if __name__ == "__main__":
    main()