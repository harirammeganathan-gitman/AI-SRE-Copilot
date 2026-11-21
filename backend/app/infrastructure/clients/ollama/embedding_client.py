import logging
from collections.abc import Sequence
from math import isfinite

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.app.application.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    embeddings: list[list[float]]


class _ModelTag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


class _TagsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    models: list[_ModelTag]


class OllamaEmbeddingClient:
    def __init__(self, client: httpx.AsyncClient, model_name: str) -> None:
        normalized_model = model_name.strip()
        if not normalized_model:
            raise ValueError("Ollama embedding model name must not be empty")
        self._client = client
        self._model_name = normalized_model

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise ValueError("Embedding inputs must not be empty")
        try:
            response = await self._client.post(
                "/api/embed",
                json={
                    "model": self._model_name,
                    "input": list(texts),
                    "truncate": False,
                },
            )
            response.raise_for_status()
            payload = _EmbeddingResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning(
                "ollama_embedding_request_failed",
                extra={
                    "event_fields": {
                        "model": self._model_name,
                        "input_count": len(texts),
                        "error_type": type(exc).__name__,
                    }
                },
            )
            raise ServiceUnavailableError("Ollama embedding request failed") from exc

        if self._model_key(payload.model) != self._model_key(self._model_name):
            raise ServiceUnavailableError("Ollama returned an unexpected embedding model")
        if len(payload.embeddings) != len(texts):
            raise ServiceUnavailableError("Ollama returned an unexpected number of embeddings")
        if any(not vector for vector in payload.embeddings):
            raise ServiceUnavailableError("Ollama returned an empty embedding")

        logger.info(
            "ollama_embeddings_generated",
            extra={
                "event_fields": {
                    "model": self._model_name,
                    "input_count": len(texts),
                    "embedding_dimension": len(payload.embeddings[0]),
                }
            },
        )
        return payload.embeddings

    async def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("Embedding query must not be empty")
        embeddings = await self.embed_documents((text,))
        if len(embeddings) != 1:
            raise ServiceUnavailableError("Ollama returned an unexpected query embedding count")
        vector = embeddings[0]
        if not all(isfinite(value) for value in vector):
            raise ServiceUnavailableError("Ollama returned non-finite query embedding values")
        return vector

    async def is_healthy(self) -> bool:
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
            payload = _TagsResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning(
                "ollama_health_check_failed",
                extra={"event_fields": {"error_type": type(exc).__name__}},
            )
            return False

        expected = self._model_key(self._model_name)
        return any(self._model_key(model.name) == expected for model in payload.models)

    @staticmethod
    def _model_key(model_name: str) -> str:
        name, separator, tag = model_name.strip().partition(":")
        if separator and tag == "latest":
            return name
        return model_name.strip()
