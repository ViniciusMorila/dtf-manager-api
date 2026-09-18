"""Endpoints de cadastro e sessão, sem autorização de licença."""

from fastapi import APIRouter, Response, status

from app.db.session import SessionDependency
from app.modules.auth.login import login_user
from app.modules.auth.logout import logout_session
from app.modules.auth.refresh import refresh_session
from app.modules.auth.registration import register_user
from app.modules.auth.schemas.input import LoginRequest, LogoutRequest, RefreshRequest
from app.modules.auth.schemas.output import LogoutResponse, TokenResponse
from app.modules.auth.schemas.registration import (
    RegistrationErrorResponse,
    RegistrationResponse,
)
from app.modules.users.schemas.input import UserCreate

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=RegistrationResponse, status_code=status.HTTP_201_CREATED,
    responses={code: {"model": RegistrationErrorResponse} for code in (409, 422, 500, 503)},
)
def register(data: UserCreate, session: SessionDependency) -> RegistrationResponse:
    return register_user(data, session)


@router.post(
    "/login", response_model=TokenResponse,
    responses={code: {"model": RegistrationErrorResponse} for code in (401, 422, 500, 503)},
)
def login(data: LoginRequest, session: SessionDependency, response: Response) -> TokenResponse:
    result: TokenResponse = login_user(data, session)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result


@router.post(
    "/refresh", response_model=TokenResponse,
    responses={code: {"model": RegistrationErrorResponse} for code in (401, 422, 500, 503)},
)
def refresh(data: RefreshRequest, session: SessionDependency, response: Response) -> TokenResponse:
    result: TokenResponse = refresh_session(data, session)
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
