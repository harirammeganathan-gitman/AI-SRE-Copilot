import logging
import time
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.core.request_context import request_id_context

logger = logging.getLogger(__name__)
_REQUEST_ID_HEADER = b"x-request-id"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._get_request_id(scope)
        token = request_id_context.set(request_id)
        started_at = time.perf_counter()
        status_code = 500
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "")

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                if not any(name.lower() == _REQUEST_ID_HEADER for name, _ in headers):
                    headers.append((_REQUEST_ID_HEADER, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.info(
                "request_completed",
                extra={
                    "event_fields": {
                        "method": method,
                        "path": path,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    }
                },
            )
            request_id_context.reset(token)

    @staticmethod
    def _get_request_id(scope: Scope) -> str:
        headers = scope.get("headers", [])
        raw_value = next(
            (value for name, value in headers if name.lower() == _REQUEST_ID_HEADER),
            b"",
        )
        try:
            return str(UUID(raw_value.decode("ascii")))
        except (UnicodeDecodeError, ValueError):
            return str(uuid4())
