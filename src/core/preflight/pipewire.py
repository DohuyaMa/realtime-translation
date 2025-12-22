"""Preflight checks for PipeWire audio system."""
import subprocess
import sys
import shutil
from loguru import logger
from typing import Optional


class PipeWirePreflight:
    """Preflight checks for PipeWire audio system."""
    
    @staticmethod
    def check() -> bool:
        """Check if required PipeWire nodes exist.
        
        Returns:
            True if all required nodes exist, False otherwise
        """
        # Check if pactl is available
        if not shutil.which("pactl"):
            logger.error("pactl not found. Please ensure pulseaudio or pipewire-pulse is installed.")
            return False
        
        try:
            # Check for sinks
            result = subprocess.check_output(
                ["pactl", "list", "sinks", "short"],
                text=True
            )
            logger.debug(f"Available sinks:\n{result}")
            
            if "rt_virtual_input" not in result or "rt_virtual_output" not in result:
                logger.error("Virtual PipeWire sinks not found. Please set up PipeWire configuration first.")
                logger.info("Run: python install_pipewire_config.py to set up virtual sinks")
                # List available sinks for debugging
                available_sinks = [line.split()[1] for line in result.split('\n') if line.strip()]
                logger.debug(f"Available sinks: {available_sinks}")
                return False
            
            # Check for sources (monitors)
            result_sources = subprocess.check_output(
                ["pactl", "list", "sources", "short"],
                text=True
            )
            logger.debug(f"Available sources:\n{result_sources}")
            
            if "rt_virtual_output.monitor" not in result_sources:
                logger.error("Virtual PipeWire source (monitor) not found. Please set up PipeWire configuration first.")
                logger.info("Run: python install_pipewire_config.py to set up virtual sinks")
                # List available sources for debugging
                available_sources = [line.split()[1] for line in result_sources.split('\n') if line.strip()]
                logger.debug(f"Available sources: {available_sources}")
                return False
                
            logger.info("PipeWire nodes verified successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to check PipeWire nodes: {e}")
            return False
        except FileNotFoundError:
            logger.error("pactl command not found. Please ensure PipeWire is installed.")
            return False


def check_pipewire_availability() -> bool:
    """Check if pipewire is available and properly configured.
    
    Returns:
        True if pipewire is available, False otherwise
    """
    try:
        # Check if pipewire service is running
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "pipewire"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            logger.warning("PipeWire user service may not be running")
        
        # Check if pactl is available
        result = subprocess.run(
            ["pactl", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            logger.error("pactl command not available")
            return False
        
        logger.info("PipeWire appears to be available")
        return True
    except subprocess.TimeoutExpired:
        logger.error("Timeout while checking PipeWire availability")
        return False
    except Exception as e:
        logger.error(f"Error checking PipeWire availability: {e}")
        return False