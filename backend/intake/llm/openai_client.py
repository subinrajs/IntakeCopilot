"""OpenAI adapter: Responses API with Structured Outputs (strict JSON schema from Pydantic)."""

import base64
import time
from typing import Any, cast

import openai
from pydantic import BaseModel, ValidationError

from intake.llm.client import (
    ImagePart,
    LLMError,
    LLMOutputInvalid,
    LLMRequest,
    LLMResult,
    TextPart,
    Usage,
)
from intake.settings import get_settings

EMBEDDING_DIMENSIONS = 384  # matches protocols.embedding vector(384) (ADR 0002)


class OpenAIClient:
    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        key = api_key or settings.openai_api_key
        if not key:
            raise LLMError("OPENAI_API_KEY is not set")
        self._client = openai.AsyncOpenAI(
            api_key=key, timeout=settings.openai_timeout_seconds, max_retries=2
        )
        self._reasoning_effort = settings.openai_reasoning_effort
        self._embedding_model = settings.openai_embedding_model

    @staticmethod
    def _content(parts: list[TextPart | ImagePart]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        for part in parts:
            if isinstance(part, TextPart):
                content.append({"type": "input_text", "text": part.text})
            else:
                data = base64.b64encode(part.png).decode()
                content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{data}",
                        "detail": part.detail,
                    }
                )
        return content

    async def complete[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]:
        started = time.monotonic()
        try:
            response = await self._client.responses.parse(
                model=request.model,
                instructions=request.instructions,
                input=cast(Any, [{"role": "user", "content": self._content(request.parts)}]),
                text_format=request.output,
                reasoning=cast(Any, {"effort": self._reasoning_effort}),
                prompt_cache_key=f"intake-{request.purpose}",
                store=False,  # no provider-side retention of requisition content
            )
        except ValidationError as error:
            raise LLMOutputInvalid(str(error)) from error
        except openai.APIError as error:
            raise LLMError(f"{type(error).__name__}: {error}") from error
        latency_ms = round((time.monotonic() - started) * 1000)
        parsed = response.output_parsed
        if parsed is None:
            raise LLMOutputInvalid("model returned no parsed output (refusal or empty)")
        usage = Usage()
        if response.usage is not None:
            details = response.usage.input_tokens_details
            usage = Usage(
                input_tokens=response.usage.input_tokens,
                cached_input_tokens=details.cached_tokens if details else 0,
                output_tokens=response.usage.output_tokens,
            )
        return LLMResult(parsed=parsed, model=response.model, usage=usage, latency_ms=latency_ms)

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        try:
            response = await self._client.embeddings.create(
                model=self._embedding_model, input=texts, dimensions=EMBEDDING_DIMENSIONS
            )
        except openai.APIError as error:
            raise LLMError(f"{type(error).__name__}: {error}") from error
        vectors = [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        return vectors, Usage(input_tokens=response.usage.prompt_tokens)
