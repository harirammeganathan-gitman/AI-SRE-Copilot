import logging
from collections.abc import Mapping
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.application.exceptions import (
    ApplicationError,
    ConflictError,
    DocumentTooLargeError,
    InvalidDocumentError,
    InvalidQueryError,
    ResourceNotFoundError,
    ServiceUnavailableError,
    UnsupportedDocumentTypeError,
    UpstreamTimeoutError,
)
from backend.app.core.request_context import request_id_context
from backend.app.domain.exceptions import DomainError
from backend.app.interfaces.api.schemas.common import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=dict(details) if details else None),
        request_id=request_id_context.get(),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def handle_application_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApplicationError):
        raise TypeError("Application error handler received an unsupported exception")
    if isinstance(exc, ResourceNotFoundError):
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
    if isinstance(exc, ConflictError):
        return _error_response(
            status_code=status.HTTP_409_CONFLICT,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
    if isinstance(exc, ServiceUnavailableError):
        return _error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
    if isinstance(exc, UpstreamTimeoutError):
        return _error_response(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
    if isinstance(exc, DocumentTooLargeError):
        return _error_response(
            status_code=413,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
    if isinstance(exc, UnsupportedDocumentTypeError):
        return _error_response(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
    if isinstance(exc, InvalidDocumentError):
        return _error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
    if isinstance(exc, InvalidQueryError):
        return _error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    logger.error(
        "unmapped_application_error",
        extra={"event_fields": {"path": request.url.path, "error_code": exc.code}},
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="An unexpected application error occurred",
    )


async def handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, DomainError):
        raise TypeError("Domain error handler received an unsupported exception")
    return _error_response(
        status_code=422,
        code="DOMAIN_VALIDATION_ERROR",
        message=str(exc),
    )


async def handle_request_validation_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise TypeError("Validation error handler received an unsupported exception")
    issues = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return _error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details={"issues": issues},
    )


async def handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError("HTTP error handler received an unsupported exception")
    codes = {
        status.HTTP_404_NOT_FOUND: ("NOT_FOUND", "Resource not found"),
        status.HTTP_405_METHOD_NOT_ALLOWED: ("METHOD_NOT_ALLOWED", "Method not allowed"),
    }
    code, message = codes.get(
        exc.status_code,
        ("HTTP_ERROR", "Request could not be completed"),
    )
    return _error_response(status_code=exc.status_code, code=code, message=message)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_request_error",
        extra={"event_fields": {"path": request.url.path}},
    )
    return _error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
    )
