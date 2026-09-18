"""Autenticação Bearer, isolamento de identidade e projeções públicas."""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine, get_session
from app.main import create_app
from app.modules.plans.models import Plan, PlanCode
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.users.models import User
from app.shared.security.jwt import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
)
from scripts.seed_plans import seed_plans


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))
    return Settings()


@pytest.fixture
def session() -> MagicMock:
    db: MagicMock = MagicMock(spec=Session)
    user: User = User(id=uuid4(), name="Ana", email="ana@example.com", is_active=True,
                      cpf="12345678909", password_hash=secrets.token_hex(32))
    plan: Plan = Plan(id=uuid4(), code=PlanCode.ANNUAL, name="1 ano", is_lifetime=False)
    subscription: Subscription = Subscription(id=uuid4(), user_id=user.id, plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE, starts_at=datetime.now(UTC) - timedelta(days=1),
        expires_at=datetime.now(UTC) + timedelta(days=1))
    db.scalar.return_value = user
    db.execute.return_value.all.return_value = [(subscription, plan)]
    return db


def get_account(path: str, session: Session, authorization: str | None = None, injected_id: str | None = None) -> Response:
    application = create_app()

    def override() -> Session:
        return session

    application.dependency_overrides[get_session] = override

    async def get() -> Response:
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
            return await client.request("GET", path,
                headers={"Authorization": authorization} if authorization else {},
                params={"user_id": injected_id} if injected_id else None,
                json={"user_id": injected_id} if injected_id else None)

    return asyncio.run(get())


@pytest.mark.parametrize("path", ["/me", "/license/status"])
def test_valid_access_only_uses_jwt_identity(settings: Settings, session: MagicMock, path: str) -> None:
    user: User = session.scalar.return_value
    token: str = create_access_token(user.id, settings=settings)
    other_id: str = str(uuid4())
    response: Response = get_account(path, session, f"Bearer {token}", other_id)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert other_id not in response.text
    assert user.cpf not in response.text
    assert user.password_hash not in response.text
    assert token not in response.text
    for call in session.scalar.call_args_list:
        assert user.id in call.args[0].compile().params.values()
    assert user.id in session.execute.call_args.args[0].compile().params.values()
    # A leitura de identidade termina antes da transação do serviço de licença.
    assert session.begin.call_count == 2
    if path == "/me":
        assert set(response.json()) == {"id", "name", "email", "subscription"}
        assert response.json()["id"] == str(user.id)
        assert set(response.json()["subscription"]) == {"status", "plan", "starts_at", "expires_at"}
    else:
        assert response.json()["active"] is True
        assert response.json()["plan"] == {"code": "ANNUAL", "name": "1 ano"}


@pytest.mark.parametrize("path", ["/me", "/license/status"])
@pytest.mark.parametrize("problem", ["missing", "basic", "malformed", "refresh", "expired", "wrong_signature"])
def test_invalid_bearer_rejected_before_database(settings: Settings, session: MagicMock, path: str, problem: str) -> None:
    subject: UUID = session.scalar.return_value.id
    token: str = create_access_token(subject, settings=settings)
    header: str | None = f"Bearer {token}"
    if problem == "missing":
        header = None
    elif problem == "basic":
        header = "Basic invalid"
    elif problem == "malformed":
        header = "Bearer invalid.jwt"
    elif problem == "refresh":
        header = f"Bearer {create_refresh_token(subject, settings=settings)}"
    else:
        payload = decode_access_token(token, settings=settings)
        assert settings.jwt_access_secret is not None
        key: str = settings.jwt_access_secret.get_secret_value()
        if problem == "expired":
            payload["iat"] = int(datetime.now(UTC).timestamp()) - 100
            payload["exp"] = payload["iat"] + 1
        else:
            key = secrets.token_hex(32)
        header = f"Bearer {jwt.encode(payload, key, algorithm='HS256')}"
    response: Response = get_account(path, session, header)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"
    session.begin.assert_not_called()


