import json
import secrets
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.modules.payments.mercado_pago import MercadoPagoPaymentProvider


@pytest.mark.parametrize("live,environment,reason", [
    (True, "test", "environment_mismatch"),
    (False, "production", "environment_mismatch"),
    (False, "test", "valid"),
])
def test_read_only_diagnostic(live: bool, environment: str, reason: str) -> None:
    settings = Settings(_env_file=None, environment="testing",
                        mercado_pago_environment=environment,
                        mercado_pago_access_token=SecretStr(secrets.token_urlsafe(40)),
                        mercado_pago_webhook_secret=SecretStr(secrets.token_urlsafe(40)))
    provider = MercadoPagoPaymentProvider(settings)
    data: dict[str, Any] = {"id": 182705996051, "live_mode": live,
        "collector_id": 123, "application_id": 456, "currency_id": "BRL",
        "external_reference": str(uuid4()), "metadata": {"payment_id": str(uuid4())},
        "transaction_amount": "79.90", "status": "approved", "payer": {"email": "private@example.com"}}
    with patch.object(provider._sdk, "payment") as payment, patch.object(provider._sdk, "user") as user:
        payment.return_value.get.return_value = {"status": 200, "response": data}
        user.return_value.get.return_value = {"status": 200, "response": {
            "id": 123, "tags": ["test_user"], "email": "private@example.com"}}
        event = provider.diagnose_payment_environment("456")
        payment.return_value.get.assert_called_once_with("182705996051")
        user.return_value.get.assert_called_once_with()
        assert event["live_mode"] is live
        assert event["loaded_environment"] == environment
        assert event["validation_result"] == reason
        assert event["seller_matches_token_account"] is True
        assert event["token_account_is_test_user"] is True
        assert event["payment_application_matches_expected"] is True
        assert event["token_application_verified"] is False
        output = json.dumps(event)
        assert "private@example.com" not in output and '"collector_id"' not in output
        for secret in (settings.mercado_pago_access_token, settings.mercado_pago_webhook_secret):
            assert secret is not None and secret.get_secret_value() not in output


def test_failed_queries_do_not_emit_exception_contents() -> None:
    provider = MercadoPagoPaymentProvider(Settings(_env_file=None, environment="testing",
        mercado_pago_environment="test", mercado_pago_access_token=SecretStr(secrets.token_urlsafe(40)),
        mercado_pago_webhook_secret=SecretStr(secrets.token_urlsafe(40))))
    with patch.object(provider._sdk, "payment") as payment, patch.object(provider._sdk, "user") as user:
        payment.return_value.get.side_effect = RuntimeError("sensitive response")
        user.return_value.get.return_value = {"status": 403, "response": {"message": "sensitive response"}}
        event = provider.diagnose_payment_environment("456")
    assert event["seller_matches_token_account"] is None
    assert event["payment_fetch_ok"] is False
    assert "sensitive" not in json.dumps(event)
