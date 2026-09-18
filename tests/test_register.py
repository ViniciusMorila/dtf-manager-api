"""Contrato HTTP e transação de cadastro; PostgreSQL real é opcional no ambiente."""

import asyncio
import secrets
from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine, get_session
from app.main import create_app
from app.modules.auth.registration import register_user
from app.modules.plans.models import Plan, PlanCode
from app.modules.subscriptions.models import Subscription
from app.modules.users.models import User
from app.modules.users.schemas.input import UserCreate
from app.shared.exceptions.registration import (
    CPFAlreadyExists,
    EmailAlreadyExists,
    RegistrationError,
)
from app.shared.security.password import verify_password
from scripts.seed_plans import seed_plans


@pytest.fixture
def payload() -> dict[str, str]:
    return {"name": " Ana ", "email": "ANA@EXAMPLE.COM", "cpf": "123.456.789-09",
            "password": secrets.token_urlsafe(24) + "a1", "plan_code": "MONTHLY"}


@pytest.fixture
def database_session() -> MagicMock:
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [None, None, Plan(id=uuid4(), code=PlanCode.MONTHLY, is_active=True)]

    def flush() -> None:
        for call in session.add.call_args_list:
            instance = call.args[0]
            if instance.id is None:
                instance.id = uuid4()
                instance.created_at = datetime.now(UTC)
                instance.updated_at = instance.created_at

    session.flush.side_effect = flush
    return session


def post_registration(payload: dict[str, str], session: Session) -> Response:
    application: FastAPI = create_app()

    def override_session() -> Session:
        return session

    application.dependency_overrides[get_session] = override_session

    async def post() -> Response:
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
            return await client.post("/auth/register", json=payload)

    return asyncio.run(post())


def test_register_201_pending_and_no_credentials(payload: dict[str, str], database_session: MagicMock) -> None:
    response: Response = post_registration(payload, database_session)
    assert response.status_code == 201, response.text
    assert set(response.json()) == {"user", "subscription"}
    assert response.json()["user"]["name"] == "Ana"
    assert response.json()["user"]["email"] == "ana@example.com"
    subscription = response.json()["subscription"]
    assert subscription["status"] == "PENDING"
    for field in ("starts_at", "expires_at", "activated_at", "cancelled_at"):
        assert subscription[field] is None
    user: User = database_session.add.call_args_list[0].args[0]
    assert user.cpf == "12345678909"
    assert verify_password(payload["password"], user.password_hash)
    assert payload["password"] not in response.text
    assert user.password_hash not in response.text
    for private in ("password", "password_hash", "access_token", "refresh_token", "token_hash"):
        assert private not in response.text
    database_session.begin.assert_called_once()


@pytest.mark.parametrize(("field", "value", "code"), [
    ("name", "   ", "VALIDATION_ERROR"), ("email", "invalid", "VALIDATION_ERROR"),
    ("cpf", "11111111111", "INVALID_CPF"), ("cpf", "123.456.789-00", "INVALID_CPF"),
    ("cpf", "12345678909a", "INVALID_CPF"), ("password", "short1", "WEAK_PASSWORD"),
    ("password", "abcdefgh", "WEAK_PASSWORD"), ("password", "12345678", "WEAK_PASSWORD"),
    ("plan_code", "UNKNOWN", "INVALID_PLAN"), ("status", "ACTIVE", "VALIDATION_ERROR"),
])
def test_invalid_inputs_never_write(
    payload: dict[str, str], database_session: MagicMock, field: str, value: str, code: str,
) -> None:
    payload[field] = value
    response: Response = post_registration(payload, database_session)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert payload["password"] not in response.text
    assert "input" not in response.text
    database_session.begin.assert_not_called()
    database_session.add.assert_not_called()


@pytest.mark.parametrize(("existing_email", "code"), [(True, "EMAIL_ALREADY_EXISTS"), (False, "CPF_ALREADY_EXISTS")])
def test_duplicate_conflicts(
    payload: dict[str, str], database_session: MagicMock, existing_email: bool, code: str,
) -> None:
    database_session.scalar.side_effect = [uuid4()] if existing_email else [None, uuid4()]
    response: Response = post_registration(payload, database_session)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == code
    database_session.add.assert_not_called()


