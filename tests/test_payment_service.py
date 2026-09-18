"""Pagamentos internos, idempotência e ativação transacional."""

import secrets
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import load_models
from app.db.session import get_engine
from app.modules.payments.contracts import (
    PaymentCreate,
    PaymentRecord,
    PaymentUpdate,
    ProviderPayment,
)
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.provider import PaymentProvider
from app.modules.payments.service import PaymentConflict, PaymentError, PaymentService
from app.modules.plans.models import Plan, PlanCode
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.subscriptions.service import (
    SubscriptionActivationError,
    SubscriptionService,
)
from app.modules.users.models import User
from scripts.seed_plans import seed_plans


@pytest.fixture
def records() -> tuple[Payment, Subscription, Plan]:
    load_models()
    plan: Plan = Plan(id=uuid4(), code=PlanCode.MONTHLY, is_lifetime=False)
    subscription: Subscription = Subscription(id=uuid4(), user_id=uuid4(), plan_id=plan.id,
        status=SubscriptionStatus.PENDING, starts_at=None, expires_at=None, activated_at=None,
        cancelled_at=None, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    payment: Payment = Payment(id=uuid4(), user_id=subscription.user_id, subscription_id=subscription.id,
        provider="test", provider_payment_id="external-test", amount=Decimal("29.90"), currency="BRL",
        status=PaymentStatus.PENDING)
    return payment, subscription, plan


def creation(payment: Payment) -> PaymentCreate:
    return PaymentCreate(user_id=payment.user_id, subscription_id=payment.subscription_id,
        provider=payment.provider, provider_payment_id=payment.provider_payment_id,
        amount=payment.amount, currency=payment.currency, payment_id=payment.id)


@pytest.mark.parametrize("inserted", [True, False])
def test_create_and_duplicate_return_same_record(records: tuple[Payment, Subscription, Plan], inserted: bool) -> None:
    payment, subscription, _ = records
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [subscription, payment]
    session.execute.return_value.scalar_one_or_none.return_value = payment.id if inserted else None
    result: PaymentRecord = PaymentService(session).create_payment(creation(payment))
    assert result.id == payment.id and result.amount == Decimal("29.90")
    assert "ON CONFLICT DO NOTHING" in str(session.execute.call_args.args[0])


def test_duplicate_with_changed_amount_rejected(records: tuple[Payment, Subscription, Plan]) -> None:
    payment, subscription, _ = records
    data: PaymentCreate = creation(payment)
    data.amount = Decimal("30.00")
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [subscription, payment]
    session.execute.return_value.scalar_one_or_none.return_value = None
    with pytest.raises(PaymentConflict):
        PaymentService(session).create_payment(data)


def test_create_rejects_other_users_subscription(records: tuple[Payment, Subscription, Plan]) -> None:
    payment, subscription, _ = records
    subscription.user_id = uuid4()
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.return_value = subscription
    with pytest.raises(PaymentConflict):
        PaymentService(session).create_payment(creation(payment))
    session.execute.assert_not_called()


@pytest.mark.parametrize("amount", [29.9, "29.90", Decimal("NaN"), Decimal("Infinity"), Decimal(-1), Decimal("1.001")])
def test_money_rejects_float_and_invalid_values(records: tuple[Payment, Subscription, Plan], amount: object) -> None:
    with pytest.raises(ValidationError):
        PaymentCreate.model_validate({**creation(records[0]).model_dump(), "amount": amount})


def test_approval_twice_activates_once_and_preserves_period(records: tuple[Payment, Subscription, Plan]) -> None:
    payment, subscription, plan = records
    session: MagicMock = MagicMock(spec=Session)
    session.in_transaction.return_value = True
    session.scalar.side_effect = [payment, subscription]
    session.execute.return_value.one_or_none.return_value = (subscription, plan)
    service: PaymentService = PaymentService(session)
    with patch.object(SubscriptionService, "activate_subscription", autospec=True,
                      side_effect=SubscriptionService.activate_subscription) as activate:
        assert service.process_approval(payment.id).status == PaymentStatus.APPROVED
        dates = (subscription.starts_at, subscription.expires_at, subscription.activated_at)
        assert subscription.status == SubscriptionStatus.ACTIVE
        session.scalar.side_effect = [payment]
        assert service.process_approval(payment.id).status == PaymentStatus.APPROVED
        assert (subscription.starts_at, subscription.expires_at, subscription.activated_at) == dates
        activate.assert_called_once()
        session.begin_nested.assert_called_once()
        assert session.begin.call_count == 2


@pytest.mark.parametrize("status", [PaymentStatus.PENDING, PaymentStatus.REJECTED, PaymentStatus.CANCELLED])
def test_nonapproved_updates_never_activate(records: tuple[Payment, Subscription, Plan], status: PaymentStatus) -> None:
    payment, subscription, _ = records
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [payment, subscription]
    with patch.object(SubscriptionService, "activate_subscription") as activate:
        assert PaymentService(session).update_payment(payment.id, PaymentUpdate(status=status)).status == status
        activate.assert_not_called()


@pytest.mark.parametrize("previous", [PaymentStatus.APPROVED, PaymentStatus.REFUNDED, PaymentStatus.CANCELLED])
def test_old_notifications_do_not_reset_state(records: tuple[Payment, Subscription, Plan], previous: PaymentStatus) -> None:
    payment = records[0]
    payment.status = previous
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.return_value = payment
    with pytest.raises(PaymentConflict):
        PaymentService(session).update_payment(payment.id, PaymentUpdate(status=PaymentStatus.PENDING))


def test_activation_failure_exits_payment_transaction_with_error(records: tuple[Payment, Subscription, Plan]) -> None:
    payment, subscription, _ = records
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [payment, subscription]
    with patch.object(SubscriptionService, "activate_subscription", side_effect=SubscriptionActivationError("failure")):  # noqa: SIM117 - separate failure expectation
        with pytest.raises(PaymentError):
            PaymentService(session).process_approval(payment.id)
    assert session.begin.return_value.__exit__.call_args.args[0] is SubscriptionActivationError


def test_external_id_collision_rejected(records: tuple[Payment, Subscription, Plan]) -> None:
    payment = records[0]
    payment.provider_payment_id = None
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.return_value = payment
    session.flush.side_effect = IntegrityError("private", {}, Exception())
    with pytest.raises(PaymentConflict):
        PaymentService(session).update_payment(payment.id, PaymentUpdate(status=PaymentStatus.PENDING, provider_payment_id="taken"))


def test_provider_confirmation_must_match_amount(records: tuple[Payment, Subscription, Plan]) -> None:
    payment = records[0]
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.return_value = payment
    with pytest.raises(PaymentConflict):
        PaymentService(session).apply_provider_payment(payment.id, "test", ProviderPayment(
            provider_payment_id="external-test", amount=Decimal(0), currency="BRL", status=PaymentStatus.APPROVED))
    session.flush.assert_not_called()


def test_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        PaymentProvider()


def test_local_uuid_handles_retries_without_external_id(records: tuple[Payment, Subscription, Plan]) -> None:
    payment, subscription, _ = records
    payment.provider_payment_id = None
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [subscription, payment]
    session.execute.return_value.scalar_one_or_none.return_value = None
    assert PaymentService(session).create_payment(creation(payment)).id == payment.id


@pytest.mark.parametrize("entry", ["update", "provider"])
def test_all_approval_entrypoints_activate(records: tuple[Payment, Subscription, Plan], entry: str) -> None:
    payment, subscription, _ = records
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [payment, subscription]
    service: PaymentService = PaymentService(session)
    with patch.object(SubscriptionService, "activate_subscription") as activate:
        if entry == "update":
            result = service.update_payment(payment.id, PaymentUpdate(status=PaymentStatus.APPROVED))
        else:
            result = service.apply_provider_payment(payment.id, "test", ProviderPayment(
                provider_payment_id=payment.provider_payment_id, amount=payment.amount,
                currency=payment.currency, status=PaymentStatus.APPROVED))
        assert result.status == PaymentStatus.APPROVED
        activate.assert_called_once_with(subscription.id)


@pytest.mark.parametrize("fail_activation", [False, True])
def test_postgresql_payment_and_activation_atomic(monkeypatch: pytest.MonkeyPatch, fail_activation: bool) -> None:
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: aprovação atômica real não verificada")
    load_models()
    user_id = uuid4()
    subscription_id = uuid4()
    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            seed_plans(connection)
            plan_id = connection.scalar(select(Plan.id).where(Plan.code == PlanCode.MONTHLY))
            connection.execute(User.__table__.insert().values(id=user_id, name="Pagamento teste",
                email=f"{user_id.hex}@example.com", cpf=f"{secrets.randbelow(10**11):011d}",
                password_hash=secrets.token_hex(32), is_active=True))
            connection.execute(Subscription.__table__.insert().values(id=subscription_id, user_id=user_id,
                plan_id=plan_id, status=SubscriptionStatus.PENDING))
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as db:
                service: PaymentService = PaymentService(db)
                data: PaymentCreate = PaymentCreate(user_id=user_id, subscription_id=subscription_id,
                    provider="test", provider_payment_id=uuid4().hex, amount=Decimal("29.90"), currency="BRL")
                first = service.create_payment(data)
                assert service.create_payment(data).id == first.id
                if fail_activation:
                    with patch.object(SubscriptionService, "activate_subscription", side_effect=SubscriptionActivationError("failure")):  # noqa: SIM117 - separate failure expectation
                        with pytest.raises(PaymentError):
                            service.process_approval(first.id)
                else:
                    service.process_approval(first.id)
                    dates = connection.execute(select(Subscription.starts_at, Subscription.expires_at).where(Subscription.id == subscription_id)).one()
                    service.process_approval(first.id)
                    assert connection.execute(select(Subscription.starts_at, Subscription.expires_at).where(Subscription.id == subscription_id)).one() == dates
            assert connection.scalar(select(Payment.status).where(Payment.id == first.id)) == (PaymentStatus.PENDING if fail_activation else PaymentStatus.APPROVED)
            assert connection.scalar(select(Subscription.status).where(Subscription.id == subscription_id)) == (SubscriptionStatus.PENDING if fail_activation else SubscriptionStatus.ACTIVE)
        finally:
            outer.rollback()
