"""Preflight checks for PipeWire audio system."""
import subprocess
import sys
import shutil
from loguru import logger
from typing import Optional, Set

from ...audio.routing import VIRTUAL_INPUT_SINK, VIRTUAL_MIC_SOURCE, VIRTUAL_OUTPUT_SINK


def _names(pactl_short: str) -> Set[str]:
    """Node names (2nd column) of ``pactl list sinks|sources short``."""
    return {
        line.split("\t")[1]
        for line in pactl_short.splitlines()
        if line.count("\t") >= 1
    }


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
            sinks = _names(result)
            missing = [n for n in (VIRTUAL_INPUT_SINK, VIRTUAL_OUTPUT_SINK) if n not in sinks]

            # The virtual microphone other apps record the translation from
            result_sources = subprocess.check_output(
                ["pactl", "list", "sources", "short"],
                text=True
            )
            logger.debug(f"Available sources:\n{result_sources}")
            if VIRTUAL_MIC_SOURCE not in _names(result_sources):
                missing.append(VIRTUAL_MIC_SOURCE)

            if missing:
                logger.error(
                    f"Virtual PipeWire devices missing: {', '.join(missing)}. Enable"
                    " rt-translator.virtualSinks (Home Manager) or"
                    " nixosModules.virtual-sinks and restart PipeWire, or run"
                    " `nix develop`."
                )
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