@pytest.mark.parametrize("present", [False, True])
def test_missing_or_inactive_plan(payload: dict[str, str], database_session: MagicMock, present: bool) -> None:
    database_session.scalar.side_effect = [None, None, Plan(is_active=False) if present else None]
    response: Response = post_registration(payload, database_session)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PLAN"
    database_session.add.assert_not_called()


@pytest.mark.parametrize(("constraint", "error_type"), [
    ("uq_users_email", EmailAlreadyExists), ("uq_users_cpf", CPFAlreadyExists),
])
def test_concurrent_unique_violation_maps_to_domain_error(
    payload: dict[str, str], database_session: MagicMock,
    constraint: str, error_type: type[RegistrationError],
) -> None:
    class UniqueViolation(Exception):
        sqlstate: str = "23505"
        diag: SimpleNamespace = SimpleNamespace(constraint_name=constraint)

    database_session.flush.side_effect = IntegrityError("", {}, UniqueViolation())
    with pytest.raises(error_type):
        register_user(UserCreate.model_validate(payload), database_session)
    assert database_session.begin.return_value.__exit__.call_args.args[0] is IntegrityError


def test_subscription_failure_exits_transaction_with_error(payload: dict[str, str], database_session: MagicMock) -> None:
    original_flush = database_session.flush.side_effect

    def fail_second_flush() -> None:
        if database_session.add.call_count == 2:
            raise SQLAlchemyError("internal database detail")
        original_flush()

    database_session.flush.side_effect = fail_second_flush
    response: Response = post_registration(payload, database_session)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "REGISTRATION_FAILED"
    assert "internal database detail" not in response.text
    assert database_session.begin.return_value.__exit__.call_args.args[0] is SQLAlchemyError


@pytest.fixture
def real_session() -> Generator[Session, None, None]:
    """Savepoints permitem testar commits do serviço sem manter dados de teste."""
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: cadastro/rollback no PostgreSQL não verificados")
    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            seed_plans(connection)
            connection.execute(update(Plan.__table__).where(Plan.__table__.c.code == PlanCode.MONTHLY).values(is_active=True))
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as session:
                yield session
        finally:
            outer.rollback()


def fresh_registration() -> UserCreate:
    digits: str = "".join(str(secrets.randbelow(10)) for _ in range(9))
    if len(set(digits)) == 1:
        digits = "123456789"
    for size in (9, 10):
        remainder: int = sum(int(digits[index]) * (size + 1 - index) for index in range(size)) % 11
        digits += str(0 if remainder < 2 else 11 - remainder)
    return UserCreate(name="Cadastro teste", cpf=digits, email=f"{uuid4().hex}@example.com",
                      password=secrets.token_urlsafe(24) + "a1", plan_code=PlanCode.MONTHLY)


def test_real_postgresql_registration(real_session: Session) -> None:
    data: UserCreate = fresh_registration()
    response = register_user(data, real_session)
    user: User | None = real_session.get(User, response.user.id)
    assert user is not None and verify_password(data.password.get_secret_value(), user.password_hash)
    subscription: Subscription | None = real_session.get(Subscription, response.subscription.id)
    assert subscription is not None and subscription.user_id == user.id
    assert subscription.status == "PENDING"
    assert subscription.starts_at is subscription.expires_at is subscription.activated_at is None


def test_real_postgresql_subscription_failure_rolls_back_user(real_session: Session) -> None:
    data: UserCreate = fresh_registration()

    def fail_insert(mapper: object, connection: object, target: Subscription) -> None:
        raise SQLAlchemyError("forced subscription failure")

    event.listen(Subscription, "before_insert", fail_insert)
    try:
        with pytest.raises(RegistrationError):
            register_user(data, real_session)
    finally:
        event.remove(Subscription, "before_insert", fail_insert)
    assert real_session.scalar(select(User.id).where(User.email == data.email)) is None
