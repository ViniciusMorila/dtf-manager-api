"""Estados, limites temporais e persistência da autorização de licença."""

import secrets
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import load_models
from app.db.session import get_engine
from app.modules.plans.models import Plan, PlanCode
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.subscriptions.service import (
    LicenseStatusUnavailable,
    SubscriptionService,
)
from app.modules.users.models import User
from scripts.seed_plans import seed_plans

NOW: datetime = datetime(2026, 9, 7, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def server_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    load_models()
    class ServerTime(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return NOW

    monkeypatch.setattr("app.modules.subscriptions.service.datetime", ServerTime)


@pytest.fixture
def session() -> MagicMock:
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.return_value = User(id=uuid4(), is_active=True)
    session.execute.return_value.all.return_value = []
    return session


def subscription_pair(lifetime: bool = False) -> tuple[Subscription, Plan]:
    plan: Plan = Plan(id=uuid4(), code=PlanCode.LIFETIME if lifetime else PlanCode.ANNUAL,
        name="Vitalício" if lifetime else "1 ano", is_lifetime=lifetime, is_active=True)
    subscription: Subscription = Subscription(id=uuid4(), plan_id=plan.id, status=SubscriptionStatus.ACTIVE,
        starts_at=NOW - timedelta(days=1), expires_at=None if lifetime else NOW + timedelta(days=1))
    return subscription, plan


@pytest.mark.parametrize("status", [SubscriptionStatus.PENDING, SubscriptionStatus.CANCELLED,
                                  SubscriptionStatus.SUSPENDED, SubscriptionStatus.EXPIRED])
@pytest.mark.parametrize("lifetime", [False, True])
@pytest.mark.parametrize("user_active", [False, True])
def test_inactive_states_never_authorize(session: MagicMock, status: SubscriptionStatus, lifetime: bool, user_active: bool) -> None:
    subscription, plan = subscription_pair(lifetime)
    subscription.status = status
    session.scalar.return_value.is_active = user_active
    session.execute.return_value.all.return_value = [(subscription, plan)]
    result = SubscriptionService(session).get_license_status(uuid4())
    assert result.active is False
    assert result.status == status
    assert subscription.status == status


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_expiry_boundary_and_persisted_status(session: MagicMock, offset: int) -> None:
    subscription, plan = subscription_pair()
    subscription.expires_at = NOW + timedelta(microseconds=offset)
    session.execute.return_value.all.return_value = [(subscription, plan)]
    result = SubscriptionService(session).get_license_status(uuid4())
    assert result.active is (offset > 0)
    assert result.status == (SubscriptionStatus.ACTIVE if offset > 0 else SubscriptionStatus.EXPIRED)
    assert subscription.status == result.status
    session.flush.assert_called_once()
    session.begin.assert_called_once()


@pytest.mark.parametrize("start_offset", [None, -1, 0, 1])
@pytest.mark.parametrize("lifetime", [False, True])
def test_start_date_rules(session: MagicMock, start_offset: int | None, lifetime: bool) -> None:
    subscription, plan = subscription_pair(lifetime)
    subscription.starts_at = None if start_offset is None else NOW + timedelta(seconds=start_offset)
    session.execute.return_value.all.return_value = [(subscription, plan)]
    result = SubscriptionService(session).get_license_status(uuid4())
    expected: bool = lifetime or (start_offset is not None and start_offset <= 0)
    assert result.active is expected


@pytest.mark.parametrize("lifetime", [False, True])
def test_inactive_user_never_authorized(session: MagicMock, lifetime: bool) -> None:
    session.scalar.return_value.is_active = False
    session.execute.return_value.all.return_value = [subscription_pair(lifetime)]
    assert not SubscriptionService(session).get_license_status(uuid4()).active


def test_missing_user_and_subscription(session: MagicMock) -> None:
    result = SubscriptionService(session).get_license_status(uuid4())
    assert result.model_dump() == {"active": False, "status": None, "plan": None,
                                   "starts_at": None, "expires_at": None, "is_lifetime": False}
    session.scalar.return_value = None
    assert not SubscriptionService(session).get_license_status(uuid4()).active


def test_common_plan_requires_expiry(session: MagicMock) -> None:
    subscription, plan = subscription_pair()
    subscription.expires_at = None
    session.execute.return_value.all.return_value = [(subscription, plan)]
    assert not SubscriptionService(session).get_license_status(uuid4()).active


def test_lifetime_with_expiry_fails_closed(session: MagicMock) -> None:
    subscription, plan = subscription_pair(True)
    subscription.expires_at = NOW + timedelta(days=1)
    session.execute.return_value.all.return_value = [(subscription, plan)]
    assert not SubscriptionService(session).get_license_status(uuid4()).active


def test_catalog_deactivation_does_not_cancel_paid_license(session: MagicMock) -> None:
    subscription, plan = subscription_pair()
    plan.is_active = False
    session.execute.return_value.all.return_value = [(subscription, plan)]
    assert SubscriptionService(session).get_license_status(uuid4()).active


def test_multiple_subscriptions_and_expiration(session: MagicMock) -> None:
    pending, plan = subscription_pair()
    pending.status = SubscriptionStatus.PENDING
    expired, _ = subscription_pair()
    expired.expires_at = NOW
    valid, lifetime = subscription_pair(True)
    session.execute.return_value.all.return_value = [(pending, plan), (expired, plan), (valid, lifetime)]
    result = SubscriptionService(session).get_license_status(uuid4())
    assert result.active and result.is_lifetime
    assert expired.status == SubscriptionStatus.EXPIRED
    assert pending.status == SubscriptionStatus.PENDING


def test_without_valid_subscription_returns_latest(session: MagicMock) -> None:
    latest, plan = subscription_pair()
    latest.status = SubscriptionStatus.SUSPENDED
    older, _ = subscription_pair()
    older.status = SubscriptionStatus.CANCELLED
    session.execute.return_value.all.return_value = [(latest, plan), (older, plan)]
    assert SubscriptionService(session).get_license_status(uuid4()).status == SubscriptionStatus.SUSPENDED


def test_timezone_and_public_contract(session: MagicMock) -> None:
    subscription, plan = subscription_pair()
    subscription.expires_at = (NOW + timedelta(hours=1)).astimezone(timezone(timedelta(hours=-3)))
    session.execute.return_value.all.return_value = [(subscription, plan)]
    result = SubscriptionService(session).get_license_status(uuid4())
    assert result.active
    assert result.expires_at.tzinfo is UTC
    assert result.model_dump()["plan"] == {"code": PlanCode.ANNUAL, "name": "1 ano"}
    assert set(result.model_dump()) == {"active", "status", "plan", "starts_at", "expires_at", "is_lifetime"}


@pytest.mark.parametrize("failure", ["query", "flush", "commit"])
def test_database_failure_cannot_return_authorization(session: MagicMock, failure: str) -> None:
    session.execute.return_value.all.return_value = [subscription_pair()]
    if failure == "query":
        session.execute.side_effect = SQLAlchemyError("private")
    elif failure == "flush":
        session.flush.side_effect = SQLAlchemyError("private")
    else:
        session.begin.return_value.__exit__.side_effect = SQLAlchemyError("private")
    with pytest.raises(LicenseStatusUnavailable) as error:
        SubscriptionService(session).get_license_status(uuid4())
    assert "private" not in str(error.value)


def test_postgresql_expiration_is_persisted() -> None:
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: expiração real não verificada")
    user_id = uuid4()
    subscription_id = uuid4()
    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            seed_plans(connection)
            plan_id = connection.scalar(select(Plan.id).where(Plan.code == PlanCode.ANNUAL))
            connection.execute(User.__table__.insert().values(id=user_id, name="Licença teste",
                email=f"{user_id.hex}@example.com", cpf=f"{secrets.randbelow(10**11):011d}",
                password_hash=secrets.token_hex(32), is_active=True))
            connection.execute(Subscription.__table__.insert().values(id=subscription_id,
                user_id=user_id, plan_id=plan_id, status=SubscriptionStatus.ACTIVE,
                starts_at=NOW - timedelta(days=1), expires_at=NOW))
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as db_session:
                result = SubscriptionService(db_session).get_license_status(user_id)
                assert not result.active and result.status == SubscriptionStatus.EXPIRED
            assert connection.scalar(select(Subscription.status).where(Subscription.id == subscription_id)) == SubscriptionStatus.EXPIRED
        finally:
            outer.rollback()
