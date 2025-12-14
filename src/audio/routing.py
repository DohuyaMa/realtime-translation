import pulsectl
import threading
from loguru import logger
from typing import Optional, Dict, List, Tuple

# Fixed device names for the static PipeWire configuration
VIRTUAL_INPUT_SINK = "rt_virtual_input"
VIRTUAL_OUTPUT_SINK = "rt_virtual_output"
VIRTUAL_MIC_SOURCE = "rt_virtual_output.monitor"


class AudioRouter:
    """Audio routing manager for PipeWire/PulseAudio virtual devices."""
    
    def __init__(self):
        """Initialize audio router."""
        self.pulse = pulsectl.Pulse('real-time-translator')
        self._lock = threading.Lock()
        self.virtual_input = VIRTUAL_INPUT_SINK
        self.virtual_output = VIRTUAL_OUTPUT_SINK

    def get_virtual_devices(self) -> Tuple[str, str]:
        """Get the fixed virtual input and output devices.
        
        Returns:
            Tuple of (input_device_name, output_device_name)
        """
        return self.virtual_input, self.virtual_output

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
                
                # Create loopback module with low latency settings
                self.pulse.module_load('module-loopback',
                    f'source={source_name} sink={sink_name} '
                    'adjust_time=0 '  # Disable time adjustment for lower latency
                    'rate=48000 channels=2 '
                    'latency_msec=10 '  # Low latency setting
                    'source_dont_move=true '  # Prevent source from moving
                    'sink_dont_move=true')  # Prevent sink from moving
                
                logger.info(f"Routed audio: {source_name} -> {sink_name}")
                
            except Exception as e:
                logger.error(f"Failed to route audio: {e}")
                raise

    def get_virtual_device_status(self) -> Dict[str, Dict]:
        """Check status of virtual devices.
        
        Returns:
            Dictionary with status and metrics of input and output devices
        """
        with self._lock:
            try:
                sinks = {s.name: s for s in self.pulse.sink_list()}
                sources = {s.name: s for s in self.pulse.source_list()}
                
                input_stats = self._get_device_stats(
                    sinks.get(self.virtual_input) if self.virtual_input else None
                )
                output_stats = self._get_device_stats(
                    sinks.get(self.virtual_output) if self.virtual_output else None
                )
                
                return {
                    'input': input_stats,
                    'output': output_stats
                }
                
            except Exception as e:
                logger.error(f"Failed to get virtual device status: {e}")
                return {
                    'input': {'active': False, 'latency_ms': 0, 'buffer_size': 0},
                    'output': {'active': False, 'latency_ms': 0, 'buffer_size': 0}
                }

    def _get_device_stats(self, device) -> Dict:
        """Get device statistics.
        
        Args:
            device: PulseAudio device object
            
        Returns:
            Dictionary with device statistics
        """
        if not device:
            return {'active': False, 'latency_ms': 0, 'buffer_size': 0}
            
        try:
            latency_us = device.latency
            buffer_size = device.configured_latency
            
            return {
                'active': True,
                'latency_ms': latency_us / 1000 if latency_us else 0,
                'buffer_size': buffer_size,
                'sample_rate': device.rate,
                'channels': device.channel_count
            }
        except Exception as e:
            logger.error(f"Failed to get device stats: {e}")
            return {'active': False, 'latency_ms': 0, 'buffer_size': 0}
        
    def cleanup(self):
        """Clean up audio routing."""
        with self._lock:
            try:
                logger.info("Audio routing cleanup completed")
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")
            finally:
                self.pulse.close()

    def __del__(self):
        """Cleanup on deletion."""
        self.cleanup()