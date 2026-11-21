from typing import Protocol


class StructuredChatGenerator(Protocol):
    @property
    def model_name(self) -> str: ...

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> str: ...
