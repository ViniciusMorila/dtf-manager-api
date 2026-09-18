"""Validação criptográfica com chaves efêmeras, sem banco ou espera temporal."""

import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import jwt
import pytest
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidAlgorithmError,
    InvalidSignatureError,
    InvalidTokenError,
)
from pydantic import SecretStr

from app.core.config import Settings
from app.shared.security.jwt import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_ACCESS_EXPIRE_MINUTES", "7")
    monkeypatch.setenv("JWT_REFRESH_EXPIRE_DAYS", "12")
    return Settings()


@pytest.mark.parametrize("kind", ["access", "refresh"])
def test_valid_minimal_payload_and_configured_lifetime(settings: Settings, kind: str) -> None:
    subject: UUID = uuid4()
    create = create_access_token if kind == "access" else create_refresh_token
    decode = decode_access_token if kind == "access" else decode_refresh_token
    token: str = create(subject, settings=settings)
    payload: TokenPayload = decode(token, settings=settings)
    assert set(payload) == {"sub", "type", "jti", "iat", "exp"}
    assert payload["sub"] == str(subject)
    assert payload["type"] == kind
    assert UUID(payload["jti"]).version == 4
    assert payload["exp"] - payload["iat"] == (7 * 60 if kind == "access" else 12 * 86400)
    assert decode(create(subject, settings=settings), settings=settings)["jti"] != payload["jti"]
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


@pytest.mark.parametrize("kind", ["access", "refresh"])
@pytest.mark.parametrize("problem", ["expired", "wrong_signature", "wrong_type", "missing_exp", "bad_sub", "bad_jti", "extra_claim", "wrong_algorithm"])
def test_rejects_invalid_tokens(settings: Settings, kind: str, problem: str) -> None:
    now: int = int(datetime.now(UTC).timestamp())
    payload: dict[str, str | int] = {"sub": str(uuid4()), "type": kind, "jti": str(uuid4()), "iat": now - 60, "exp": now + 60}
    secret = settings.jwt_access_secret if kind == "access" else settings.jwt_refresh_secret
    assert secret is not None
    key: str = secret.get_secret_value()
    algorithm: str = "HS256"
    expected: type[InvalidTokenError] = InvalidTokenError
    if problem == "expired":
        payload["exp"] = now - 1
        expected = ExpiredSignatureError
    elif problem == "wrong_signature":
        key = secrets.token_hex(32)
        expected = InvalidSignatureError
    elif problem == "wrong_type":
        payload["type"] = "refresh" if kind == "access" else "access"
    elif problem == "missing_exp":
        del payload["exp"]
    elif problem == "bad_sub":
        payload["sub"] = "not-a-uuid"
    elif problem == "bad_jti":
        payload["jti"] = "not-a-uuid"
    elif problem == "extra_claim":
        payload["plan"] = "LIFETIME"
    elif problem == "wrong_algorithm":
        algorithm = "HS512"
        expected = InvalidAlgorithmError
    token: str = jwt.encode(payload, key, algorithm=algorithm)
    decode = decode_access_token if kind == "access" else decode_refresh_token
    with pytest.raises(expected):
        decode(token, settings=settings)


def test_access_and_refresh_are_not_interchangeable(settings: Settings) -> None:
    with pytest.raises(InvalidSignatureError):
        decode_access_token(create_refresh_token(uuid4(), settings=settings), settings=settings)
    with pytest.raises(InvalidSignatureError):
        decode_refresh_token(create_access_token(uuid4(), settings=settings), settings=settings)


@pytest.mark.parametrize("problem", ["missing", "empty", "example", "same"])
def test_requires_distinct_secure_secrets(settings: Settings, problem: str) -> None:
    if problem == "missing":
        settings.jwt_refresh_secret = None
    elif problem == "empty":
        settings.jwt_access_secret = SecretStr("")
    elif problem == "example":
        settings.jwt_access_secret = SecretStr("trocar")
    else:
        settings.jwt_refresh_secret = settings.jwt_access_secret
    with pytest.raises(ValueError):
        create_access_token(uuid4(), settings=settings)
    with pytest.raises(ValueError):
        decode_refresh_token("invalid", settings=settings)


def test_default_functions_read_settings(settings: Settings) -> None:
    subject: UUID = uuid4()
    assert decode_access_token(create_access_token(subject))["sub"] == str(subject)
    assert decode_refresh_token(create_refresh_token(subject))["sub"] == str(subject)
