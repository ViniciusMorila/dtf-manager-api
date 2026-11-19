"""Autenticação da conta seguida da validação da licença do aplicativo.

Tokens só são emitidos quando a licença permite uso nesta máquina. Recusas de
licença são lançadas depois do commit, para persistir a mudança para EXPIRADA.
"""

import secrets
from datetime import UTC, datetime

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth.dependencies import AuthenticatedUser
from app.modules.auth.models import RefreshToken
from app.modules.auth.schemas.input import LoginRequest
from app.modules.auth.schemas.output import LoginResponse
from app.modules.licenses.service import (
    AccessDecision,
    check_access,
    translate_integrity_error,
)
from app.modules.users.models import User, UserRole
from app.shared.exceptions.authentication import (
    AuthenticationError,
    AuthenticationFailed,
    AuthenticationUnavailable,
)
from app.shared.exceptions.license import LicenseError
from app.shared.security.jwt import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.shared.security.password import hash_password, verify_password
from app.shared.security.refresh_token import hash_refresh_token

# Uma verificação Argon2 também ocorre para conta ausente ou inativa.
_DUMMY_PASSWORD_HASH: str = hash_password(secrets.token_urlsafe(32) + "a1")


def verify_credentials(session: Session, email: str, password: SecretStr) -> User:
    """Dentro da transação do chamador; erro genérico sem revelar a existência da conta."""
    user: User | None = session.scalar(
        select(User).where(func.lower(User.email) == email).with_for_update(read=True),
    )
    eligible: bool = user is not None and user.is_active
    stored_hash: str = user.password_hash if eligible and user is not None else _DUMMY_PASSWORD_HASH
    valid: bool = verify_password(password.get_secret_value(), stored_hash)
    if not eligible or not valid or user is None:
        raise AuthenticationError()
    return user


def authenticate_with_password(session: Session, email: str, password: SecretStr) -> AuthenticatedUser:
    """Autenticação sem emitir tokens (ativação de chave com licença expirada)."""
    try:
        with session.begin():
            user: User = verify_credentials(session, email, password)
            result: AuthenticatedUser = AuthenticatedUser(id=user.id, name=user.name, email=user.email,
                                                          role=user.role)
        return result
    except OperationalError:
        raise AuthenticationUnavailable() from None
    except SQLAlchemyError:
        raise AuthenticationFailed() from None


def authorize_license_login(session: Session, user: User, machine_id: str | None, now: datetime) -> AccessDecision:
    return check_access(session, user_id=user.id, is_admin=user.role == UserRole.ADMIN,
                        machine_id=machine_id, now=now)


def login_user(data: LoginRequest, session: Session) -> LoginResponse:
    """Só entrega tokens depois de confirmar a persistência do digest do refresh."""
    refusal: LicenseError | None = None
    response: LoginResponse | None = None
    try:
        with session.begin():
            user: User = verify_credentials(session, str(data.email), data.password)
            now: datetime = datetime.now(UTC)
            decision: AccessDecision = authorize_license_login(session, user, data.id_maquina, now)
            if decision.error is not None:
                refusal = decision.error
            else:
                settings: Settings = Settings()
                access: str = create_access_token(user.id, settings=settings)
                refresh: str = create_refresh_token(user.id, settings=settings)
                refresh_payload = decode_refresh_token(refresh, settings=settings)
                session.add(RefreshToken(
                    user_id=user.id, token_hash=hash_refresh_token(refresh),
                    expires_at=datetime.fromtimestamp(refresh_payload["exp"], UTC), revoked_at=None,
                ))
                session.flush()
                response = LoginResponse(
                    access_token=access, refresh_token=refresh,
                    expires_in=settings.jwt_access_expire_minutes * 60,
                    licenca=decision.public, servidor_agora=now,
                )
    except IntegrityError as exc:
        license_error: LicenseError | None = translate_integrity_error(exc)
        if license_error is not None:
            raise license_error from None
        raise AuthenticationFailed() from None
    except OperationalError:
        raise AuthenticationUnavailable() from None
    except SQLAlchemyError:
        raise AuthenticationFailed() from None
    except ValueError:
        raise AuthenticationUnavailable() from None
    if refusal is not None:
        raise refusal
    assert response is not None
    return response
