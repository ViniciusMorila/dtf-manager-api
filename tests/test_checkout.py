"""Checkout Pro com SDK/rede simulados, sem credenciais ou cobranças reais."""
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mercadopago.errors.exceptions import MPBadRequestError
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user
from app.modules.payments.contracts import CheckoutCreate, CheckoutPreference
from app.modules.payments.diagnostics import log_checkout_failure
from app.modules.payments.mercado_pago import (
    MercadoPagoPaymentProvider,
    ProviderUnavailable,
)
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.routes import get_provider
from app.modules.payments.service import PaymentConflict, PaymentService
from app.modules.plans.models import Plan, PlanCode
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.users.models import User
from app.shared.security.jwt import create_access_token
from app.shared.utils.calendar import add_calendar_months


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("API_PUBLIC_BASE_URL", "https://api.example.com")
    def reject(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Rede Mercado Pago proibida nos testes")
    monkeypatch.setattr("requests.sessions.Session.request", reject)


@pytest.fixture
def provider() -> MercadoPagoPaymentProvider:
    return MercadoPagoPaymentProvider(Settings(_env_file=None,
        mercado_pago_access_token=SecretStr(secrets.token_urlsafe(40)),
        mercado_pago_webhook_secret=SecretStr(secrets.token_urlsafe(40)), mercado_pago_environment="test"))


@pytest.fixture
def db() -> MagicMock:
    session = MagicMock(spec=Session)
    user = User(id=uuid4(), name="Teste", email="test@example.com", is_active=True)
    plan = Plan(id=uuid4(), code=PlanCode.MONTHLY, name="Mensal", price=Decimal("79.90"),
                is_active=True, is_lifetime=False, duration_months=1)
    subscription = Subscription(id=uuid4(), user_id=user.id, plan_id=plan.id,
        status=SubscriptionStatus.PENDING, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    session.records = {User: user, Plan: plan, Subscription: subscription, Payment: None}
    session.get.side_effect = lambda model, key: session.records[model]
    def scalar(statement: Any) -> Any:
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params
        record = session.records[entity]
        if record is not None and "user_id_1" in params and params["user_id_1"] != record.user_id:
            return None
        if record is not None and "id_1" in params and params["id_1"] != record.id:
            return None
        return record
    session.scalar.side_effect = scalar
    def scalars(statement: Any) -> MagicMock:
        result = MagicMock()
        entity = statement.column_descriptions[0]["entity"]
        record = session.records[entity]
        result.all.return_value = [record] if record is not None else []
        return result
    session.scalars.side_effect = scalars
    session.add.side_effect = lambda record: session.records.update({type(record): record})
    session.execute.return_value.one_or_none.return_value = (subscription, plan)
    session.in_transaction.return_value = True
    return session


def client_for(db: MagicMock, provider: MercadoPagoPaymentProvider, authenticated: bool = True) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_provider] = lambda: provider
    if authenticated:
        user = db.records[User]
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user.id, user.name, user.email)
    return TestClient(app)


def start(client: TestClient, db: MagicMock, **extra: Any) -> Any:
    return client.post("/payments/checkout", json={"subscription_id": str(db.records[Subscription].id), **extra})


def preference() -> CheckoutPreference:
    return CheckoutPreference(preference_id="test-preference", init_point="https://www.mercadopago.com.br/checkout/test")


def test_plans_public_database_prices(db: MagicMock, provider: MercadoPagoPaymentProvider) -> None:
    db.records[Plan].price = Decimal("87.65")
    with client_for(db, provider, False) as client:
        result = client.get("/plans")
    assert result.status_code == 200
    assert result.json() == [{"code": "MONTHLY", "name": "Mensal", "price": "87.65", "is_lifetime": False}]
    sql = str(db.scalars.call_args.args[0])
    assert "plans.is_active IS true" in sql


def test_checkout_and_payment_require_auth(db: MagicMock, provider: MercadoPagoPaymentProvider) -> None:
    with client_for(db, provider, False) as client:
        assert start(client, db).status_code == 401
        assert client.get(f"/payments/{uuid4()}").status_code == 401
    db.add.assert_not_called()


