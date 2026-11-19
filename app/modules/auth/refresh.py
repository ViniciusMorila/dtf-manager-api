"""Rotação atômica de refresh tokens de uso único, com revalidação da licença.

O refresh exige a mesma máquina do login e repete a decisão de licença; uma
recusa não consome o token e é lançada depois do commit (EXPIRADA persiste).
"""

from datetime import UTC, datetime
from uuid import UUID

from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth.models import RefreshToken
from app.modules.auth.schemas.input import RefreshRequest
from app.modules.auth.schemas.output import LoginResponse
from app.modules.licenses.service import (
    AccessDecision,
    check_access,
    translate_integrity_error,
)
from app.modules.users.models import User, UserRole
from app.shared.exceptions.authentication import (
    InvalidRefreshToken,
    RefreshFailed,
    RefreshUnavailable,
)
from app.shared.exceptions.license import LicenseError, MachineMismatch
from app.shared.security.jwt import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.shared.security.refresh_token import hash_refresh_token


def authorize_license_refresh(session: Session, user: User, machine_id: str | None, now: datetime) -> AccessDecision:
    return check_access(session, user_id=user.id, is_admin=user.role == UserRole.ADMIN,
                        machine_id=machine_id, now=now)


def refresh_session(data: RefreshRequest, session: Session) -> LoginResponse:
    """Consome o token antigo e confirma o novo digest antes de retornar tokens."""
    refusal: LicenseError | None = None
    result: LoginResponse | None = None
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
            if current.machine_id is not None and data.id_maquina != current.machine_id:
                refusal = MachineMismatch()
            else:
                decision: AccessDecision = authorize_license_refresh(session, user, data.id_maquina, now)
                refusal = decision.error
            if refusal is None:
                current.revoked_at = now
                replacement: str = create_refresh_token(user_id, settings=settings)
                replacement_payload = decode_refresh_token(replacement, settings=settings)
                session.add(RefreshToken(
                    user_id=user_id, token_hash=hash_refresh_token(replacement),
                    expires_at=datetime.fromtimestamp(replacement_payload["exp"], UTC), revoked_at=None,
                    machine_id=current.machine_id,
                ))
                session.flush()
                access: str = create_access_token(user_id, settings=settings)
                result = LoginResponse(
                    access_token=access, refresh_token=replacement,
                    expires_in=settings.jwt_access_expire_minutes * 60,
                    licenca=decision.public, servidor_agora=now,
                )
    except InvalidTokenError:
        raise InvalidRefreshToken() from None
    except IntegrityError as exc:
        license_error: LicenseError | None = translate_integrity_error(exc)
        if license_error is not None:
            raise license_error from None
        raise RefreshFailed() from None
    except OperationalError:
        raise RefreshUnavailable() from None
    except SQLAlchemyError:
        raise RefreshFailed() from None
    except ValueError:
        raise RefreshUnavailable() from None
    if refusal is not None:
        raise refusal
    assert result is not None
    return result
