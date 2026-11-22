import pytest
from pydantic import SecretStr, ValidationError

from backend.app.core.config import Settings


def test_production_rejects_api_docs_and_wildcard_hosts() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment="production",
            enable_api_docs=True,
            allowed_hosts=["api.example.com"],
        )

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment="production",
            enable_api_docs=False,
            allowed_hosts=["*"],
        )


def test_cors_origins_are_normalized_and_reject_wildcard() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        cors_allowed_origins=["https://console.example.com/"],
    )
    assert settings.cors_allowed_origins == ["https://console.example.com"]

    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_allowed_origins=["*"])  # type: ignore[call-arg]


def test_secret_settings_use_masked_secret_type() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=SecretStr("postgresql://user:secret@db/app"),
    )
    assert isinstance(settings.database_url, SecretStr)
    assert settings.database_url.get_secret_value() == "postgresql://user:secret@db/app"
    assert "secret" not in repr(settings.database_url)


def test_example_environment_file_is_valid() -> None:
    settings = Settings(_env_file=".env.example")  # type: ignore[call-arg]

    assert settings.environment == "development"
    assert settings.max_upload_size_bytes == 10 * 1024 * 1024
    assert settings.cors_allowed_origins == ["http://localhost:8501"]
    assert settings.top_k_retrieve == 15
    assert settings.top_k_rerank == 5
    assert settings.ollama_chat_model == "llama3.1"
    assert settings.rca_max_attempts == 3


def test_rag_rerank_limit_must_not_exceed_retrieval_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            top_k_retrieve=4,
            top_k_rerank=5,
        )


def test_rca_request_timeout_covers_embedding_and_model_retries() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        rca_timeout_seconds=120,
        rca_max_attempts=3,
        rca_retry_backoff_seconds=0.25,
    )

    assert settings.rca_request_timeout_seconds == pytest.approx(690.75)


def test_api_base_url_must_be_an_origin_without_credentials_or_path() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        api_base_url="https://api.example.com/",
    )
    assert settings.api_base_url == "https://api.example.com"

    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_base_url="https://user:pass@api.example.com")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_base_url="https://api.example.com/v1")  # type: ignore[call-arg]
