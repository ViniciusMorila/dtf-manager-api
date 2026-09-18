"""Respostas seguras: nunca devolver input ou ctx de erros Pydantic."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.modules.subscriptions.service import LicenseStatusUnavailable
from app.shared.exceptions.authentication import AuthenticationError
from app.shared.exceptions.registration import (
    InvalidCPF,
    InvalidPlan,
    RegistrationError,
    WeakPassword,
)


async def license_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, LicenseStatusUnavailable):
        raise exc
    return JSONResponse(status_code=503, headers={"Cache-Control": "no-store"}, content={
        "error": {"code": "LICENSE_STATUS_UNAVAILABLE", "message": "Consulta de licença temporariamente indisponível."},
    })


async def authentication_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AuthenticationError):
        raise exc
    headers: dict[str, str] = {"Cache-Control": "no-store"}
    if exc.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(status_code=exc.status_code, headers=headers,
                        content={"error": {"code": exc.code, "message": exc.message}})


async def registration_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Converte apenas as mensagens públicas dos erros de domínio."""
    if not isinstance(exc, RegistrationError):
        raise exc
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Erros de entrada não ecoam senhas, CPFs, tokens ou campos extras."""
    if not isinstance(exc, RequestValidationError):
        raise exc
    if request.url.path == "/auth/register":
        mapping: dict[str, type[RegistrationError]] = {
            "cpf": InvalidCPF, "plan_code": InvalidPlan, "password": WeakPassword,
        }
        for error in exc.errors():
            location = error.get("loc", ())
            if len(location) == 2 and location[0] == "body" and location[1] in mapping:
                return await registration_error_handler(request, mapping[location[1]]())
    return JSONResponse(status_code=422, content={
        "error": {"code": "VALIDATION_ERROR", "message": "Dados de entrada inválidos."},
    })
