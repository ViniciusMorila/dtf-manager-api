"""Adaptador Mercado Pago: SDK, Pix, assinatura e estados externos isolados."""
import json
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import urlsplit

import mercadopago
import requests
import simplejson
from mercadopago.config.request_options import RequestOptions
from mercadopago.errors.exceptions import MercadoPagoError
from mercadopago.http.http_client import HttpClient
from mercadopago.webhook.validator import (
    InvalidWebhookSignatureError,
    WebhookSignatureValidator,
)

from app.core.config import Settings
from app.modules.payments.contracts import (
    CheckoutCreate,
    CheckoutPreference,
    PaymentCreate,
    ProviderPayment,
)
from app.modules.payments.models import PaymentStatus
from app.modules.payments.provider import PaymentProvider


class ProviderUnavailable(Exception):
    """Falha externa: resposta segura, permite nova tentativa."""


class ProviderInvalidPayment(Exception):
    """Pagamento ausente ou resposta externa incompatível."""


class InvalidSignature(Exception):
    """Notificação não autenticada."""


class DecimalHttpClient(HttpClient):
    """Transporte injetado no SDK: JSON numérico exato, sem dinheiro em float.

    O SDK usa JSONEncoder padrão. O adaptador entrega amount como string e
    este transporte converte somente transaction_amount em número JSON Decimal.
    Timeout e headers continuam sendo configurados pelo SDK. Sem retry oculto.
    """

    def request(self, method: str, url: str, maxretries: int | None = None,
                retry_on: list[int] | None = None, backoff_factor: float | None = None,
                **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("data") is not None:
            payload: dict[str, Any] = json.loads(kwargs["data"], parse_float=Decimal)
            if "transaction_amount" in payload:
                payload["transaction_amount"] = Decimal(payload["transaction_amount"])
            for item in payload.get("items", []):
                item["unit_price"] = Decimal(item["unit_price"])
            kwargs["data"] = simplejson.dumps(payload, use_decimal=True, allow_nan=False)
        with requests.Session() as session:
            response: requests.Response = session.request(method, url, **kwargs)
            return {"status": response.status_code,
                    "response": json.loads(response.text, parse_float=Decimal) if response.content else None}


class MercadoPagoPaymentProvider(PaymentProvider):
    STATUSES: ClassVar[dict[str, PaymentStatus]] = {
        "pending": PaymentStatus.PENDING, "in_process": PaymentStatus.PENDING,
        "authorized": PaymentStatus.PENDING, "in_mediation": PaymentStatus.PENDING,
        "approved": PaymentStatus.APPROVED, "rejected": PaymentStatus.REJECTED,
        "cancelled": PaymentStatus.CANCELLED, "refunded": PaymentStatus.REFUNDED,
        "charged_back": PaymentStatus.REFUNDED,
    }

    def __init__(self, settings: Settings) -> None:
        try:
            settings.validate_mercado_pago()
        except ValueError:
            raise ProviderUnavailable("Pagamentos não configurados.") from None
        self._settings: Settings = settings
        assert settings.mercado_pago_access_token is not None
        self._sdk: mercadopago.SDK = mercadopago.SDK(
            settings.mercado_pago_access_token.get_secret_value(),
            request_options=RequestOptions(connection_timeout=8.0, max_retries=0),
            http_client=DecimalHttpClient())

    @property
    def name(self) -> str:
        return "mercado_pago"

    def validate_signature(self, signature: str, request_id: str, data_id: str) -> None:
        if not signature.strip() or not request_id.strip() or not data_id.strip():
            raise InvalidSignature()
        assert self._settings.mercado_pago_webhook_secret is not None
        try:
            WebhookSignatureValidator.validate(signature, request_id, data_id.lower(),
                self._settings.mercado_pago_webhook_secret.get_secret_value())
        except (InvalidWebhookSignatureError, ValueError, TypeError):
            raise InvalidSignature() from None

    def create_payment(self, payment: PaymentCreate, *, idempotency_key: str) -> ProviderPayment:
        if (payment.provider != self.name or payment.currency != "BRL" or payment.amount <= 0
                or payment.payment_id is None or not payment.payer_email or not payment.description
                or idempotency_key != str(payment.payment_id)):
            raise ProviderInvalidPayment("Dados de cobrança inválidos.")
        payload: dict[str, Any] = {
            "transaction_amount": str(payment.amount), "description": payment.description,
            "payment_method_id": "pix", "payer": {"email": payment.payer_email},
            "external_reference": str(payment.subscription_id),
            "metadata": {"payment_id": str(payment.payment_id)},
        }
        options: RequestOptions = RequestOptions(connection_timeout=8.0, max_retries=0,
            custom_headers={"x-idempotency-key": idempotency_key})
        try:
            result: Any = self._sdk.payment().create(payload, options)
        except (MercadoPagoError, requests.RequestException, TimeoutError, ValueError, TypeError):
            raise ProviderUnavailable("Falha na API de pagamentos.") from None
        return self._parse(result)

    def create_checkout(self, payment: CheckoutCreate) -> CheckoutPreference:
        if (payment.provider != self.name or payment.currency != "BRL" or payment.amount <= 0
                or payment.payment_id is None or not payment.description):
            raise ProviderInvalidPayment("Dados de checkout inválidos.")
        payload: dict[str, Any] = {
            "items": [{"id": str(payment.subscription_id), "title": payment.description,
                       "quantity": 1, "currency_id": "BRL", "unit_price": str(payment.amount)}],
            "external_reference": str(payment.subscription_id),
            "metadata": {"payment_id": str(payment.payment_id)},
            "notification_url": payment.notification_url,
            "expires": True, "expiration_date_to": payment.expires_at.isoformat(),
        }
        # Sem retry automático: Preferences não é tratado como Payments idempotente.
        try:
            result: Any = self._sdk.preference().create(payload,
                RequestOptions(connection_timeout=8.0, max_retries=0))
            if not isinstance(result, dict) or result.get("status") not in (200, 201):
                raise ProviderUnavailable("Falha ao criar checkout.")
            data: dict[str, Any] = result["response"]
            url = urlsplit(data["init_point"])
            if (url.scheme != "https" or url.hostname not in
                    {"www.mercadopago.com.br", "www.mercadopago.com", "mercadopago.com.br"}
                    or url.username or url.password or url.port not in (None, 443)):
                raise ValueError("URL de checkout inválida")
            return CheckoutPreference(preference_id=data["id"], init_point=data["init_point"])
        except (MercadoPagoError, requests.RequestException, TimeoutError, ValueError, TypeError, KeyError):
            raise ProviderUnavailable("Falha ao criar checkout.") from None

    def fetch_payment(self, provider_payment_id: str) -> ProviderPayment:
        if not provider_payment_id.isascii() or not provider_payment_id.isdigit() or len(provider_payment_id) > 64:
            raise ProviderInvalidPayment("Identificador inválido.")
        try:
            result: Any = self._sdk.payment().get(provider_payment_id)
        except (MercadoPagoError, requests.RequestException, TimeoutError, ValueError, TypeError):
            raise ProviderUnavailable("Falha na API de pagamentos.") from None
        payment: ProviderPayment = self._parse(result)
        if payment.provider_payment_id != provider_payment_id:
            raise ProviderInvalidPayment("Identificador divergente.")
        return payment

    def _parse(self, result: Any) -> ProviderPayment:
        if not isinstance(result, dict) or not isinstance(result.get("status"), int):
            raise ProviderInvalidPayment("Resposta de pagamento inválida.")
        if result["status"] == 404:
            raise ProviderInvalidPayment("Pagamento não encontrado.")
        if not 200 <= result["status"] < 300:
            raise ProviderUnavailable("Falha na API de pagamentos.")
        try:
            data: dict[str, Any] = result["response"]
            payment_id: str = str(data["id"])
            if (not payment_id.isascii() or not payment_id.isdigit() or len(payment_id) > 64
                    or data["currency_id"] != "BRL" or data["external_reference"] is None
                    or data["metadata"]["payment_id"] is None):
                raise ValueError("Identidade ou moeda inválida")
            if data["live_mode"] is not (self._settings.mercado_pago_environment == "production"):
                raise ValueError("Ambiente divergente")
            amount: Any = data["transaction_amount"]
            if isinstance(amount, (float, bool)):
                raise TypeError("Valor sem precisão decimal")
            return ProviderPayment(provider_payment_id=str(data["id"]), amount=Decimal(amount),
                currency=data["currency_id"], status=self.STATUSES[data["status"]],
                external_reference=data["external_reference"], payment_id=data["metadata"]["payment_id"])
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise ProviderInvalidPayment("Resposta de pagamento inválida.") from None
