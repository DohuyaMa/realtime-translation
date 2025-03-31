import pulsectl
import threading
from loguru import logger
from typing import Optional, Dict, List, Tuple

class AudioRouter:
    """Audio routing manager for PipeWire/PulseAudio virtual devices."""
    
    def __init__(self):
        """Initialize audio router."""
        self.pulse = pulsectl.Pulse('real-time-translator')
        self._lock = threading.Lock()
        self.virtual_input: Optional[str] = None
        self.virtual_output: Optional[str] = None

    def create_virtual_devices(self) -> Tuple[str, str]:
        """Create virtual input and output devices.
        
        Returns:
            Tuple of (input_device_name, output_device_name)
        """
        with self._lock:
            try:
                # Create virtual input sink
                input_name = "virtual_input"
                self.pulse.module_load('module-null-sink',
                    f'sink_name={input_name} '
                    'sink_properties=device.description="Virtual Input" '
                    'rate=48000 channels=2')
                
                # Create virtual output sink
                output_name = "virtual_output"
                self.pulse.module_load('module-null-sink',
                    f'sink_name={output_name} '
                    'sink_properties=device.description="Virtual Output" '
                    'rate=48000 channels=2')
                
                self.virtual_input = input_name
                self.virtual_output = output_name
                
                logger.info("Virtual audio devices created successfully")
                return input_name, output_name
                
            except Exception as e:
                logger.error(f"Failed to create virtual devices: {e}")
                raise

    def list_devices(self) -> Dict[str, List[Dict]]:
        """List all available audio devices.
        
        Returns:
            Dictionary containing 'inputs' and 'outputs' lists
        """
        with self._lock:
            try:
                sources = self.pulse.source_list()
                sinks = self.pulse.sink_list()
                
                return {
                    'inputs': [
                        {
                            'name': s.name,
                            'description': s.description,
                            'channels': s.channel_count,
                            'sample_rate': s.rate
                        }
                        for s in sources
                    ],
                    'outputs': [
                        {
                            'name': s.name,
                            'description': s.description,
                            'channels': s.channel_count,
                            'sample_rate': s.rate
                        }
                        for s in sinks
                    ]
                }
            except Exception as e:
                logger.error(f"Failed to list devices: {e}")
                raise

    def set_default_source(self, source_name: str):
        """Set default audio input source.
        
        Args:
            source_name: Name of the source to set as default
        """
        with self._lock:
            try:
                self.pulse.source_default_set(source_name)
                logger.info(f"Set default source to: {source_name}")
            except Exception as e:
                logger.error(f"Failed to set default source: {e}")
                raise

    def set_default_sink(self, sink_name: str):
        """Set default audio output sink.
        
        Args:
            sink_name: Name of the sink to set as default
        """
        with self._lock:
            try:
                self.pulse.sink_default_set(sink_name)
                logger.info(f"Set default sink to: {sink_name}")
            except Exception as e:
                logger.error(f"Failed to set default sink: {e}")
                raise

    def route_audio(self, source_name: str, sink_name: str):
        """Route audio from source to sink.
        
        Args:
            source_name: Name of the source device
            sink_name: Name of the sink device
        """
        with self._lock:
            try:
                # Find the source and sink
                source = next((s for s in self.pulse.source_list() if s.name == source_name), None)
                sink = next((s for s in self.pulse.sink_list() if s.name == sink_name), None)
                
                if not source or not sink:
                    raise ValueError(f"Source '{source_name}' or sink '{sink_name}' not found")
                
                # Create loopback module
                self.pulse.module_load('module-loopback',
                    f'source={source_name} sink={sink_name} '
                    'adjust_time=1 rate=48000 channels=2')
                
                logger.info(f"Routed audio: {source_name} -> {sink_name}")
                
            except Exception as e:
                logger.error(f"Failed to route audio: {e}")
                raise

    def get_virtual_device_status(self) -> Dict[str, bool]:
        """Check status of virtual devices.
        
        Returns:
            Dictionary with status of input and output devices
        """
        with self._lock:
            try:
                sinks = [s.name for s in self.pulse.sink_list()]
                return {
                    'input': self.virtual_input in sinks if self.virtual_input else False,
                    'output': self.virtual_output in sinks if self.virtual_output else False
                }
            except Exception as e:
                logger.error(f"Failed to get virtual device status: {e}")
                return {'input': False, 'output': False}

    def cleanup(self):
        """Clean up audio routing and virtual devices."""
        with self._lock:
            try:
                # Unload virtual device modules
                for module in self.pulse.module_list():
                    if any(name in str(module) for name in 
                        [self.virtual_input, self.virtual_output]):
                        self.pulse.module_unload(module.index)
                
                logger.info("Audio routing cleanup completed")
                
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")
            finally:
                self.pulse.close()

    def __del__(self):
        """Cleanup on deletion."""
        self.cleanup()