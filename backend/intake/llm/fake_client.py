"""Deterministic client for tests: scripted responses per purpose, no network, no cost."""

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from intake.llm.client import LLMOutputInvalid, LLMRequest, LLMResult, Usage

Responder = Callable[[LLMRequest[Any]], BaseModel | dict[str, Any] | Exception]


class FakeLLMClient:
    """Each purpose has a queue of responders; the last one repeats when the queue runs out.

    A responder may return a model instance, a dict (validated against the request's output
    model, like the real client) or an exception to raise.
    """

    def __init__(
        self,
        responders: dict[str, list[Responder]] | None = None,
        fallback: Any | None = None,
    ) -> None:
        """`fallback` (e.g. the dev oracle) answers purposes with no scripted responder."""
        self.responders: dict[str, list[Responder]] = defaultdict(list, responders or {})
        self.fallback = fallback
        self.requests: list[LLMRequest[Any]] = []
        self.embed_calls: list[list[str]] = []

    def on(self, purpose: str, *responders: Responder) -> "FakeLLMClient":
        self.responders[purpose].extend(responders)
        return self

    async def complete[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]:
        self.requests.append(request)
        queue = self.responders[request.purpose]
        if not queue:
            if self.fallback is not None:
                result: LLMResult[T] = await self.fallback.complete(request)
                return result
            raise AssertionError(f"no fake response for {request.purpose}")
        responder = queue.pop(0) if len(queue) > 1 else queue[0]
        value = responder(request)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict):
            try:
                value = request.output.model_validate(value)
            except Exception as error:
                raise LLMOutputInvalid(str(error)) from error
        return LLMResult(
            parsed=request.output.model_validate(value.model_dump()),
            model=request.model,
            usage=Usage(input_tokens=1000, cached_input_tokens=0, output_tokens=200),
            latency_ms=5,
        )

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        self.embed_calls.append(texts)
        return [_bag_of_words_vector(t) for t in texts], Usage(input_tokens=len(texts))


def _bag_of_words_vector(text: str, dims: int = 384) -> list[float]:
    """Cheap deterministic embedding: hashed word counts, L2-normalised."""
    import hashlib
    import math
    import re

    vector = [0.0] * dims
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % dims
        vector[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]
