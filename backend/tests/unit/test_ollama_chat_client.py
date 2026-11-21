import httpx
import pytest

from backend.app.application.exceptions import UpstreamTimeoutError
from backend.app.infrastructure.clients.ollama.chat_client import OllamaChatClient


@pytest.mark.anyio
async def test_chat_client_maps_http_timeout_to_upstream_timeout() -> None:
    def timeout_request(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Ollama response timed out", request=request)

    async with httpx.AsyncClient(
        base_url="http://ollama",
        transport=httpx.MockTransport(timeout_request),
    ) as client:
        chat = OllamaChatClient(client, "llama3.1")

        with pytest.raises(UpstreamTimeoutError):
            await chat.generate_json(
                system_prompt="system",
                user_prompt="user",
                response_schema={"type": "object"},
            )
