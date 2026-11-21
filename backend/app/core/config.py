from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_SRE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    app_name: str = "AI SRE Copilot API"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    enable_api_docs: bool = False
    api_v1_prefix: str = "/api/v1"
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver", "backend"]
    )
    cors_allowed_origins: list[str] = Field(default_factory=list)
    max_upload_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0, le=100 * 1024 * 1024)
    api_base_url: str = "http://localhost:8000"

    database_url: SecretStr | None = None
    chroma_url: AnyHttpUrl | None = None
    ollama_base_url: AnyHttpUrl | None = None
    ollama_chat_model: str = "llama3.1"
    ollama_embedding_model: str = "nomic-embed-text"
    chroma_collection_name: str = "incident_knowledge_base"
    embedding_batch_size: int = Field(default=32, gt=0, le=256)
    top_k_retrieve: int = Field(default=15, gt=0, le=100)
    top_k_rerank: int = Field(default=5, gt=0, le=100)
    rca_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    rca_max_attempts: int = Field(default=3, ge=1, le=5)
    rca_retry_backoff_seconds: float = Field(default=0.25, ge=0, le=5)

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith("/") or normalized == "":
            raise ValueError("api_v1_prefix must be an absolute URL path")
        return normalized

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("api_base_url must be an HTTP(S) origin without credentials or path")
        return normalized

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values if value.strip()]
        if "*" in normalized and len(normalized) > 1:
            raise ValueError("wildcard host cannot be combined with explicit hosts")
        return normalized

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_cors_origins(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            origin = value.strip().rstrip("/")
            parsed = urlsplit(origin)
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins")
            normalized.append(origin)
        return normalized

    @field_validator("api_base_url", "chroma_url", "ollama_base_url")
    @classmethod
    def validate_service_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is None:
            return None
        parsed = urlsplit(str(value))
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Service URLs must not contain a path, query, or fragment")
        if parsed.username or parsed.password:
            raise ValueError("Service credentials must not be embedded in URLs")
        return value

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if not self.ollama_embedding_model.strip():
            raise ValueError("ollama_embedding_model must not be empty")
        if not self.ollama_chat_model.strip():
            raise ValueError("ollama_chat_model must not be empty")
        if self.chroma_collection_name != "incident_knowledge_base":
            raise ValueError("chroma_collection_name must be incident_knowledge_base")
        if self.top_k_rerank > self.top_k_retrieve:
            raise ValueError("top_k_rerank must not exceed top_k_retrieve")
        if self.environment == "production":
            if self.enable_api_docs:
                raise ValueError("API documentation must be disabled in production")
            if not self.allowed_hosts or "*" in self.allowed_hosts:
                raise ValueError("production requires explicit allowed hosts")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
