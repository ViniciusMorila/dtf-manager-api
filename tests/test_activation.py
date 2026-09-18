"""Ativação inicial, meses de calendário e idempotência."""

import secrets
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import load_models
from app.db.session import get_engine
from app.modules.plans.models import Plan, PlanCode
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.subscriptions.service import (
    SubscriptionActivationError,
    SubscriptionService,
)
from app.modules.users.models import User
from app.shared.utils.calendar import add_calendar_months
from scripts.seed_plans import seed_plans


def freeze_time(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return instant

    monkeypatch.setattr("app.modules.subscriptions.service.datetime", Clock)


@pytest.fixture
def session() -> MagicMock:
    load_models()
    session: MagicMock = MagicMock(spec=Session)
    session.in_transaction.return_value = False
    plan: Plan = Plan(id=uuid4(), code=PlanCode.MONTHLY, is_lifetime=False)
    subscription: Subscription = Subscription(id=uuid4(), user_id=uuid4(), plan_id=plan.id,
        status=SubscriptionStatus.PENDING, starts_at=None, expires_at=None, activated_at=None,
        cancelled_at=None, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    session.execute.return_value.one_or_none.return_value = (subscription, plan)
    return session


@pytest.mark.parametrize(("start", "months", "expected"), [
    ("2026-01-31", 1, "2026-02-28"), ("2024-01-31", 1, "2024-02-29"),
    ("2024-02-29", 12, "2025-02-28"), ("2026-08-31", 6, "2027-02-28"),
    ("2023-08-31", 6, "2024-02-29"), ("2026-12-31", 1, "2027-01-31"),
    ("2026-04-30", 1, "2026-05-30"),
])
def test_calendar_months(start: str, months: int, expected: str) -> None:
    source: datetime = datetime.fromisoformat(start).replace(hour=17, minute=23, microsecond=987, tzinfo=UTC)
    result: datetime = add_calendar_months(source, months)
    assert result.date().isoformat() == expected
    assert result.timetz() == source.timetz()


@pytest.mark.parametrize(("code", "expected"), [
    (PlanCode.MONTHLY, datetime(2024, 2, 29, 13, tzinfo=UTC)),
    (PlanCode.SEMIANNUAL, datetime(2024, 7, 31, 13, tzinfo=UTC)),
    (PlanCode.ANNUAL, datetime(2025, 1, 31, 13, tzinfo=UTC)),
    (PlanCode.LIFETIME, None),
])
def test_activation_and_repeat_preserve_dates(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch, code: PlanCode, expected: datetime | None,
) -> None:
    now: datetime = datetime(2024, 1, 31, 13, tzinfo=UTC)
    freeze_time(monkeypatch, now)
    subscription, plan = session.execute.return_value.one_or_none.return_value
    plan.code = code
    plan.is_lifetime = code == PlanCode.LIFETIME
    service: SubscriptionService = SubscriptionService(session)
    result = service.activate_subscription(subscription.id)
    assert result.status == SubscriptionStatus.ACTIVE
    assert result.starts_at == result.activated_at == now
    assert result.expires_at == expected
    freeze_time(monkeypatch, now + timedelta(days=100))
    assert service.activate_subscription(subscription.id).model_dump() == result.model_dump()
    session.flush.assert_called_once()
    query = session.execute.call_args.args[0]
    assert "FOR UPDATE OF subscriptions" in str(query.compile(dialect=postgresql.dialect()))
    assert query.get_execution_options()["populate_existing"] is True


@pytest.mark.parametrize("status", [SubscriptionStatus.EXPIRED, SubscriptionStatus.SUSPENDED, SubscriptionStatus.CANCELLED])
def test_retries_do_not_reactivate_previously_activated_subscription(session: MagicMock, status: SubscriptionStatus) -> None:
    subscription, _ = session.execute.return_value.one_or_none.return_value
    subscription.activated_at = datetime(2020, 1, 1, tzinfo=UTC)
    subscription.status = status
    result = SubscriptionService(session).activate_subscription(subscription.id)
    assert result.status == status
    assert result.activated_at == subscription.activated_at
    session.flush.assert_not_called()


@pytest.mark.parametrize("status", [SubscriptionStatus.EXPIRED, SubscriptionStatus.SUSPENDED, SubscriptionStatus.CANCELLED])
def test_nonpending_initial_activation_rejected(session: MagicMock, status: SubscriptionStatus) -> None:
    subscription, _ = session.execute.return_value.one_or_none.return_value
    subscription.status = status
    with pytest.raises(SubscriptionActivationError):
        SubscriptionService(session).activate_subscription(subscription.id)
    session.flush.assert_not_called()


def test_legacy_active_without_activation_date_not_extended(session: MagicMock) -> None:
    subscription, _ = session.execute.return_value.one_or_none.return_value
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.expires_at = datetime(2020, 2, 1, tzinfo=UTC)
    result = SubscriptionService(session).activate_subscription(subscription.id)
    assert result.expires_at == subscription.expires_at
    assert result.activated_at is None
    session.flush.assert_not_called()


def test_missing_subscription(session: MagicMock) -> None:
    session.execute.return_value.one_or_none.return_value = None
    with pytest.raises(SubscriptionActivationError):
        SubscriptionService(session).activate_subscription(uuid4())


@pytest.mark.parametrize("failure", ["flush", "commit"])
def test_failures_do_not_report_success(session: MagicMock, failure: str) -> None:
    if failure == "flush":
        session.flush.side_effect = SQLAlchemyError("internal")
    else:
        session.begin.return_value.__exit__.side_effect = SQLAlchemyError("internal")
    with pytest.raises(SubscriptionActivationError) as error:
        SubscriptionService(session).activate_subscription(uuid4())
    assert "internal" not in str(error.value)


def test_postgresql_activation_is_idempotent() -> None:
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: ativação real não verificada")
    load_models()
    user_id = uuid4()
    subscription_id = uuid4()
    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            seed_plans(connection)
            plan_id = connection.scalar(select(Plan.id).where(Plan.code == PlanCode.MONTHLY))
            connection.execute(User.__table__.insert().values(id=user_id, name="Ativação teste",
                email=f"{user_id.hex}@example.com", cpf=f"{secrets.randbelow(10**11):011d}",
                password_hash=secrets.token_hex(32), is_active=True))
            connection.execute(Subscription.__table__.insert().values(id=subscription_id,
                user_id=user_id, plan_id=plan_id, status=SubscriptionStatus.PENDING))
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as db_session:
                service: SubscriptionService = SubscriptionService(db_session)
                first = service.activate_subscription(subscription_id)
                second = service.activate_subscription(subscription_id)
                assert first.model_dump() == second.model_dump()
            stored = connection.execute(select(Subscription.__table__).where(Subscription.id == subscription_id)).mappings().one()
            assert stored["status"] == SubscriptionStatus.ACTIVE
            assert stored["expires_at"] == add_calendar_months(stored["starts_at"], 1)
        finally:
            outer.rollback()