def test_checkout_uses_real_access_jwt(db: MagicMock, provider: MercadoPagoPaymentProvider,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_urlsafe(40))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_urlsafe(40))
    token = create_access_token(db.records[User].id, settings=Settings(_env_file=None))
    with client_for(db, provider, False) as client, patch.object(provider, "create_checkout", return_value=preference()):
        result = client.post("/payments/checkout", headers={"Authorization": f"Bearer {token}"},
            json={"subscription_id": str(db.records[Subscription].id)})
        assert result.status_code == 200
        assert db.records[Payment].user_id == db.records[User].id


def test_missing_public_url_no_intent(db: MagicMock, provider: MercadoPagoPaymentProvider,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_PUBLIC_BASE_URL")
    with client_for(db, provider) as client, patch.object(provider, "create_checkout") as create:
        assert start(client, db).status_code == 503
        create.assert_not_called()
        db.add.assert_not_called()


@pytest.mark.parametrize("extra", [{"amount": "0.01"}, {"price": "0.01"}, {"currency": "USD"},
    {"user_id": str(uuid4())}, {"external_reference": str(uuid4())}, {"plan_code": "LIFETIME"}])
def test_client_cannot_control_charge(db: MagicMock, provider: MercadoPagoPaymentProvider, extra: dict[str, Any]) -> None:
    with client_for(db, provider) as client:
        assert start(client, db, **extra).status_code == 422
    db.add.assert_not_called()


def test_checkout_other_owner_and_missing(db: MagicMock, provider: MercadoPagoPaymentProvider) -> None:
    db.records[Subscription].user_id = uuid4()
    with client_for(db, provider) as client, patch.object(provider, "create_checkout") as create:
        assert start(client, db).status_code == 404
        assert client.post("/payments/checkout", json={"subscription_id": str(uuid4())}).status_code == 404
        create.assert_not_called()


def test_reuses_checkout_and_server_price(db: MagicMock, provider: MercadoPagoPaymentProvider) -> None:
    db.records[Plan].price = Decimal("88.77")
    def create(request: CheckoutCreate) -> CheckoutPreference:
        # A transação de intenção já encerrou antes da chamada externa.
        assert db.begin.return_value.__exit__.call_count >= 1
        assert db.records[Payment].payment_metadata["checkout_state"] == "creating"
        assert request.amount == Decimal("88.77")
        assert request.notification_url == "https://api.example.com/payments/mercado-pago/webhook"
        return preference()
    with client_for(db, provider) as client, patch.object(provider, "create_checkout", side_effect=create) as create_mock:
        results = [start(client, db) for _ in range(10)]
        assert all(r.status_code == 200 for r in results)
        assert all(r.json() == results[0].json() for r in results)
        data = results[0].json()
        assert data == {"payment_id": str(db.records[Payment].id), "status": "PENDING",
            "amount": "88.77", "currency": "BRL", "init_point": preference().init_point}
        assert client.get(f"/payments/{data['payment_id']}").json() == {
            key: value for key, value in data.items() if key != "init_point"}
        create_mock.assert_called_once()
        db.add.assert_called_once()


def test_uncertain_creation_never_retries(db: MagicMock, provider: MercadoPagoPaymentProvider) -> None:
    with client_for(db, provider) as client, patch.object(provider, "create_checkout", side_effect=ProviderUnavailable) as create:
        assert start(client, db).status_code == 503
        assert start(client, db).status_code == 409
        create.assert_called_once()
        assert db.records[Payment].status == PaymentStatus.PENDING


@pytest.mark.parametrize("sdk_exception", [False, True])
def test_safe_provider_diagnostics(db: MagicMock, provider: MercadoPagoPaymentProvider,
                                   caplog: pytest.LogCaptureFixture, sdk_exception: bool) -> None:
    secret = secrets.token_urlsafe(40)
    body = {"error": "invalid_items", "message": "unit_price invalid.",
            "cause": [{"code": "invalid_items", "description": secret}],
            "Authorization": secret, "payload": {"password": secret}}
    with client_for(db, provider) as client, patch.object(provider._sdk, "preference") as sdk:
        if sdk_exception:
            sdk.return_value.create.side_effect = MPBadRequestError(400, body)
        else:
            sdk.return_value.create.return_value = {"status": 400, "response": body}
        result = start(client, db)
        assert result.status_code == 503
        assert result.json() == {"detail": "Checkout temporariamente indisponível."}
        assert start(client, db).status_code == 409
        sdk.return_value.create.assert_called_once()
    events = [json.loads(r.message) for r in caplog.records if r.name.endswith("diagnostics")]
    assert events[0]["provider_http_status"] == 400
    assert events[0]["provider_code"] == "invalid_items"
    assert events[0]["message"] == "unit_price invalid."
    assert events[0]["provider_causes"][0]["message"] == "[REDACTED]"
    assert events[0]["exception_type"] == ("MPBadRequestError" if sdk_exception else None)
    assert events[-1]["stage"] == "checkout_conflict"
    assert secret not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


@pytest.mark.parametrize("failure", ["timeout", "non_json", "invalid_response", "db_before", "db_after"])
def test_checkout_failure_stages(db: MagicMock, provider: MercadoPagoPaymentProvider,
                                caplog: pytest.LogCaptureFixture, failure: str) -> None:
    secret = secrets.token_urlsafe(40)
    with patch("app.modules.payments.mercado_pago.requests.Session") as http, client_for(db, provider) as client:
        request = http.return_value.__enter__.return_value.request
        response = request.return_value
        response.status_code = 502 if failure == "non_json" else 201
        response.content = b"body"
        response.text = secret if failure == "non_json" else json.dumps(
            {"id": "pref", "init_point": preference().init_point})
        if failure == "timeout":
            request.side_effect = TimeoutError(secret)
        elif failure == "invalid_response":
            response.text = json.dumps({"init_point": "https://evil.example/" + secret})
        elif failure == "db_before":
            db.flush.side_effect = SQLAlchemyError(secret)
        elif failure == "db_after":
            db.flush.side_effect = [None, SQLAlchemyError(secret)]
        assert start(client, db).status_code == 503
    event = next(json.loads(r.message) for r in caplog.records if r.name.endswith("diagnostics"))
    assert event["stage"] == {
        "timeout": "preference_create", "non_json": "preference_response_decode",
        "invalid_response": "preference_response_validation",
        "db_before": "checkout_intent_persistence", "db_after": "checkout_result_persistence",
    }[failure]
    assert event["provider_http_status"] == (502 if failure == "non_json" else
                                            201 if failure == "invalid_response" else None)
    assert secret not in caplog.text


def test_railway_notification_url(db: MagicMock, provider: MercadoPagoPaymentProvider,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PUBLIC_BASE_URL", "https://dtf-manager-api-production.up.railway.app/")
    with client_for(db, provider) as client, patch.object(provider, "create_checkout", return_value=preference()) as create:
        assert start(client, db).status_code == 200
    assert create.call_args.args[0].notification_url == (
        "https://dtf-manager-api-production.up.railway.app/payments/mercado-pago/webhook")


def test_diagnostics_omit_unknown_external_text(caplog: pytest.LogCaptureFixture) -> None:
    secret = secrets.token_urlsafe(40)
    log_checkout_failure(stage="preference_create", error=ValueError(secret), result={
        "status": secret, "response": {"error": secret, "message": secret,
        "cause": [{"code": secret, "description": secret}], "headers": {"Authorization": secret}}})
    event = json.loads(caplog.records[0].message)
    assert event["provider_http_status"] is None
    assert event["provider_code"] == event["message"] == "[REDACTED]"
    assert event["provider_causes"] == [{"code": "[REDACTED]", "message": "[REDACTED]"}]
    assert secret not in caplog.text


def test_overlapping_request_cannot_create_twice(db: MagicMock, provider: MercadoPagoPaymentProvider) -> None:
    service = PaymentService(db)
    def create(request: CheckoutCreate) -> CheckoutPreference:
        with pytest.raises(PaymentConflict):
            service.create_checkout(user_id=request.user_id, subscription_id=request.subscription_id,
                provider=provider, notification_url=request.notification_url)
        return preference()
    with client_for(db, provider) as client, patch.object(provider, "create_checkout", side_effect=create) as mocked:
        assert start(client, db).status_code == 200
        mocked.assert_called_once()


@pytest.mark.parametrize("change", ["expired", "price", "approved", "pix"])
def test_existing_checkout_conflicts(db: MagicMock, provider: MercadoPagoPaymentProvider, change: str) -> None:
    with client_for(db, provider) as client, patch.object(provider, "create_checkout", return_value=preference()) as create:
        assert start(client, db).status_code == 200
        payment = db.records[Payment]
        if change == "expired":
            payment.payment_metadata["checkout_expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        elif change == "price":
            db.records[Plan].price = Decimal("90.00")
        elif change == "approved":
            payment.status = PaymentStatus.APPROVED
        else:
            payment.payment_metadata = {"description": "Pix"}
        assert start(client, db).status_code == 409
        create.assert_called_once()


def test_payment_owner_isolation(db: MagicMock, provider: MercadoPagoPaymentProvider) -> None:
    with client_for(db, provider) as client, patch.object(provider, "create_checkout", return_value=preference()):
        payment_id = start(client, db).json()["payment_id"]
        db.records[Payment].user_id = uuid4()
        assert client.get(f"/payments/{payment_id}").status_code == 404
        assert client.get(f"/payments/{uuid4()}").status_code == 404


def test_real_sdk_preference_decimal_payload(db: MagicMock, provider: MercadoPagoPaymentProvider) -> None:
    with patch("app.modules.payments.mercado_pago.requests.Session") as http, client_for(db, provider) as client:
        result = http.return_value.__enter__.return_value.request.return_value
        result.status_code = 201
        result.content = b"json"
        result.text = json.dumps({"id": "pref-test", "init_point": preference().init_point,
                                  "sandbox_init_point": "https://sandbox.example.com"})
        assert start(client, db).status_code == 200
        call = http.return_value.__enter__.return_value.request.call_args
        assert call.args == ("POST", "https://api.mercadopago.com/checkout/preferences")
        payload = json.loads(call.kwargs["data"], parse_float=Decimal)
        assert payload["items"][0]["unit_price"] == Decimal("79.90")
        assert '"unit_price": 79.90' in call.kwargs["data"]
        assert payload["items"][0]["currency_id"] == "BRL"
        assert "DTF Manager" in payload["items"][0]["title"]
        assert payload["external_reference"] == str(db.records[Subscription].id)
        assert payload["metadata"]["payment_id"] == str(db.records[Payment].id)
        assert payload["expires"] is True
        assert call.kwargs["timeout"] == 8.0


@pytest.mark.parametrize("result", [
    {"status": 500, "response": {}}, {"status": 201, "response": {}},
    {"status": 201, "response": {"id": "pref", "init_point": "http://www.mercadopago.com.br/pay"}},
    {"status": 201, "response": {"id": "pref", "init_point": "https://evil.example/pay"}},
    {"status": 201, "response": {"id": "pref", "init_point": "https://www.mercadopago.com.br.evil.example/pay"}},
])
def test_preference_failure_keeps_intent_and_blocks_retry(db: MagicMock, provider: MercadoPagoPaymentProvider,
                                                         result: dict[str, Any]) -> None:
    with client_for(db, provider) as client, patch.object(provider._sdk, "preference") as sdk:
        sdk.return_value.create.return_value = result
        assert start(client, db).status_code == 503
        assert start(client, db).status_code == 409
        sdk.return_value.create.assert_called_once()
        assert db.records[Payment].payment_metadata["checkout_state"] == "creating"


def test_pix_cannot_replace_checkout(db: MagicMock, provider: MercadoPagoPaymentProvider) -> None:
    with client_for(db, provider) as client, patch.object(provider, "create_checkout", return_value=preference()):
        assert start(client, db).status_code == 200
    with patch.object(provider, "create_payment") as pix:
        with pytest.raises(PaymentConflict):
            PaymentService(db).create_provider_charge(user_id=db.records[User].id,
                subscription_id=db.records[Subscription].id, expected_amount=db.records[Plan].price,
                description="DTF Manager", provider=provider)
        pix.assert_not_called()


def signed(provider: MercadoPagoPaymentProvider) -> dict[str, str]:
    # Timestamp antigo continua aceito: reentregas são protegidas pela idempotência.
    ts = "1704908010"
    key = provider._settings.mercado_pago_webhook_secret
    assert key is not None
    digest = hmac.new(key.get_secret_value().encode(),
        f"id:123;request-id:test;ts:{ts};".encode(), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={ts},v1={digest}", "x-request-id": "test"}


@pytest.mark.parametrize("code,months", [(PlanCode.MONTHLY, 1), (PlanCode.SEMIANNUAL, 6),
    (PlanCode.ANNUAL, 12), (PlanCode.LIFETIME, None)])
def test_checkout_webhook_activation_all_plans(db: MagicMock, provider: MercadoPagoPaymentProvider,
                                              code: PlanCode, months: int | None) -> None:
    plan = db.records[Plan]
    plan.code, plan.is_lifetime = code, months is None
    with client_for(db, provider) as client, patch.object(provider, "create_checkout", return_value=preference()):
        data = start(client, db).json()
        payment = db.records[Payment]
        external = {"status": 200, "response": {"id": 123, "transaction_amount": payment.amount,
            "currency_id": "BRL", "status": "approved", "external_reference": str(payment.subscription_id),
            "metadata": {"payment_id": str(payment.id)}, "live_mode": False}}
        with patch.object(provider._sdk, "payment") as sdk:
            sdk.return_value.get.return_value = external
            for _ in range(10):
                result = client.post("/payments/mercado-pago/webhook?data.id=123", headers=signed(provider),
                    json={"status": "rejected", "data": {"id": "body-not-trusted"}})
                assert result.status_code == 200
                sub = db.records[Subscription]
                if _ == 0:
                    dates = (sub.starts_at, sub.expires_at)
                assert (sub.starts_at, sub.expires_at) == dates
            assert sdk.return_value.get.call_count == 10
        assert client.get(f"/payments/{data['payment_id']}").json()["status"] == "APPROVED"
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.expires_at == (add_calendar_months(sub.starts_at, months) if months else None)
        db.execute.return_value.all.return_value = [(sub, plan)]
        license_data = client.get("/license/status").json()
        assert license_data["active"] is True
        assert license_data["is_lifetime"] is (months is None)


@pytest.mark.parametrize("field,value", [("transaction_amount", Decimal("0.01")), ("currency_id", "USD"),
    ("external_reference", str(uuid4())), ("metadata", {"payment_id": str(uuid4())}), ("live_mode", True)])
def test_checkout_webhook_rejects_divergences(db: MagicMock, provider: MercadoPagoPaymentProvider,
                                             field: str, value: Any) -> None:
    with client_for(db, provider) as client, patch.object(provider, "create_checkout", return_value=preference()):
        assert start(client, db).status_code == 200
        payment = db.records[Payment]
        data = {"id": 123, "transaction_amount": payment.amount, "currency_id": "BRL", "status": "approved",
            "external_reference": str(payment.subscription_id), "metadata": {"payment_id": str(payment.id)},
            "live_mode": False, field: value}
        with patch.object(provider._sdk, "payment") as sdk:
            sdk.return_value.get.return_value = {"status": 200, "response": data}
            assert client.post("/payments/mercado-pago/webhook?data.id=123", headers=signed(provider)).status_code == 409
        assert payment.status == PaymentStatus.PENDING
        assert db.records[Subscription].status == SubscriptionStatus.PENDING


@pytest.mark.parametrize("url", ["http://api.example.com", "https://u:p@api.example.com",
    "https://api.example.com/path", "https://api.example.com?token=x", "https://api.example.com#x"])
def test_public_url_rejects_unsafe_configuration(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_public_base_url=url)
