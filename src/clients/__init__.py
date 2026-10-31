"""HTTP clients for the remote speech services and the LLM worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import httpx

from ..core.runtime import EdgeSettings
from .asr import AsrClient, AsrResult
from .base import Credential, ServiceClient, ServiceError
from .discovery import Directory, Endpoint, ServiceEntry, discover, endpoints
from .llm import LlmClient, LlmTarget
from .session import WorkerSessions
from .health import ServiceHealthMonitor
from .pool import EndpointPool, SpeechPools, build_pools
from .tts import TtsClient
from .vosk import VoskClient, VoskResult, VoskWord

__all__ = [
    "AsrClient",
    "AsrResult",
    "TtsClient",
    "VoskClient",
    "VoskResult",
    "VoskWord",
    "LlmClient",
    "LlmTarget",
    "WorkerSessions",
    "Credential",
    "Directory",
    "Endpoint",
    "ServiceEntry",
    "discover",
    "endpoints",
    "ServiceClient",
    "ServiceError",
    "ClientFactories",
    "client_factories",
    "EndpointPool",
    "SpeechPools",
    "build_pools",
    "ServiceHealthMonitor",
]


@dataclass(frozen=True)
class ClientFactories:
    asr: Callable[[Endpoint], AsrClient]
    tts: Callable[[Endpoint], TtsClient]
    vosk: Callable[[Endpoint], VoskClient]


def client_factories(
    settings: EdgeSettings, transport: Optional[httpx.BaseTransport] = None
) -> ClientFactories:
    t = settings.timeouts
    common = dict(connect_timeout_s=t.connect_s, transport=transport)
    return ClientFactories(
        asr=lambda e: AsrClient(
            e.url, credential=e.credential, timeout_s=t.asr_s, **common
        ),
        tts=lambda e: TtsClient(
            e.url, credential=e.credential, timeout_s=t.tts_s, **common
        ),
        vosk=lambda e: VoskClient(
            e.url, credential=e.credential, timeout_s=t.asr_s, **common
        ),
    )
