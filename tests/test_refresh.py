"""Rotação HTTP, rejeição de reuso e rollback PostgreSQL quando disponível."""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine, get_session
from app.main import create_app
from app.modules.auth.models import RefreshToken
from app.modules.auth.refresh import refresh_session
from app.modules.auth.schemas.input import RefreshRequest
from app.modules.users.models import User
from app.shared.exceptions.authentication import InvalidRefreshToken, RefreshFailed
from app.shared.security.jwt import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from app.shared.security.refresh_token import hash_refresh_token


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))
    return Settings()


@pytest.fixture
def token(settings: Settings) -> str:
    return create_refresh_token(uuid4(), settings=settings)


@pytest.fixture
def database_session(settings: Settings, token: str) -> MagicMock:
    from uuid import UUID

    payload = decode_refresh_token(token, settings=settings)
    user: User = User(id=UUID(payload["sub"]), is_active=True)
    stored: RefreshToken = RefreshToken(id=uuid4(), user_id=user.id, token_hash=hash_refresh_token(token),
        expires_at=datetime.fromtimestamp(payload["exp"], UTC), revoked_at=None)
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [user, stored]
    return session


def post_refresh(token: str, session: Session) -> Response:
    application = create_app()

    def override_session() -> Session:
        return session

    application.dependency_overrides[get_session] = override_session

    async def post() -> Response:
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
            return await client.post("/auth/refresh", json={"refresh_token": token})

    return asyncio.run(post())


def test_valid_rotation_and_reuse(settings: Settings, token: str, database_session: MagicMock) -> None:
    user, stored = list(database_session.scalar.side_effect)
    database_session.scalar.side_effect = [user, stored]
    response: Response = post_refresh(token, database_session)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert result["refresh_token"] != token
    assert result["token_type"] == "bearer"
    assert result["expires_in"] == settings.jwt_access_expire_minutes * 60
    assert response.headers["cache-control"] == "no-store"
    assert stored.revoked_at is not None
    replacement: RefreshToken = database_session.add.call_args.args[0]
    assert replacement.token_hash == hash_refresh_token(result["refresh_token"])
    assert result["refresh_token"] not in vars(replacement).values()
    claims = decode_refresh_token(result["refresh_token"], settings=settings)
    assert replacement.expires_at == datetime.fromtimestamp(claims["exp"], UTC)
    assert decode_access_token(result["access_token"], settings=settings)["sub"] == str(user.id)
    locked_query = database_session.scalar.call_args_list[1].args[0]
    assert "FOR UPDATE" in str(locked_query.compile(dialect=postgresql.dialect()))
    assert locked_query.get_execution_options()["populate_existing"] is True
    database_session.scalar.side_effect = [user, stored]
    reused: Response = post_refresh(token, database_session)
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"
    assert database_session.add.call_count == 1


@pytest.mark.parametrize("problem", ["expired_jwt", "tampered", "access", "malformed"])
def test_invalid_jwt_never_queries_database(settings: Settings, token: str, database_session: MagicMock, problem: str) -> None:
    claims = decode_refresh_token(token, settings=settings)
    assert settings.jwt_refresh_secret is not None
    if problem == "expired_jwt":
        claims["iat"] = int(datetime.now(UTC).timestamp()) - 120
        claims["exp"] = claims["iat"] + 60
        token = jwt.encode(claims, settings.jwt_refresh_secret.get_secret_value(), algorithm="HS256")
    elif problem == "tampered":
        claims["sub"] = str(uuid4())
        token = jwt.encode(claims, secrets.token_hex(32), algorithm="HS256")
    elif problem == "access":
        token = create_access_token(uuid4(), settings=settings)
    else:
        token = "invalid.jwt"
    response: Response = post_refresh(token, database_session)
    assert response.status_code == 401
    assert token not in response.text
    database_session.begin.assert_not_called()
    database_session.scalar.assert_not_called()


@pytest.mark.parametrize("problem", ["expired_record", "revoked", "missing_record", "inactive_user", "missing_user"])
def test_invalid_database_state(settings: Settings, token: str, database_session: MagicMock, problem: str) -> None:
    user, stored = list(database_session.scalar.side_effect)
    if problem == "expired_record":
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif problem == "revoked":
        stored.revoked_at = datetime.now(UTC)
    elif problem == "missing_record":
        stored = None
    elif problem == "inactive_user":
        user.is_active = False
    else:
        user = None
    database_session.scalar.side_effect = [user, stored]
    response: Response = post_refresh(token, database_session)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"
    database_session.add.assert_not_called()


@pytest.mark.parametrize("failure", ["flush", "commit"])
def test_transaction_failure_does_not_deliver_tokens(settings: Settings, token: str, database_session: MagicMock, failure: str) -> None:
    if failure == "flush":
        database_session.flush.side_effect = SQLAlchemyError("internal")
    else:
        database_session.begin.return_value.__exit__.side_effect = SQLAlchemyError("internal")
    response: Response = post_refresh(token, database_session)
    assert response.status_code == 500
    assert "access_token" not in response.text
    assert token not in response.text
    assert "internal" not in response.text


@pytest.mark.parametrize("fail_insert", [False, True])
def test_postgresql_rotation_and_rollback(monkeypatch: pytest.MonkeyPatch, fail_insert: bool) -> None:
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: rotação/rollback real não verificados")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))
    user_id = uuid4()
    token: str = create_refresh_token(user_id)
    claims = decode_refresh_token(token)

    def fail(mapper: object, connection: object, target: RefreshToken) -> None:
        raise SQLAlchemyError("forced failure")

    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            connection.execute(User.__table__.insert().values(id=user_id, name="Refresh teste",
                email=f"{user_id.hex}@example.com", cpf=f"{secrets.randbelow(10**11):011d}",
                password_hash=secrets.token_hex(32), is_active=True))
            connection.execute(RefreshToken.__table__.insert().values(user_id=user_id,
                token_hash=hash_refresh_token(token), expires_at=datetime.fromtimestamp(claims["exp"], UTC)))
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as session:
                if fail_insert:
                    event.listen(RefreshToken, "before_insert", fail)
                    try:
                        with pytest.raises(RefreshFailed):
                            refresh_session(RefreshRequest(refresh_token=token), session)
                    finally:
                        event.remove(RefreshToken, "before_insert", fail)
                else:
                    result = refresh_session(RefreshRequest(refresh_token=token), session)
                    with pytest.raises(InvalidRefreshToken):
                        refresh_session(RefreshRequest(refresh_token=token), session)
                    assert result.refresh_token != token
                rows = session.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id)).all()
                assert len(rows) == (1 if fail_insert else 2)
                original = next(row for row in rows if row.token_hash == hash_refresh_token(token))
                assert (original.revoked_at is None) is fail_insert
        finally:
            outer.rollback()
