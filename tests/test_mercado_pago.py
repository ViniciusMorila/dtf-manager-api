"""Integração Mercado Pago totalmente simulada; nenhuma cobrança real."""
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import load_models
from app.db.session import get_engine, get_session
from app.main import create_app
from app.modules.payments.contracts import PaymentCreate, ProviderPayment
from app.modules.payments.mercado_pago import (
    InvalidSignature,
    MercadoPagoPaymentProvider,
    ProviderInvalidPayment,
    ProviderUnavailable,
)
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.routes import get_provider
from app.modules.payments.service import PaymentConflict, PaymentService
from app.modules.plans.models import Plan, PlanCode
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.subscriptions.service import SubscriptionService
from app.modules.users.models import User


@pytest.fixture(autouse=True)
def block_real_payment_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Testes Mercado Pago exigem mocks; rede bloqueada.")
    monkeypatch.setattr("requests.sessions.Session.request", reject_network)


@pytest.fixture
def provider() -> MercadoPagoPaymentProvider:
    return MercadoPagoPaymentProvider(Settings(_env_file=None, environment="testing",
        mercado_pago_access_token=SecretStr(secrets.token_urlsafe(40)),
        mercado_pago_webhook_secret=SecretStr(secrets.token_urlsafe(40)), mercado_pago_environment="test"))


@pytest.fixture
def intent() -> PaymentCreate:
    return PaymentCreate(user_id=uuid4(), subscription_id=uuid4(), payment_id=uuid4(),
        provider="mercado_pago", amount=Decimal("29.90"), currency="BRL",
        description="DTF Manager", payer_email="buyer@example.com")


def response(intent: PaymentCreate, status: str = "pending") -> dict[str, Any]:
    return {"status": 200, "response": {"id": 123, "transaction_amount": Decimal("29.90"),
        "currency_id": "BRL", "status": status, "external_reference": str(intent.subscription_id),
        "metadata": {"payment_id": str(intent.payment_id)}, "live_mode": False}}


