"""Identidade obtida apenas de JWT access validado, nunca de parâmetros HTTP."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionDependency
from app.modules.subscriptions.schemas.license import LicenseStatus
from app.modules.subscriptions.service import SubscriptionService
from app.modules.users.models import User
from app.shared.exceptions.authentication import AccessUnavailable, InvalidAccessToken
from app.shared.security.jwt import decode_access_token

bearer: HTTPBearer = HTTPBearer(auto_error=False, scheme_name="AccessToken")


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    name: str
    email: str


def get_access_subject(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UUID:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise InvalidAccessToken()
    try:
        return UUID(decode_access_token(credentials.credentials)["sub"])
    except InvalidTokenError:
        raise InvalidAccessToken() from None
    except ValueError:
        raise AccessUnavailable() from None


def get_current_user(
    subject: Annotated[UUID, Depends(get_access_subject)], session: SessionDependency,
) -> AuthenticatedUser:
    """Fecha a leitura antes da transação própria do SubscriptionService."""
    try:
        with session.begin():
            user: User | None = session.scalar(
                select(User).where(User.id == subject).execution_options(populate_existing=True),
            )
            if user is None or not user.is_active:
                raise InvalidAccessToken()
            result: AuthenticatedUser = AuthenticatedUser(id=user.id, name=user.name, email=user.email)
        return result
    except SQLAlchemyError:
        raise AccessUnavailable() from None


CurrentUserDependency = Annotated[AuthenticatedUser, Depends(get_current_user)]


def get_current_license(user: CurrentUserDependency, session: SessionDependency) -> LicenseStatus:
    """Único caminho para a decisão de licença nos endpoints autenticados."""
    return SubscriptionService(session).get_license_status(user.id)


LicenseStatusDependency = Annotated[LicenseStatus, Depends(get_current_license)]
