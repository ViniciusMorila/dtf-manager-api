"""Login HTTP e persistência de digest, sem conceder autorização de licença."""

import asyncio
import secrets
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine, get_session
from app.main import create_app
from app.modules.auth.login import login_user
from app.modules.auth.models import RefreshToken
from app.modules.auth.schemas.input import LoginRequest
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.users.models import User
from app.shared.security.jwt import decode_access_token, decode_refresh_token
from app.shared.security.password import hash_password


@pytest.fixture
def jwt_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_ACCESS_EXPIRE_MINUTES", "9")
    monkeypatch.setenv("JWT_REFRESH_EXPIRE_DAYS", "4")
    return Settings()


@pytest.fixture
def credentials() -> dict[str, str]:
    return {"email": "ANA@EXAMPLE.COM", "password": secrets.token_urlsafe(24) + "a1"}


@pytest.fixture
def database_session(credentials: dict[str, str]) -> MagicMock:
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.return_value = User(id=uuid4(), email="ana@example.com", is_active=True,
                                        password_hash=hash_password(credentials["password"]))
    return session


def post_login(credentials: dict[str, str], session: Session) -> Response:
    application: FastAPI = create_app()

    def override_session() -> Session:
        return session

    application.dependency_overrides[get_session] = override_session

    async def post() -> Response:
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
            return await client.post("/auth/login", json=credentials)

    return asyncio.run(post())


def test_login_tokens_and_only_refresh_digest_persisted(
    jwt_settings: Settings, credentials: dict[str, str], database_session: MagicMock,
) -> None:
    assert LoginRequest.model_validate(credentials).email == "ana@example.com"
    response: Response = post_login(credentials, database_session)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert result["expires_in"] == 540
    assert result["token_type"] == "bearer"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    access = decode_access_token(result["access_token"], settings=jwt_settings)
    refresh = decode_refresh_token(result["refresh_token"], settings=jwt_settings)
    assert access["sub"] == refresh["sub"] == str(database_session.scalar.return_value.id)
    assert refresh["exp"] - refresh["iat"] == 4 * 86400
    stored: RefreshToken = database_session.add.call_args.args[0]
    assert stored.token_hash == sha256(result["refresh_token"].encode()).hexdigest()
    assert stored.token_hash != result["refresh_token"]
    assert stored.expires_at == datetime.fromtimestamp(refresh["exp"], UTC)
    assert stored.revoked_at is None
    assert result["refresh_token"] not in vars(stored).values()
    database_session.begin.assert_called_once()
    database_session.scalar.assert_called_once()
    assert credentials["password"] not in response.text
    assert database_session.scalar.return_value.password_hash not in response.text


@pytest.mark.parametrize("state", ["missing", "wrong_password", "inactive", "bad_hash"])
def test_invalid_credentials_are_generic(
    jwt_settings: Settings, credentials: dict[str, str], database_session: MagicMock, state: str,
) -> None:
    if state == "missing":
        database_session.scalar.return_value = None
    elif state == "wrong_password":
        credentials["password"] += "wrong"
    elif state == "inactive":
        database_session.scalar.return_value.is_active = False
    else:
        database_session.scalar.return_value.password_hash = "invalid"
    response: Response = post_login(credentials, database_session)
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "INVALID_CREDENTIALS", "message": "Email ou senha inválidos."}}
    assert response.headers["www-authenticate"] == "Bearer"
    database_session.add.assert_not_called()


@pytest.mark.parametrize("status", [SubscriptionStatus.PENDING, SubscriptionStatus.EXPIRED,
                                  SubscriptionStatus.SUSPENDED, SubscriptionStatus.CANCELLED])
def test_subscription_status_does_not_prevent_login_or_change_license(
    jwt_settings: Settings, credentials: dict[str, str], database_session: MagicMock, status: SubscriptionStatus,
) -> None:
    subscription: Subscription = Subscription(status=status)
    database_session.scalar.return_value.subscriptions = [subscription]
    response: Response = post_login(credentials, database_session)
    assert response.status_code == 200
    assert subscription.status == status
    database_session.scalar.assert_called_once()
    assert set(decode_access_token(response.json()["access_token"], settings=jwt_settings)) == {"sub", "type", "jti", "iat", "exp"}
    assert isinstance(database_session.add.call_args.args[0], RefreshToken)


@pytest.mark.parametrize("failure", ["flush", "commit"])
def test_persistence_failure_never_returns_tokens(
    jwt_settings: Settings, credentials: dict[str, str], database_session: MagicMock, failure: str,
) -> None:
    if failure == "flush":
        database_session.flush.side_effect = SQLAlchemyError("private SQL detail")
    else:
        database_session.begin.return_value.__exit__.side_effect = SQLAlchemyError("private SQL detail")
    response: Response = post_login(credentials, database_session)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    assert "token" not in response.text
    assert "private SQL detail" not in response.text


def test_repeated_logins_have_distinct_refresh_tokens(
    jwt_settings: Settings, credentials: dict[str, str], database_session: MagicMock,
) -> None:
    first: Response = post_login(credentials, database_session)
    second: Response = post_login(credentials, database_session)
    assert first.json()["refresh_token"] != second.json()["refresh_token"]
    assert database_session.add.call_args_list[0].args[0].token_hash != database_session.add.call_args_list[1].args[0].token_hash


def test_validation_does_not_echo_password(
    jwt_settings: Settings, credentials: dict[str, str], database_session: MagicMock,
) -> None:
    credentials["email"] = "invalid"
    response: Response = post_login(credentials, database_session)
    assert response.status_code == 422
    assert credentials["password"] not in response.text
    database_session.begin.assert_not_called()


def test_login_postgresql_persists_digest_only(monkeypatch: pytest.MonkeyPatch) -> None:
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: persistência real do refresh não verificada")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))
    password: str = secrets.token_urlsafe(24) + "a1"
    user_id = uuid4()
    email: str = f"{user_id.hex}@example.com"
    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            connection.execute(User.__table__.insert().values(
                id=user_id, name="Login teste", cpf=f"{secrets.randbelow(10**11):011d}",
                email=email, password_hash=hash_password(password), is_active=True,
            ))
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as session:
                result = login_user(LoginRequest(email=email, password=password), session)
                stored: RefreshToken = session.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id)).one()
                assert stored.token_hash == sha256(result.refresh_token.encode()).hexdigest()
                assert result.refresh_token not in vars(stored).values()
                assert stored.revoked_at is None
        finally:
            outer.rollback()
