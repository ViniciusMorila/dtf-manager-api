"""Autenticação da conta, independente do estado de qualquer assinatura."""

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth.models import RefreshToken
from app.modules.auth.schemas.input import LoginRequest
from app.modules.auth.schemas.output import TokenResponse
from app.modules.users.models import User
from app.shared.exceptions.authentication import (
    AuthenticationError,
    AuthenticationFailed,
    AuthenticationUnavailable,
)
from app.shared.security.jwt import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.shared.security.password import hash_password, verify_password
from app.shared.security.refresh_token import hash_refresh_token

# Uma verificação Argon2 também ocorre para conta ausente ou inativa.
_DUMMY_PASSWORD_HASH: str = hash_password(secrets.token_urlsafe(32) + "a1")


def login_user(data: LoginRequest, session: Session) -> TokenResponse:
    """Só entrega tokens depois de confirmar a persistência do digest do refresh."""
    try:
        with session.begin():
            user: User | None = session.scalar(
                select(User).where(func.lower(User.email) == str(data.email)).with_for_update(read=True),
            )
            eligible: bool = user is not None and user.is_active
            stored_hash: str = user.password_hash if eligible and user is not None else _DUMMY_PASSWORD_HASH
            valid: bool = verify_password(data.password.get_secret_value(), stored_hash)
            if not eligible or not valid or user is None:
                raise AuthenticationError()

            settings: Settings = Settings()
            access: str = create_access_token(user.id, settings=settings)
            refresh: str = create_refresh_token(user.id, settings=settings)
            refresh_payload = decode_refresh_token(refresh, settings=settings)
            session.add(RefreshToken(
                user_id=user.id, token_hash=hash_refresh_token(refresh),
                expires_at=datetime.fromtimestamp(refresh_payload["exp"], UTC), revoked_at=None,
            ))
            session.flush()
            response: TokenResponse = TokenResponse(
                access_token=access, refresh_token=refresh,
                expires_in=settings.jwt_access_expire_minutes * 60,
            )
        return response
    except OperationalError:
        raise AuthenticationUnavailable() from None
    except SQLAlchemyError:
        raise AuthenticationFailed() from None
    except ValueError:
        raise AuthenticationUnavailable() from None
