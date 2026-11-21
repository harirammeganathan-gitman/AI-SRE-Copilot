from collections.abc import Mapping
from typing import Any


class ApplicationError(Exception):
    code = "APPLICATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details) if details is not None else None


class ResourceNotFoundError(ApplicationError):
    code = "RESOURCE_NOT_FOUND"


class ConflictError(ApplicationError):
    code = "CONFLICT"


class ServiceUnavailableError(ApplicationError):
    code = "SERVICE_UNAVAILABLE"


class UpstreamTimeoutError(ApplicationError):
    code = "UPSTREAM_TIMEOUT"


class InvalidDocumentError(ApplicationError):
    code = "INVALID_DOCUMENT"


class InvalidQueryError(ApplicationError):
    code = "INVALID_QUERY"


class UnsupportedDocumentTypeError(ApplicationError):
    code = "UNSUPPORTED_DOCUMENT_TYPE"


class DocumentTooLargeError(ApplicationError):
    code = "DOCUMENT_TOO_LARGE"