def signed(provider: MercadoPagoPaymentProvider, data_id: str = "123") -> dict[str, str]:
    ts: str = "1704908010"
    secret: SecretStr = provider._settings.mercado_pago_webhook_secret  # type: ignore[assignment]
    digest: str = hmac.new(secret.get_secret_value().encode(),
        f"id:{data_id.lower()};request-id:test-request;ts:{ts};".encode(), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={ts},v1={digest}", "x-request-id": "test-request"}


def test_create_uses_real_sdk_and_exact_decimal_transport(provider: MercadoPagoPaymentProvider,
                                                        intent: PaymentCreate) -> None:
    with patch("app.modules.payments.mercado_pago.requests.Session") as http:
        result: MagicMock = http.return_value.__enter__.return_value.request.return_value
        result.status_code = 201
        result.content = b"json"
        body: dict[str, Any] = response(intent)["response"]
        body["transaction_amount"] = "29.90"
        result.text = json.dumps(body).replace('"transaction_amount": "29.90"', '"transaction_amount": 29.90')
        for _ in range(2):
            assert provider.create_payment(intent, idempotency_key=str(intent.payment_id)).amount == Decimal("29.90")
        calls = http.return_value.__enter__.return_value.request.call_args_list
        for call in calls:
            assert call.args == ("POST", "https://api.mercadopago.com/v1/payments")
            assert call.kwargs["headers"]["x-idempotency-key"] == str(intent.payment_id)
            assert call.kwargs["timeout"] == 8.0
            assert '"transaction_amount": 29.90' in call.kwargs["data"]
            assert "cpf" not in call.kwargs["data"]
        assert calls[0].kwargs["data"] == calls[1].kwargs["data"]


@pytest.mark.parametrize(("external", "internal"), list(MercadoPagoPaymentProvider.STATUSES.items()))
def test_status_mapping(provider: MercadoPagoPaymentProvider, intent: PaymentCreate,
                        external: str, internal: PaymentStatus) -> None:
    with patch.object(provider._sdk, "payment") as sdk:
        sdk.return_value.get.return_value = response(intent, external)
        assert provider.fetch_payment("123").status == internal


@pytest.mark.parametrize("field,value", [("id", 999), ("status", "unknown"), ("live_mode", True),
    ("transaction_amount", 29.9), ("transaction_amount", "NaN"), ("external_reference", "bad")])
def test_invalid_external_response(provider: MercadoPagoPaymentProvider, intent: PaymentCreate,
                                   field: str, value: Any) -> None:
    data: dict[str, Any] = response(intent)
    data["response"][field] = value
    with patch.object(provider._sdk, "payment") as sdk:
        sdk.return_value.get.return_value = data
        with pytest.raises(ProviderInvalidPayment):
            provider.fetch_payment("123")


@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_api_failure(provider: MercadoPagoPaymentProvider, status: int) -> None:
    with patch.object(provider._sdk, "payment") as sdk:
        sdk.return_value.get.return_value = {"status": status, "response": {}}
        with pytest.raises(ProviderInvalidPayment if status == 404 else ProviderUnavailable):
            provider.fetch_payment("123")


def test_timeout_create_and_fetch(provider: MercadoPagoPaymentProvider, intent: PaymentCreate) -> None:
    with patch.object(provider._sdk, "payment") as sdk:
        sdk.return_value.get.side_effect = TimeoutError()
        sdk.return_value.create.side_effect = TimeoutError()
        with pytest.raises(ProviderUnavailable):
            provider.fetch_payment("123")
        with pytest.raises(ProviderUnavailable):
            provider.create_payment(intent, idempotency_key=str(intent.payment_id))


def test_signature(provider: MercadoPagoPaymentProvider) -> None:
    headers: dict[str, str] = signed(provider, "AbC123")
    provider.validate_signature(headers["x-signature"], headers["x-request-id"], "AbC123")
    for signature, request_id, data_id in [("", "test-request", "AbC123"),
        (headers["x-signature"], "other", "AbC123"), (headers["x-signature"], "test-request", "999")]:
        with pytest.raises(InvalidSignature):
            provider.validate_signature(signature, request_id, data_id)


@pytest.mark.parametrize("field,value", [("currency_id", "USD"), ("external_reference", None),
    ("metadata", {"payment_id": None}), ("id", None), ("id", True)])
def test_required_identity_and_brl(provider: MercadoPagoPaymentProvider, intent: PaymentCreate,
                                   field: str, value: Any) -> None:
    data: dict[str, Any] = response(intent)
    data["response"][field] = value
    with patch.object(provider._sdk, "payment") as sdk:
        sdk.return_value.create.return_value = data
        with pytest.raises(ProviderInvalidPayment):
            provider.create_payment(intent, idempotency_key=str(intent.payment_id))


@pytest.fixture
def records(intent: PaymentCreate) -> tuple[Payment, Subscription, Plan, User]:
    load_models()
    plan: Plan = Plan(id=uuid4(), code=PlanCode.MONTHLY, name="1 mês", price=intent.amount,
        is_active=True, is_lifetime=False, duration_months=1)
    user: User = User(id=intent.user_id, is_active=True, email=intent.payer_email)
    subscription: Subscription = Subscription(id=intent.subscription_id, user_id=user.id, plan_id=plan.id,
        status=SubscriptionStatus.PENDING, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    payment: Payment = Payment(id=intent.payment_id, user_id=user.id, subscription_id=subscription.id,
        provider="mercado_pago", amount=intent.amount, currency="BRL", status=PaymentStatus.PENDING,
        payment_metadata={"description": intent.description, "payer_email": intent.payer_email})
    return payment, subscription, plan, user


def confirmation(payment: Payment, status: PaymentStatus = PaymentStatus.APPROVED) -> ProviderPayment:
    return ProviderPayment(provider_payment_id="123", amount=payment.amount, currency="BRL", status=status,
        external_reference=payment.subscription_id, payment_id=payment.id)


@pytest.mark.parametrize("field,value", [("amount", Decimal(30)), ("currency", "USD"),
    ("external_reference", uuid4()), ("payment_id", uuid4()), ("provider_payment_id", "999")])
def test_divergence_never_approves(records: tuple[Payment, Subscription, Plan, User],
                                  field: str, value: Any) -> None:
    payment, subscription, plan, _ = records
    payment.provider_payment_id = "123"
    data: ProviderPayment = confirmation(payment).model_copy(update={field: value})
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [payment, subscription, plan]
    with patch.object(SubscriptionService, "activate_subscription") as activate:
        with pytest.raises(PaymentConflict):
            PaymentService(session).apply_provider_payment(payment.id, "mercado_pago", data)
        activate.assert_not_called()


def test_duplicate_approval_activates_once(records: tuple[Payment, Subscription, Plan, User]) -> None:
    payment, subscription, plan, _ = records
    session: MagicMock = MagicMock(spec=Session)
    session.in_transaction.return_value = True
    session.scalar.side_effect = [payment, subscription, plan, subscription, payment, subscription, plan]
    session.execute.return_value.one_or_none.return_value = (subscription, plan)
    service: PaymentService = PaymentService(session)
    with patch.object(SubscriptionService, "activate_subscription", autospec=True,
                      side_effect=SubscriptionService.activate_subscription) as activate:
        service.apply_provider_payment(payment.id, "mercado_pago", confirmation(payment))
        expiry: datetime | None = subscription.expires_at
        service.apply_provider_payment(payment.id, "mercado_pago", confirmation(payment))
        assert subscription.expires_at == expiry and expiry is not None
        assert activate.call_count == 1


@pytest.mark.parametrize("invalid", ["price", "user", "subscription"])
def test_authoritative_links(records: tuple[Payment, Subscription, Plan, User], invalid: str) -> None:
    payment, subscription, plan, _ = records
    if invalid == "price":
        plan.price = Decimal(99)
    elif invalid == "user":
        subscription.user_id = uuid4()
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [payment, None if invalid == "subscription" else subscription, plan]
    with pytest.raises(PaymentConflict):
        PaymentService(session).apply_provider_payment(payment.id, "mercado_pago", confirmation(payment))


@pytest.mark.parametrize("price", [Decimal("79.90"), Decimal("399.90"), Decimal("699.90"), Decimal("1499.90")])
def test_creation_uses_database_price(records: tuple[Payment, Subscription, Plan, User],
    provider: MercadoPagoPaymentProvider, price: Decimal) -> None:
    _, subscription, plan, user = records
    plan.price = price
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [subscription, plan]
    session.get.side_effect = [user, None]
    with patch.object(provider, "create_payment", side_effect=ProviderUnavailable()) as create:
        with pytest.raises(ProviderUnavailable):
            PaymentService(session).create_provider_charge(user_id=user.id, subscription_id=subscription.id,
                expected_amount=price, description="DTF Manager", provider=provider)
        stored: Payment = session.add.call_args.args[0]
        sent: PaymentCreate = create.call_args.args[0]
        assert stored.amount == sent.amount == plan.price
        assert isinstance(stored.amount, Decimal) and isinstance(sent.amount, Decimal)
        assert stored.currency == sent.currency == "BRL"


def test_creation_retry_persists_intent_before_network(records: tuple[Payment, Subscription, Plan, User],
                                                      provider: MercadoPagoPaymentProvider) -> None:
    payment, subscription, plan, user = records
    payment.id = uuid5(NAMESPACE_URL, f"dtf-manager:{provider.name}:{subscription.id}")
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [subscription, plan, subscription, plan]
    session.get.side_effect = [user, None, user, payment]
    with patch.object(provider, "create_payment", side_effect=ProviderUnavailable()) as create:
        for _ in range(2):
            with pytest.raises(ProviderUnavailable):
                PaymentService(session).create_provider_charge(user_id=user.id, subscription_id=subscription.id,
                    expected_amount=plan.price, description="DTF Manager", provider=provider)
        assert session.add.call_count == 1
        assert session.begin.return_value.__exit__.call_count == 2
        assert create.call_args_list[0] == create.call_args_list[1]


@pytest.mark.parametrize("price", [None, Decimal(0), Decimal(99)])
def test_creation_price_fail_closed(records: tuple[Payment, Subscription, Plan, User],
                                     provider: MercadoPagoPaymentProvider, price: Decimal | None) -> None:
    payment, subscription, plan, user = records
    plan.price = price
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [subscription, plan]
    session.get.return_value = user
    with patch.object(provider, "create_payment") as create:
        with pytest.raises(PaymentConflict):
            PaymentService(session).create_provider_charge(user_id=user.id, subscription_id=subscription.id,
                expected_amount=payment.amount, description="DTF", provider=provider)
        create.assert_not_called()


def test_webhook_duplicate_and_untrusted_body(provider: MercadoPagoPaymentProvider, intent: PaymentCreate) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client, patch.object(provider._sdk, "payment") as sdk, \
            patch.object(PaymentService, "apply_provider_payment") as apply:
        sdk.return_value.get.return_value = response(intent)
        for _ in range(2):
            result = client.post("/payments/mercado-pago/webhook?data.id=123", headers=signed(provider),
                json={"data": {"id": "999"}, "status": "approved", "user_id": str(uuid4())})
            assert result.status_code == 200 and result.json() == {"status": "ok"}
        assert apply.call_count == 2
        assert apply.call_args.args[2].status == PaymentStatus.PENDING
        assert sdk.return_value.get.call_args.args == ("123",)
        assert client.post("/payments/mercado-pago/webhook?data.id=123").status_code == 401
        assert apply.call_count == 2


@pytest.mark.parametrize("failure,code", [(ProviderUnavailable(), 503), (ProviderInvalidPayment(), 409)])
def test_webhook_failure(provider: MercadoPagoPaymentProvider, failure: Exception, code: int) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client, patch.object(provider, "fetch_payment", side_effect=failure), \
            patch.object(PaymentService, "apply_provider_payment") as apply:
        assert client.post("/payments/mercado-pago/webhook?data.id=123",
                           headers=signed(provider)).status_code == code
        apply.assert_not_called()


def test_settings_missing_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    with pytest.raises(ProviderUnavailable):
        MercadoPagoPaymentProvider(Settings(_env_file=None))
    access: str = secrets.token_urlsafe(40)
    webhook: str = secrets.token_urlsafe(40)
    monkeypatch.setenv("MERCADO_PAGO_ACCESS_TOKEN", access)
    monkeypatch.setenv("MERCADO_PAGO_WEBHOOK_SECRET", webhook)
    monkeypatch.setenv("MERCADO_PAGO_ENVIRONMENT", "test")
    settings: Settings = Settings(_env_file=None)
    settings.validate_mercado_pago()
    assert access not in repr(settings) and webhook not in repr(settings)


def test_production_rejects_partial_payment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    with pytest.raises(ValueError, match="Mercado Pago"):
        Settings(_env_file=None, environment="production",
            jwt_access_secret=SecretStr(secrets.token_urlsafe(40)),
            jwt_refresh_secret=SecretStr(secrets.token_urlsafe(40)),
            mercado_pago_environment="production")


@pytest.mark.parametrize("deliveries", [2, 5, 10])
@pytest.mark.parametrize("status", ["approved", "pending", "rejected"])
def test_webhook_full_service_repeated(provider: MercadoPagoPaymentProvider, intent: PaymentCreate,
    records: tuple[Payment, Subscription, Plan, User], deliveries: int, status: str) -> None:
    payment, subscription, plan, _ = records
    session: MagicMock = MagicMock(spec=Session)
    session.in_transaction.return_value = True
    session.scalar.side_effect = lambda statement: {
        Payment: payment, Subscription: subscription, Plan: plan,
    }[statement.column_descriptions[0]["entity"]]
    session.execute.return_value.one_or_none.return_value = (subscription, plan)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client, patch.object(provider._sdk, "payment") as sdk, \
            patch.object(SubscriptionService, "activate_subscription", autospec=True,
                         side_effect=SubscriptionService.activate_subscription) as activate:
        sdk.return_value.get.return_value = response(intent, status)
        dates: tuple[datetime | None, datetime | None] | None = None
        for _ in range(deliveries):
            result = client.post("/payments/mercado-pago/webhook?data.id=123", headers=signed(provider),
                                 json={"status": "approved", "data": {"id": "999"}})
            assert result.status_code == 200
            current = (subscription.starts_at, subscription.expires_at)
            if dates is not None:
                assert current == dates
            dates = current
        assert payment.status == provider.STATUSES[status]
        assert activate.call_count == (1 if status == "approved" else 0)
        assert session.add.call_count == 0
        assert sdk.return_value.get.call_count == deliveries


@pytest.mark.parametrize("field,value", [("transaction_amount", Decimal("99.90")),
    ("external_reference", str(uuid4())), ("currency_id", "USD"), ("id", 999)])
def test_webhook_divergence_through_service(provider: MercadoPagoPaymentProvider, intent: PaymentCreate,
    records: tuple[Payment, Subscription, Plan, User], field: str, value: Any) -> None:
    payment, subscription, plan, _ = records
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.side_effect = [payment, subscription, plan]
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: provider
    data: dict[str, Any] = response(intent, "approved")
    data["response"][field] = value
    with TestClient(app) as client, patch.object(provider._sdk, "payment") as sdk, \
            patch.object(SubscriptionService, "activate_subscription") as activate:
        sdk.return_value.get.return_value = data
        assert client.post("/payments/mercado-pago/webhook?data.id=123",
                           headers=signed(provider)).status_code == 409
        assert payment.status == PaymentStatus.PENDING
        activate.assert_not_called()


@pytest.mark.parametrize("query", ["", "?data.id=123&data.id=999"])
def test_webhook_ambiguous_identity(provider: MercadoPagoPaymentProvider, query: str) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client, patch.object(provider, "fetch_payment") as fetch:
        assert client.post("/payments/mercado-pago/webhook" + query,
                           headers=signed(provider)).status_code == 401
        fetch.assert_not_called()


@pytest.mark.parametrize("signature", ["invalid", "ts=1704908010,v1=é", "ts=1704908010,v1=00"])
def test_webhook_invalid_signature_never_calls_provider(
    provider: MercadoPagoPaymentProvider, signature: str,
) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: MagicMock(spec=Session)
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client, patch.object(provider, "fetch_payment") as fetch:
        result = client.post("/payments/mercado-pago/webhook?data.id=123",
            headers=[(b"x-signature", signature.encode("utf-8")), (b"x-request-id", b"test-request")])
        assert result.status_code == 401
        fetch.assert_not_called()


def test_whitespace_request_id_is_not_an_omitted_signature_component(
    provider: MercadoPagoPaymentProvider,
) -> None:
    secret: SecretStr | None = provider._settings.mercado_pago_webhook_secret
    assert secret is not None
    digest: str = hmac.new(secret.get_secret_value().encode(),
        b"id:123;ts:1704908010;", hashlib.sha256).hexdigest()
    with pytest.raises(InvalidSignature):
        provider.validate_signature(f"ts=1704908010,v1={digest}", "   ", "123")


def test_webhook_missing_local_payment(provider: MercadoPagoPaymentProvider, intent: PaymentCreate) -> None:
    session: MagicMock = MagicMock(spec=Session)
    session.scalar.return_value = None
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client, patch.object(provider._sdk, "payment") as sdk, \
            patch.object(SubscriptionService, "activate_subscription") as activate:
        sdk.return_value.get.return_value = response(intent, "approved")
        result = client.post("/payments/mercado-pago/webhook?data.id=123", headers=signed(provider))
        assert result.status_code == 409
        session.add.assert_not_called()
        activate.assert_not_called()


@pytest.mark.parametrize("failure,code", [(404, 409), (429, 503), (503, 503), ("timeout", 503)])
def test_webhook_api_failure_and_successful_retry(
    provider: MercadoPagoPaymentProvider, intent: PaymentCreate,
    records: tuple[Payment, Subscription, Plan, User], failure: int | str, code: int,
) -> None:
    payment, subscription, plan, _ = records
    session: MagicMock = MagicMock(spec=Session)
    session.in_transaction.return_value = True
    session.scalar.side_effect = lambda statement: {
        Payment: payment, Subscription: subscription, Plan: plan,
    }[statement.column_descriptions[0]["entity"]]
    session.execute.return_value.one_or_none.return_value = (subscription, plan)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client, patch.object(provider._sdk, "payment") as sdk, \
            patch.object(SubscriptionService, "activate_subscription", autospec=True,
                         side_effect=SubscriptionService.activate_subscription) as activate:
        sdk.return_value.get.side_effect = [
            TimeoutError() if failure == "timeout" else {"status": failure, "response": {}},
            response(intent, "approved"), response(intent, "approved"),
        ]
        assert client.post("/payments/mercado-pago/webhook?data.id=123",
                           headers=signed(provider)).status_code == code
        assert payment.status == PaymentStatus.PENDING
        session.begin.assert_not_called()
        activate.assert_not_called()
        dates: tuple[datetime | None, datetime | None] | None = None
        for _ in range(2):
            assert client.post("/payments/mercado-pago/webhook?data.id=123",
                               headers=signed(provider)).status_code == 200
            current = (subscription.starts_at, subscription.expires_at)
            if dates is not None:
                assert current == dates
            dates = current
        assert payment.status == PaymentStatus.APPROVED
        assert activate.call_count == 1
        session.add.assert_not_called()


def test_postgresql_charge_and_duplicate_notification(provider: MercadoPagoPaymentProvider) -> None:
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: cobrança e conciliação PostgreSQL não verificadas")
    from sqlalchemy import update

    from scripts.seed_plans import seed_plans

    load_models()
    user_id: UUID = uuid4()
    subscription_id: UUID = uuid4()
    payment_id: UUID = uuid5(NAMESPACE_URL, f"dtf-manager:{provider.name}:{subscription_id}")
    with get_engine().connect() as connection:
        outer = connection.begin()
        try:
            seed_plans(connection)
            plan_id = connection.scalar(select(Plan.id).where(Plan.code == PlanCode.MONTHLY))
            connection.execute(update(Plan).where(Plan.id == plan_id).values(price=Decimal("29.90")))
            connection.execute(User.__table__.insert().values(id=user_id, name="Teste pagamento",
                cpf=f"{secrets.randbelow(10**11):011d}", email=f"{user_id.hex}@example.com",
                password_hash=secrets.token_hex(32), is_active=True))
            connection.execute(Subscription.__table__.insert().values(id=subscription_id, user_id=user_id,
                plan_id=plan_id, status=SubscriptionStatus.PENDING))
            data: ProviderPayment = ProviderPayment(provider_payment_id=str(secrets.randbelow(10**15)),
                amount=Decimal("29.90"), currency="BRL", status=PaymentStatus.PENDING,
                payment_id=payment_id, external_reference=subscription_id)
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as db:
                service: PaymentService = PaymentService(db)
                with patch.object(provider, "create_payment", return_value=data) as create, \
                        patch.object(provider, "fetch_payment", return_value=data):
                    for _ in range(2):
                        service.create_provider_charge(user_id=user_id, subscription_id=subscription_id,
                            expected_amount=Decimal("29.90"), description="DTF", provider=provider)
                    create.assert_called_once()
                data.status = PaymentStatus.APPROVED
                service.apply_provider_payment(payment_id, provider.name, data)
                dates = connection.execute(select(Subscription.starts_at, Subscription.expires_at)
                    .where(Subscription.id == subscription_id)).one()
                service.apply_provider_payment(payment_id, provider.name, data)
                assert connection.execute(select(Subscription.starts_at, Subscription.expires_at)
                    .where(Subscription.id == subscription_id)).one() == dates
                assert connection.scalar(select(func.count()).select_from(Payment)
                    .where(Payment.subscription_id == subscription_id)) == 1
        finally:
            outer.rollback()
