"""Logout idempotente e revogação da sessão específica."""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine, get_session
from app.main import create_app
from app.modules.auth.logout import logout_session
from app.modules.auth.models import RefreshToken
from app.modules.auth.refresh import refresh_session
from app.modules.auth.schemas.input import LogoutRequest, RefreshRequest
from app.modules.users.models import User
from app.shared.exceptions.authentication import InvalidRefreshToken
from app.shared.security.jwt import create_refresh_token
from app.shared.security.refresh_token import hash_refresh_token


def post_logout(body: dict[str, str], session: Session) -> Response:
    application = create_app()

    def override_session() -> Session:
        return session

    application.dependency_overrides[get_session] = override_session

    async def post() -> Response:
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
            return await client.post("/auth/logout", json=body)

    return asyncio.run(post())


@pytest.mark.parametrize("affected_rows", [0, 1])
def test_logout_only_returns_ok_and_updates_by_digest(affected_rows: int) -> None:
    session: MagicMock = MagicMock(spec=Session)
    session.execute.return_value.rowcount = affected_rows
    token: str = secrets.token_urlsafe(32)
    response: Response = post_logout({"refresh_token": token}, session)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    statement = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "refresh_tokens.revoked_at IS NULL" in str(statement)
    assert hash_refresh_token(token) in statement.params.values()
    assert token not in statement.params.values()
    assert statement.params["revoked_at"].tzinfo is UTC
    session.begin.assert_called_once()
    session.add.assert_not_called()


def test_repeated_logout_is_safe() -> None:
    session: MagicMock = MagicMock(spec=Session)
    token: str = secrets.token_urlsafe(32)
    for _ in range(3):
        assert post_logout({"refresh_token": token}, session).json() == {"status": "ok"}


@pytest.mark.parametrize("body", [{}, {"refresh_token": ""}, {"refresh_token": "test", "token_hash": "forbidden"}])
def test_invalid_input(body: dict[str, str]) -> None:
    session: MagicMock = MagicMock(spec=Session)
    response: Response = post_logout(body, session)
    assert response.status_code == 422
    assert "forbidden" not in response.text
    session.execute.assert_not_called()


@pytest.mark.parametrize("failure", ["execute", "commit", "operational"])
def test_logout_failure_does_not_report_success(failure: str) -> None:
    session: MagicMock = MagicMock(spec=Session)
    if failure == "commit":
        session.begin.return_value.__exit__.side_effect = SQLAlchemyError("internal")
    elif failure == "operational":
        session.execute.side_effect = OperationalError("internal", {}, Exception())
    else:
        session.execute.side_effect = SQLAlchemyError("internal")
    token: str = secrets.token_urlsafe(32)
    response: Response = post_logout({"refresh_token": token}, session)
    assert response.status_code == (503 if failure == "operational" else 500)
    assert token not in response.text
    assert "internal" not in response.text
    assert "ok" not in response.json()


def test_postgresql_logout_idempotent_and_other_session_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: logout real não verificado")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))
    user_id = uuid4()
    token: str = create_refresh_token(user_id)
    other: str = create_refresh_token(user_id)
    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            connection.execute(User.__table__.insert().values(id=user_id, name="Logout teste",
                email=f"{user_id.hex}@example.com", cpf=f"{secrets.randbelow(10**11):011d}",
                password_hash=secrets.token_hex(32), is_active=True))
            for value in (token, other):
                connection.execute(RefreshToken.__table__.insert().values(user_id=user_id,
                    token_hash=hash_refresh_token(value), expires_at=datetime.now(UTC) + timedelta(days=1)))
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as session:
                assert logout_session(LogoutRequest(refresh_token=token), session).status == "ok"
                with session.begin():
                    timestamp = session.scalar(select(RefreshToken.revoked_at).where(RefreshToken.token_hash == hash_refresh_token(token)))
                assert timestamp is not None
                assert logout_session(LogoutRequest(refresh_token=token), session).status == "ok"
                with pytest.raises(InvalidRefreshToken):
                    refresh_session(RefreshRequest(refresh_token=token), session)
                with session.begin():
                    records = session.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id)).all()
                    assert next(row for row in records if row.token_hash == hash_refresh_token(token)).revoked_at == timestamp
                    assert next(row for row in records if row.token_hash == hash_refresh_token(other)).revoked_at is None
        finally:
            outer.rollback()