@pytest.mark.parametrize("path", ["/me", "/license/status"])
@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_disabled_account(settings: Settings, session: MagicMock, path: str, missing: bool) -> None:
    token: str = create_access_token(session.scalar.return_value.id, settings=settings)
    if missing:
        session.scalar.return_value = None
    else:
        session.scalar.return_value.is_active = False
    response: Response = get_account(path, session, f"Bearer {token}")
    assert response.status_code == 401
    session.execute.assert_not_called()


@pytest.mark.parametrize("status", [SubscriptionStatus.PENDING, SubscriptionStatus.EXPIRED,
                                  SubscriptionStatus.CANCELLED, SubscriptionStatus.SUSPENDED])
def test_authenticated_account_can_inspect_inactive_license(settings: Settings, session: MagicMock, status: SubscriptionStatus) -> None:
    subscription, _ = session.execute.return_value.all.return_value[0]
    subscription.status = status
    token: str = create_access_token(session.scalar.return_value.id, settings=settings)
    response: Response = get_account("/license/status", session, f"Bearer {token}")
    assert response.status_code == 200
    assert response.json()["active"] is False
    assert response.json()["status"] == status


def test_expiration_is_evaluated_by_service(settings: Settings, session: MagicMock) -> None:
    subscription, _ = session.execute.return_value.all.return_value[0]
    subscription.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    response: Response = get_account("/license/status", session,
        f"Bearer {create_access_token(session.scalar.return_value.id, settings=settings)}")
    assert response.json()["active"] is False
    assert response.json()["status"] == "EXPIRED"
    assert subscription.status == SubscriptionStatus.EXPIRED


@pytest.mark.parametrize("path", ["/me", "/license/status"])
def test_no_subscription_returns_null_summary(settings: Settings, session: MagicMock, path: str) -> None:
    session.execute.return_value.all.return_value = []
    response: Response = get_account(path, session,
        f"Bearer {create_access_token(session.scalar.return_value.id, settings=settings)}")
    assert response.status_code == 200
    summary = response.json()["subscription"] if path == "/me" else response.json()
    assert summary["status"] is None and summary["plan"] is None


def test_database_failure_does_not_authorize(settings: Settings, session: MagicMock) -> None:
    session.execute.side_effect = SQLAlchemyError("private database detail")
    response: Response = get_account("/license/status", session,
        f"Bearer {create_access_token(session.scalar.return_value.id, settings=settings)}")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LICENSE_STATUS_UNAVAILABLE"
    assert "private" not in response.text


def test_openapi_declares_bearer_and_no_identity_parameter() -> None:
    specification = create_app().openapi()
    for path in ("/me", "/license/status"):
        operation = specification["paths"][path]["get"]
        assert operation["security"] == [{"AccessToken": []}]
        assert "requestBody" not in operation
        assert not operation.get("parameters")


def test_postgresql_authenticated_license_and_account(monkeypatch: pytest.MonkeyPatch) -> None:
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: endpoints autenticados no PostgreSQL não verificados")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))
    user_id: UUID = uuid4()
    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            seed_plans(connection)
            plan_id = connection.scalar(select(Plan.id).where(Plan.code == PlanCode.ANNUAL))
            connection.execute(User.__table__.insert().values(id=user_id, name="Conta teste",
                email=f"{user_id.hex}@example.com", cpf=f"{secrets.randbelow(10**11):011d}",
                password_hash=secrets.token_hex(32), is_active=True))
            connection.execute(Subscription.__table__.insert().values(user_id=user_id, plan_id=plan_id,
                status=SubscriptionStatus.ACTIVE, starts_at=datetime.now(UTC) - timedelta(days=2),
                expires_at=datetime.now(UTC) - timedelta(days=1)))
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as db:
                header: str = f"Bearer {create_access_token(user_id)}"
                result: Response = get_account("/license/status", db, header)
                assert result.status_code == 200
                assert result.json()["active"] is False and result.json()["status"] == "EXPIRED"
                account: Response = get_account("/me", db, header)
                assert account.status_code == 200
                assert account.json()["id"] == str(user_id)
                assert account.json()["subscription"]["status"] == "EXPIRED"
        finally:
            outer.rollback()
