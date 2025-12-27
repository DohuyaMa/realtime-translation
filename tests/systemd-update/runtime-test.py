"""
Runtime configuration tests for the real-time translation system.
These tests validate that runtime configurations work correctly with XDG-compliant paths.
"""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import sys

# Add src directory to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.runtime import RuntimePaths
from core.config import Config
from core.env import EnvironmentManager


class TestRuntimeConfiguration(unittest.TestCase):
    """Test runtime configuration and XDG-compliant paths"""
    
    def setUp(self):
        """Set up test environment"""
        self.test_config = {
            'runtime': {
                'xdg_base': '/tmp/test-xdg',
                'socket_dir': '/tmp/test-sockets',
                'log_dir': '/tmp/test-logs',
                'config_dir': '/tmp/test-config'
            }
        }
    
    def test_xdg_path_generation(self):
        """Test that XDG-compliant paths are generated correctly"""
        with tempfile.TemporaryDirectory() as temp_dir:
            xdg_base = os.path.join(temp_dir, 'xdg')
            socket_dir = os.path.join(temp_dir, 'sockets')
            log_dir = os.path.join(temp_dir, 'logs')
            config_dir = os.path.join(temp_dir, 'config')
            
            # Create directories
            os.makedirs(xdg_base, exist_ok=True)
            os.makedirs(socket_dir, exist_ok=True)
            os.makedirs(log_dir, exist_ok=True)
            os.makedirs(config_dir, exist_ok=True)
            
            # Test RuntimePaths generation
            runtime_paths = RuntimePaths(
                xdg_base=xdg_base,
                socket_dir=socket_dir,
                log_dir=log_dir,
                config_dir=config_dir
            )
            
            # Verify paths are correctly set
            self.assertTrue(os.path.exists(runtime_paths.socket_dir))
            self.assertTrue(os.path.exists(runtime_paths.log_dir))
            self.assertTrue(os.path.exists(runtime_paths.config_dir))
            
            # Verify specific socket paths exist
            expected_sockets = [
                'capture.sock', 'whisper.sock', 'translate.sock', 
                'tts.sock', 'playback.sock', 'hybrid-whisper.sock'
            ]
            
            for sock_name in expected_sockets:
                sock_path = os.path.join(runtime_paths.socket_dir, sock_name)
                self.assertTrue(os.path.exists(os.path.dirname(sock_path)))
    
    def test_environment_manager(self):
        """Test environment variable configuration"""
        with tempfile.TemporaryDirectory() as temp_dir:
            env_manager = EnvironmentManager()
            
            # Test setting runtime paths
            runtime_paths = RuntimePaths(
                xdg_base=temp_dir,
                socket_dir=os.path.join(temp_dir, 'sockets'),
                log_dir=os.path.join(temp_dir, 'logs'),
                config_dir=os.path.join(temp_dir, 'config')
            )
            
            env_vars = env_manager.get_runtime_env_vars(runtime_paths)
            
            # Verify environment variables are properly set
            self.assertIn('RT_SOCKET_DIR', env_vars)
            self.assertIn('RT_LOG_DIR', env_vars)
            self.assertIn('RT_CONFIG_DIR', env_vars)
            
            # Verify values match expected paths
            self.assertEqual(env_vars['RT_SOCKET_DIR'], runtime_paths.socket_dir)
            self.assertEqual(env_vars['RT_LOG_DIR'], runtime_paths.log_dir)
            self.assertEqual(env_vars['RT_CONFIG_DIR'], runtime_paths.config_dir)
    
    def test_config_loading(self):
        """Test configuration loading with runtime paths"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = os.path.join(temp_dir, 'config')
            os.makedirs(config_dir, exist_ok=True)
            
            # Create a sample config file
            config_path = os.path.join(config_dir, 'default.yml')
            sample_config = """
            audio:
              input_device: "default"
              output_device: "default"
              sample_rate: 16000
            translation:
              source_lang: "en"
              target_lang: "uk"
            """
            
            with open(config_path, 'w') as f:
                f.write(sample_config)
            
            # Test config loading
            config = Config(config_path)
            
            # Verify config values are loaded correctly
            self.assertEqual(config.get('audio.sample_rate'), 16000)
            self.assertEqual(config.get('translation.source_lang'), 'en')
            self.assertEqual(config.get('translation.target_lang'), 'uk')


class TestSystemdIntegration(unittest.TestCase):
    """Test systemd integration aspects of runtime configuration"""
    
    def test_socket_activation_paths(self):
        """Test that socket paths match systemd expectations"""
        with tempfile.TemporaryDirectory() as temp_dir:
            socket_dir = os.path.join(temp_dir, 'sockets')
            os.makedirs(socket_dir, exist_ok=True)
            
            # Test socket files that should exist for systemd integration
            expected_sockets = [
                'rt-capture.socket',
                'rt-whisper.socket', 
                'rt-translate.socket',
                'rt-tts.socket',
                'rt-playback.socket',
                'rt-hybrid-whisper.socket'
            ]
            
            # Create expected socket files
            for sock_name in expected_sockets:
                sock_path = os.path.join(socket_dir, sock_name)
                # Create the socket file
                with open(sock_path, 'w') as f:
                    f.write('')
            
            # Verify all expected sockets exist
            for sock_name in expected_sockets:
                sock_path = os.path.join(socket_dir, sock_name)
                self.assertTrue(os.path.exists(sock_path))


if __name__ == '__main__':
    unittest.main()