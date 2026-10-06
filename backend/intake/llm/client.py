"""Vendor-neutral model interface used by every pipeline step (ADR 0001).

A request is instructions + ordered content parts + a Pydantic output model. The result is the
validated model instance with usage and latency, so steps never see vendor types.
"""

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from intake.settings import get_settings


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    png: bytes
    detail: str = "high"


Part = TextPart | ImagePart


@dataclass(frozen=True)
class LLMRequest[T: BaseModel]:
    purpose: str  # "extract", "triage", "protocol": used for cache routing and recordings
    model: str
    instructions: str  # static: kept first so the provider's prefix cache can reuse it
    parts: list[Part]
    output: type[T]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    def cost_usd(self, model: str) -> float:
        prices = get_settings().price_table.get(model)
        if prices is None:
            return 0.0
        fresh = max(self.input_tokens - self.cached_input_tokens, 0)
        return (
            fresh * prices[0]
            + self.cached_input_tokens * prices[1]
            + self.output_tokens * prices[2]
        ) / 1_000_000


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    parsed: T
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0


class LLMError(Exception):
    """The provider call failed (network, rate limit, refusal). Retried by the job queue."""


class LLMOutputInvalid(Exception):
    """The provider returned output that does not validate against the schema."""


class LLMClient(Protocol):
    async def complete[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]: ...

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]: ...
