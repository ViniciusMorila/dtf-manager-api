"""Rotação atômica de refresh tokens de uso único."""

from datetime import UTC, datetime
from uuid import UUID

from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth.models import RefreshToken
from app.modules.auth.schemas.input import RefreshRequest
from app.modules.auth.schemas.output import TokenResponse
from app.modules.users.models import User
from app.shared.exceptions.authentication import (
    InvalidRefreshToken,
    RefreshFailed,
    RefreshUnavailable,
)
from app.shared.security.jwt import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.shared.security.refresh_token import hash_refresh_token


def refresh_session(data: RefreshRequest, session: Session) -> TokenResponse:
    """Consome o token antigo e confirma o novo digest antes de retornar tokens."""
    try:
        settings: Settings = Settings()
        token: str = data.refresh_token.get_secret_value()
        payload = decode_refresh_token(token, settings=settings)
        user_id: UUID = UUID(payload["sub"])
        with session.begin():
            user: User | None = session.scalar(
                select(User).where(User.id == user_id).with_for_update(read=True)
                .execution_options(populate_existing=True),
            )
            if user is None or not user.is_active:
                raise InvalidRefreshToken()
            current: RefreshToken | None = session.scalar(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(token),
                    RefreshToken.user_id == user_id,
                ).with_for_update().execution_options(populate_existing=True),
            )
            # Reavalia o prazo depois de aguardar locks, usando a hora atual.
            now: datetime = datetime.now(UTC)
            if (
                current is None or current.expires_at <= now or current.revoked_at is not None
                or payload["exp"] <= now.timestamp()
            ):
                raise InvalidRefreshToken()
            current.revoked_at = now
            replacement: str = create_refresh_token(user_id, settings=settings)
            replacement_payload = decode_refresh_token(replacement, settings=settings)
            session.add(RefreshToken(
                user_id=user_id, token_hash=hash_refresh_token(replacement),
                expires_at=datetime.fromtimestamp(replacement_payload["exp"], UTC), revoked_at=None,
            ))
            session.flush()
            access: str = create_access_token(user_id, settings=settings)
            result: TokenResponse = TokenResponse(
                access_token=access, refresh_token=replacement,
                expires_in=settings.jwt_access_expire_minutes * 60,
            )
        return result
    except InvalidTokenError:
        raise InvalidRefreshToken() from None
    except OperationalError:
        raise RefreshUnavailable() from None
    except SQLAlchemyError:
        raise RefreshFailed() from None
    except ValueError:
        raise RefreshUnavailable() from None
