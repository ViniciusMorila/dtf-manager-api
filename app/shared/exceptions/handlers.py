"""Respostas seguras: nunca devolver input ou ctx de erros Pydantic."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.modules.subscriptions.service import LicenseStatusUnavailable
from app.shared.exceptions.api import ApiError
from app.shared.exceptions.authentication import AuthenticationError
from app.shared.exceptions.license import (
    InvalidActivationKey,
    InvalidPhone,
    MachineIdInvalid,
)
from app.shared.exceptions.registration import (
    InvalidCPF,
    InvalidPlan,
    RegistrationError,
    WeakPassword,
)

_REGISTRATION_FIELDS: dict[str, type[RegistrationError]] = {
    "cpf": InvalidCPF, "plan_code": InvalidPlan, "password": WeakPassword, "senha": WeakPassword,
}
# Campos de licença têm código próprio em qualquer rota.
_LICENSE_FIELDS: dict[str, type[ApiError]] = {
    "telefone": InvalidPhone, "phone": InvalidPhone,
    "id_maquina": MachineIdInvalid, "machine_id": MachineIdInvalid, "x-id-maquina": MachineIdInvalid,
}


async def license_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, LicenseStatusUnavailable):
        raise exc
    return JSONResponse(status_code=503, headers={"Cache-Control": "no-store"}, content={
        "error": {"code": "LICENSE_STATUS_UNAVAILABLE", "message": "Consulta de licença temporariamente indisponível."},
    })


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Formato {"error": {code, message}} e campos extras declarados pelo erro."""
    if not isinstance(exc, ApiError):
        raise exc
    return JSONResponse(status_code=exc.status_code, headers={"Cache-Control": "no-store", **exc.headers},
                        content={"error": {"code": exc.code, "message": exc.message}, **exc.extra})


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
    """Erros de entrada não ecoam senhas, CPFs, tokens, chaves ou campos extras."""
    if not isinstance(exc, RequestValidationError):
        raise exc
    for error in exc.errors():
        location = error.get("loc", ())
        if len(location) != 2 or location[0] not in ("body", "header"):
            continue
        field = location[1]
        if request.url.path == "/auth/register" and field in _REGISTRATION_FIELDS:
            return await registration_error_handler(request, _REGISTRATION_FIELDS[field]())
        if field in _LICENSE_FIELDS:
            return await api_error_handler(request, _LICENSE_FIELDS[field]())
        if request.url.path == "/licencas/ativar-chave" and field == "chave":
            return await api_error_handler(request, InvalidActivationKey())
    return JSONResponse(status_code=422, content={
        "error": {"code": "VALIDATION_ERROR", "message": "Dados de entrada inválidos."},
    })
