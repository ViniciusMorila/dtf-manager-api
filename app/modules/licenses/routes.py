"""Consulta da assinatura (/license) e licença do aplicativo desktop (/licencas)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.db.session import SessionDependency
from app.modules.auth.dependencies import (
    AuthenticatedUser,
    CurrentUserDependency,
    LicenseStatusDependency,
    bearer,
    get_access_subject,
    get_current_user,
)
from app.modules.auth.login import authenticate_with_password
from app.modules.auth.schemas.registration import RegistrationErrorResponse
from app.modules.licenses.schemas.contract import (
    AtivarChaveRequest,
    AtivarChaveResponse,
    LicencaStatusResponse,
)
from app.modules.licenses.service import license_status, redeem_key
from app.modules.subscriptions.schemas.license import LicenseStatus
from app.shared.exceptions.authentication import InvalidAccessToken
from app.shared.security.rate_limit import rate_limit
from app.shared.utils.machine import MachineId

router: APIRouter = APIRouter(prefix="/license", tags=["license"])
licencas_router: APIRouter = APIRouter(prefix="/licencas", tags=["licencas"])
_ERRORS = {code: {"model": RegistrationErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)}


@router.get("/status", response_model=LicenseStatus)
def license_status_route(license: LicenseStatusDependency, response: Response) -> LicenseStatus:
    response.headers["Cache-Control"] = "no-store"
    return license


@licencas_router.get("/status", response_model=LicencaStatusResponse, responses=_ERRORS)
def licenca_status(
    user: CurrentUserDependency, session: SessionDependency, response: Response,
    id_maquina: Annotated[MachineId | None, Header(alias="X-Id-Maquina")] = None,
) -> LicencaStatusResponse:
    """Chamado a cada abertura do aplicativo; recusa com código estável quando não pode usar."""
    response.headers["Cache-Control"] = "no-store"
    return license_status(session, user_id=user.id, is_admin=user.is_admin, machine_id=id_maquina)


@licencas_router.post("/ativar-chave", response_model=AtivarChaveResponse, responses=_ERRORS,
                      dependencies=[Depends(rate_limit("licencas_ativar_chave"))])
def ativar_chave(
    data: AtivarChaveRequest, session: SessionDependency, response: Response,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AtivarChaveResponse:
    """Bearer normalmente; email+senha quando o login recusou por licença expirada ou ausente."""
    response.headers["Cache-Control"] = "no-store"
    if credentials is not None:
        user: AuthenticatedUser = get_current_user(get_access_subject(credentials), session)
    elif data.email is not None and data.senha is not None:
        user = authenticate_with_password(session, data.email, data.senha)
    else:
        raise InvalidAccessToken()
    return redeem_key(session, user_id=user.id, is_admin=user.is_admin, raw_key=data.chave,
                      machine_id=data.id_maquina)
