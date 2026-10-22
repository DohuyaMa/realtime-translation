"""Audio processing components for real-time translation system.

Submodules with heavy or optional dependencies (pyaudio) are imported lazily so
that lightweight modules such as ``vad_stream`` work on the edge devShell.
"""

import importlib

_LAZY = {
    "AudioCapture": ".capture",
    "AudioRouter": ".routing",
    "AudioProcessor": ".processor",
}

__all__ = list(_LAZY)


def __getattr__(name: str):
    if name in _LAZY:
        return getattr(importlib.import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
