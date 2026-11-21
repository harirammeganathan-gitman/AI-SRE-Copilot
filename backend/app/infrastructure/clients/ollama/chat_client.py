import logging

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.app.application.exceptions import ServiceUnavailableError, UpstreamTimeoutError

logger = logging.getLogger(__name__)


class _ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str


class _ChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    message: _ChatMessage


class _ModelTag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


class _TagsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    models: list[_ModelTag]


class OllamaChatClient:
    def __init__(self, client: httpx.AsyncClient, model_name: str) -> None:
        normalized_model = model_name.strip()
        if not normalized_model:
            raise ValueError("Ollama chat model name must not be empty")
        self._client = client
        self._model_name = normalized_model

    @property
    def model_name(self) -> str:
        return self._model_name

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> str:
        try:
            response = await self._client.post(
                "/api/chat",
                json={
                    "model": self._model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "format": response_schema,
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            response.raise_for_status()
            payload = _ChatResponse.model_validate(response.json())
        except httpx.TimeoutException as exc:
            logger.warning(
                "ollama_chat_timeout",
                extra={"event_fields": {"model": self._model_name}},
            )
            raise UpstreamTimeoutError("Ollama RCA generation timed out") from exc
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning(
                "ollama_chat_request_failed",
                extra={
                    "event_fields": {
                        "model": self._model_name,
                        "error_type": type(exc).__name__,
                    }
                },
            )
            raise ServiceUnavailableError("Ollama RCA generation failed") from exc

        if self._model_key(payload.model) != self._model_key(self._model_name):
            raise ServiceUnavailableError("Ollama returned an unexpected chat model")
        content = payload.message.content.strip()
        if not content:
            raise ServiceUnavailableError("Ollama returned an empty RCA response")

        logger.info(
            "ollama_rca_response_generated",
            extra={"event_fields": {"model": self._model_name}},
        )
        return content

    async def is_healthy(self) -> bool:
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
            payload = _TagsResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning(
                "ollama_chat_health_check_failed",
                extra={
                    "event_fields": {
                        "model": self._model_name,
                        "error_type": type(exc).__name__,
                    }
                },
            )
            return False
        expected = self._model_key(self._model_name)
        return any(self._model_key(model.name) == expected for model in payload.models)

    @staticmethod
    def _model_key(model_name: str) -> str:
        name, separator, tag = model_name.strip().partition(":")
        return name if separator and tag == "latest" else model_name.strip()
