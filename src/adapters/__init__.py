"""Adapters module for the real-time translation system."""
from .direct_adapter import DirectAdapter
from .ipc_adapter import IPCAdapter

__all__ = ['DirectAdapter', 'IPCAdapter']