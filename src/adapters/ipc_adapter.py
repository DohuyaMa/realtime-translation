"""IPC adapter for communication with the translation system via Unix sockets."""
import json
import socket
import threading
from typing import Dict, List, Optional
from loguru import logger

from ..controller.controller import TranslatorController, Device
from ..core.runtime import get_runtime_config


class IPCAdapter:
    """IPC adapter for communication with the translation system via Unix sockets."""
    
    def __init__(self, socket_path: str = None):
        """Initialize the IPC adapter with a socket path."""
        self.socket_path = socket_path or get_runtime_config().get_main_socket_path()
        """Initialize the IPC adapter with a socket path."""
        self.socket_path = socket_path
        self._socket: Optional[socket.socket] = None
        self._connected = False
        self._lock = threading.Lock()
    
    def connect(self) -> bool:
        """Connect to the IPC socket."""
        try:
            with self._lock:
                self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._socket.connect(self.socket_path)
                self._connected = True
                return True
        except Exception as e:
            logger.error(f"Failed to connect to IPC socket {self.socket_path}: {e}")
            self._connected = False
            return False
    
    def disconnect(self):
        """Disconnect from the IPC socket."""
        with self._lock:
            if self._socket:
                try:
                    self._socket.close()
                except:
                    pass
                self._socket = None
                self._connected = False
    
    def _send_command(self, command: str, data: Dict = None) -> Optional[Dict]:
        """Send a command to the backend and receive response."""
        if not self._connected or not self._socket:
            if not self.connect():
                return None
        
        try:
            # Prepare the message
            message = {
                "command": command,
                "data": data or {}
            }
            
            # Send the message
            msg_str = json.dumps(message) + "\n"
            self._socket.sendall(msg_str.encode())
            
            # Receive the response
            response_str = self._socket.recv(4096).decode().strip()
            if response_str:
                return json.loads(response_str)
            return None
        except Exception as e:
            logger.error(f"Error sending command {command}: {e}")
            self._connected = False
            return None
    
    def start_pipeline(self) -> bool:
        """Start the entire translation pipeline."""
        response = self._send_command("start_pipeline")
        return response and response.get("success", False)
    
    def stop_pipeline(self) -> bool:
        """Stop the entire translation pipeline."""
        response = self._send_command("stop_pipeline")
        return response and response.get("success", False)
    
    def start_service(self, name: str) -> bool:
        """Start a specific service."""
        response = self._send_command("start_service", {"service": name})
        return response and response.get("success", False)
    
    def stop_service(self, name: str) -> bool:
        """Stop a specific service."""
        response = self._send_command("stop_service", {"service": name})
        return response and response.get("success", False)
    
    def get_status(self) -> Dict:
        """Get system status."""
        response = self._send_command("get_status")
        return response or {}
    
    def set_languages(self, source_lang: str, target_lang: str = "en") -> bool:
        """Set source and target languages."""
        response = self._send_command("set_languages", {
            "source_lang": source_lang,
            "target_lang": target_lang
        })
        return response and response.get("success", False)
    
    def get_input_devices(self) -> List[Device]:
        """Get available input devices."""
        response = self._send_command("get_input_devices")
        devices_data = response.get("devices", []) if response else []
        devices = []
        for device_info in devices_data:
            devices.append(Device(
                device_info.get("name", ""),
                device_info.get("description", "")
            ))
        return devices
    
    def get_output_devices(self) -> List[Device]:
        """Get available output devices."""
        response = self._send_command("get_output_devices")
        devices_data = response.get("devices", []) if response else []
        devices = []
        for device_info in devices_data:
            devices.append(Device(
                device_info.get("name", ""),
                device_info.get("description", "")
            ))
        return devices
    
    def set_input_device(self, device_id: str) -> bool:
        """Set the input device."""
        response = self._send_command("set_input_device", {"device_id": device_id})
        return response and response.get("success", False)
    
    def set_output_device(self, device_id: str) -> bool:
        """Set the output device."""
        response = self._send_command("set_output_device", {"device_id": device_id})
        return response and response.get("success", False)
    
    def get_audio_levels(self) -> Dict[str, float]:
        """Get current audio input/output levels."""
        response = self._send_command("get_audio_levels")
        if response and "levels" in response:
            return response["levels"]
        return {"input": 0.0, "output": 0.0}
    
    def toggle_translation(self, enabled: bool) -> bool:
        """Enable or disable translation."""
        response = self._send_command("toggle_translation", {"enabled": enabled})
        return response and response.get("success", False)
    
    def cleanup(self) -> None:
        """Clean up resources."""
        self.disconnect()