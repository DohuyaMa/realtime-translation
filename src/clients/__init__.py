"""HTTP clients for the remote speech services and the LLM worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import httpx

from ..core.runtime import EdgeSettings
from .asr import AsrClient, AsrResult
from .base import ServiceClient, ServiceError
from .llm import LlmClient, LlmTarget, StaticTarget
from .tts import TtsClient

__all__ = [
    "AsrClient",
    "AsrResult",
    "TtsClient",
    "LlmClient",
    "LlmTarget",
    "StaticTarget",
    "ServiceClient",
    "ServiceError",
    "ClientFactories",
    "client_factories",
]


@dataclass(frozen=True)
class ClientFactories:
    asr: Callable[[str], AsrClient]
    tts: Callable[[str], TtsClient]


def client_factories(settings: EdgeSettings, transport: Optional[httpx.BaseTransport] = None) -> ClientFactories:
    t = settings.timeouts
    common = dict(token_file=settings.api_token_file, connect_timeout_s=t.connect_s, transport=transport)
    return ClientFactories(
        asr=lambda url: AsrClient(url, timeout_s=t.asr_s, **common),
        tts=lambda url: TtsClient(url, timeout_s=t.tts_s, **common),
    )
