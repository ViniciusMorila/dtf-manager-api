"""JWT mínimo com PyJWT; autenticação não comprova validade de licença."""

from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict, cast
from uuid import UUID, uuid4

import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import Settings

TokenType = Literal["access", "refresh"]
_ALGORITHM: str = "HS256"
_CLAIMS: tuple[str, ...] = ("sub", "type", "jti", "iat", "exp")


class TokenPayload(TypedDict):
    sub: str
    type: TokenType
    jti: str
    iat: int
    exp: int


def _secret(settings: Settings, token_type: TokenType) -> str:
    settings.validate_jwt_secrets()
    secret = settings.jwt_access_secret if token_type == "access" else settings.jwt_refresh_secret
    assert secret is not None  # Garantido pela validação centralizada acima.
    return secret.get_secret_value()


def _create_token(user_id: UUID, token_type: TokenType, settings: Settings) -> str:
    key: str = _secret(settings, token_type)
    subject: str = str(UUID(str(user_id)))
    now: datetime = datetime.now(UTC)
    duration: timedelta = (
        timedelta(minutes=settings.jwt_access_expire_minutes) if token_type == "access"
        else timedelta(days=settings.jwt_refresh_expire_days)
    )
    payload: TokenPayload = {
        "sub": subject, "type": token_type, "jti": str(uuid4()),
        "iat": int(now.timestamp()), "exp": int((now + duration).timestamp()),
    }
    return jwt.encode(payload, key, algorithm=_ALGORITHM)


def _decode_token(token: str, token_type: TokenType, settings: Settings) -> TokenPayload:
    payload = jwt.decode(
        token, _secret(settings, token_type), algorithms=[_ALGORITHM],
        options={"require": list(_CLAIMS)},
    )
    if set(payload) != set(_CLAIMS) or payload["type"] != token_type:
        raise InvalidTokenError("Payload ou tipo de token inválido.")
    for claim in ("sub", "jti"):
        try:
            if not isinstance(payload[claim], str):
                raise ValueError  # noqa: TRY004 - normalized below to InvalidTokenError
            UUID(payload[claim])
        except ValueError:
            raise InvalidTokenError("Identificador de token inválido.") from None
    if (
        type(payload["iat"]) is not int or type(payload["exp"]) is not int
        or payload["exp"] <= payload["iat"]
    ):
        raise InvalidTokenError("Datas de token inválidas.")
    return cast(TokenPayload, payload)


def create_access_token(user_id: UUID, *, settings: Settings | None = None) -> str:
    """Emite access com duração JWT_ACCESS_EXPIRE_MINUTES e novo jti."""
    return _create_token(user_id, "access", settings if settings is not None else Settings())


def create_refresh_token(user_id: UUID, *, settings: Settings | None = None) -> str:
    """Emite refresh com duração JWT_REFRESH_EXPIRE_DAYS e novo jti."""
    return _create_token(user_id, "refresh", settings if settings is not None else Settings())


def decode_access_token(token: str, *, settings: Settings | None = None) -> TokenPayload:
    """Valida assinatura, expiração, claims obrigatórios e tipo access."""
    return _decode_token(token, "access", settings if settings is not None else Settings())


def decode_refresh_token(token: str, *, settings: Settings | None = None) -> TokenPayload:
    """Valida tipo refresh; não consulta revogação ou licença no banco."""
    return _decode_token(token, "refresh", settings if settings is not None else Settings())
