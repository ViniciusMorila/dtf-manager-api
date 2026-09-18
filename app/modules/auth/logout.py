"""Revogação idempotente do registro identificado pelo refresh apresentado."""

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.auth.models import RefreshToken
from app.modules.auth.schemas.input import LogoutRequest
from app.modules.auth.schemas.output import LogoutResponse
from app.shared.exceptions.authentication import LogoutFailed, LogoutUnavailable
from app.shared.security.refresh_token import hash_refresh_token


def logout_session(data: LogoutRequest, session: Session) -> LogoutResponse:
    """Atualiza apenas o hash correspondente e preserva uma revogação anterior."""
    digest: str = hash_refresh_token(data.refresh_token.get_secret_value())
    try:
        with session.begin():
            session.execute(
                update(RefreshToken).where(
                    RefreshToken.token_hash == digest,
                    RefreshToken.revoked_at.is_(None),
                ).values(revoked_at=datetime.now(UTC)),
            )
        return LogoutResponse()
    except OperationalError:
        raise LogoutUnavailable() from None
    except SQLAlchemyError:
        raise LogoutFailed() from None
