"""Endpoints de cadastro e sessão; login também valida a licença do aplicativo."""

from fastapi import APIRouter, Depends, Response, status

from app.db.session import SessionDependency
from app.modules.auth.login import login_user
from app.modules.auth.logout import logout_session
from app.modules.auth.refresh import refresh_session
from app.modules.auth.registration import register_user
from app.modules.auth.schemas.input import LoginRequest, LogoutRequest, RefreshRequest
from app.modules.auth.schemas.output import LoginResponse, LogoutResponse
from app.modules.auth.schemas.registration import (
    RegistrationErrorResponse,
    RegistrationResponse,
)
from app.modules.users.schemas.input import UserCreate
from app.shared.exceptions.license import InvalidActivationKey
from app.shared.security.rate_limit import (
    ensure_account_not_limited,
    limit_registration_email,
    rate_limit,
    record_password_failure,
)

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=RegistrationResponse, status_code=status.HTTP_201_CREATED,
    responses={code: {"model": RegistrationErrorResponse} for code in (409, 422, 429, 500, 503)},
    dependencies=[Depends(rate_limit("auth_register", strict=True))],
)
def register(data: UserCreate, session: SessionDependency) -> RegistrationResponse:
    limit_registration_email(str(data.email))
    if data.activation_key is None:
        return register_user(data, session)
    # Chaves erradas contam no mesmo limite por conta das senhas erradas (login e ativar-chave).
    ensure_account_not_limited(str(data.email))
    try:
        return register_user(data, session)
    except InvalidActivationKey:
        record_password_failure(str(data.email))
        raise


@router.post(
    "/login", response_model=LoginResponse,
    responses={code: {"model": RegistrationErrorResponse} for code in (401, 403, 404, 409, 422, 429, 500, 503)},
    dependencies=[Depends(rate_limit("auth_login"))],
)
def login(data: LoginRequest, session: SessionDependency, response: Response) -> LoginResponse:
    result: LoginResponse = login_user(data, session)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result


@router.post(
    "/refresh", response_model=LoginResponse,
    responses={code: {"model": RegistrationErrorResponse} for code in (401, 403, 404, 409, 422, 500, 503)},
)
def refresh(data: RefreshRequest, session: SessionDependency, response: Response) -> LoginResponse:
    result: LoginResponse = refresh_session(data, session)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result


@router.post(
    "/logout", response_model=LogoutResponse,
    responses={code: {"model": RegistrationErrorResponse} for code in (422, 500, 503)},
)
def logout(data: LogoutRequest, session: SessionDependency, response: Response) -> LogoutResponse:
    result: LogoutResponse = logout_session(data, session)
    response.headers["Cache-Control"] = "no-store"
    return result
