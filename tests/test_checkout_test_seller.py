"""Independent provider evidence; no real requests or credentials."""
import secrets
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.modules.payments.mercado_pago import (
    MercadoPagoPaymentProvider,
    ProviderInvalidPayment,
    ProviderUnavailable,
)


@pytest.fixture
def evidence() -> dict[str, dict[str, Any]]:
    reference, local_id = str(uuid4()), str(uuid4())
    return {
        "payment": {"id": 123, "live_mode": True, "collector_id": 111,
            "order": {"id": 222, "type": "mercadopago"}, "currency_id": "BRL",
            "external_reference": reference, "metadata": {"payment_id": local_id},
            "transaction_amount": "79.90", "status": "approved"},
        "user": {"id": 111, "tags": ["test_user"]},
        "merchant_order": {"id": 222, "preference_id": "local-preference",
            "application_id": 333, "collector": {"id": 111}, "external_reference": reference,
            "payments": [{"id": 123}]},
        "preference": {"id": "local-preference", "client_id": 333, "collector_id": 111,
            "external_reference": reference, "metadata": {"payment_id": local_id}},
    }


def provider(environment: str = "test", **options: Any) -> MercadoPagoPaymentProvider:
    return MercadoPagoPaymentProvider(Settings(_env_file=None, environment="testing",
        mercado_pago_environment=environment, mercado_pago_checkout_test_seller=environment == "test",
        mercado_pago_application_id="333", mercado_pago_access_token=SecretStr(secrets.token_urlsafe(40)),
        mercado_pago_webhook_secret=SecretStr(secrets.token_urlsafe(40)), **options))


def query(p: MercadoPagoPaymentProvider, evidence: dict[str, dict[str, Any]]) -> Any:
    with ExitStack() as stack:
        for resource, data in evidence.items():
            mock = stack.enter_context(patch.object(p._sdk, resource))
            mock.return_value.get.return_value = {"status": 200, "response": data}
        return p.fetch_payment("123")


def test_verified_test_checkout_accepts_true_live_mode(evidence: dict[str, dict[str, Any]]) -> None:
    result = query(provider(), evidence)
    assert result.payment_environment == "test"
    assert result.verified_preference_id == "local-preference"


@pytest.mark.parametrize("resource,field,value", [
    ("payment", "order", None), ("payment", "application_id", 999),
    ("payment", "collector_id", 999), ("payment", "id", 999),
    ("payment", "currency_id", "USD"), ("payment", "transaction_amount", 79.9),
    ("user", "tags", []), ("user", "tags", None), ("user", "id", 999),
    ("merchant_order", "id", 999), ("merchant_order", "payments", []),
    ("merchant_order", "preference_id", None), ("merchant_order", "application_id", None),
    ("merchant_order", "application_id", 999), ("merchant_order", "collector", {"id": 999}),
    ("merchant_order", "external_reference", "wrong"),
    ("preference", "client_id", None), ("preference", "client_id", 999),
    ("preference", "id", "other"), ("preference", "collector_id", 999),
    ("preference", "metadata", {}), ("preference", "external_reference", "wrong"),
])
def test_missing_or_divergent_evidence_blocks(resource: str, field: str, value: Any,
                                             evidence: dict[str, dict[str, Any]]) -> None:
    evidence[resource][field] = value
    with pytest.raises(ProviderInvalidPayment):
        query(provider(), evidence)


def test_test_seller_never_accepted_as_real_even_with_true_live_mode(evidence: dict[str, dict[str, Any]]) -> None:
    with pytest.raises(ProviderInvalidPayment):
        query(provider("production"), evidence)


def test_real_seller_cannot_use_test_checkout_exception(evidence: dict[str, dict[str, Any]]) -> None:
    evidence["user"]["tags"] = ["normal"]
    with pytest.raises(ProviderInvalidPayment):
        query(provider(), evidence)


def test_false_live_mode_never_accepted_as_real(evidence: dict[str, dict[str, Any]]) -> None:
    evidence["user"]["tags"] = ["normal"]
    evidence["payment"]["live_mode"] = False
    with pytest.raises(ProviderInvalidPayment):
        query(provider("production"), evidence)


def test_real_seller_and_true_live_mode_remain_valid(evidence: dict[str, dict[str, Any]]) -> None:
    evidence["user"]["tags"] = ["normal"]
    assert query(provider("production"), evidence).payment_environment == "production"


def test_production_seller_mismatch_blocks(evidence: dict[str, dict[str, Any]]) -> None:
    evidence["user"] = {"id": 999, "tags": ["normal"]}
    with pytest.raises(ProviderInvalidPayment):
        query(provider("production"), evidence)


def test_missing_application_or_opt_in_blocks(evidence: dict[str, dict[str, Any]]) -> None:
    p = provider()
    p._settings.mercado_pago_application_id = None
    with pytest.raises(ProviderInvalidPayment):
        query(p, evidence)
    p = provider()
    p._settings.mercado_pago_checkout_test_seller = False
    with pytest.raises(ProviderInvalidPayment):
        query(p, evidence)


def test_test_flow_blocked_in_real_api(evidence: dict[str, dict[str, Any]]) -> None:
    p = provider()
    p._settings.environment = "production"
    with pytest.raises(ProviderInvalidPayment):
        query(p, evidence)


def test_direct_pix_keeps_strict_environment(evidence: dict[str, dict[str, Any]]) -> None:
    p = provider()
    with pytest.raises(ProviderInvalidPayment):
        p._parse({"status": 200, "response": evidence["payment"]})


def test_evidence_timeout_never_grants_confirmation(evidence: dict[str, dict[str, Any]]) -> None:
    p = provider()
    with patch.object(p._sdk, "payment") as payment, patch.object(p._sdk, "user") as user:
        payment.return_value.get.return_value = {"status": 200, "response": evidence["payment"]}
        user.return_value.get.side_effect = TimeoutError()
        with pytest.raises(ProviderUnavailable):
            p.fetch_payment("123")
