"""Porta para futuros provedores; nenhuma implementação de rede nesta etapa."""

from abc import ABC, abstractmethod

from app.modules.payments.contracts import (
    CheckoutCreate,
    CheckoutPreference,
    PaymentCreate,
    ProviderPayment,
)


class PaymentProvider(ABC):
    def create_checkout(self, payment: CheckoutCreate) -> CheckoutPreference:
        """Capacidade opcional; provedores existentes de pagamento direto são preservados."""
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """Identificador estável do provedor."""
        raise NotImplementedError

    @abstractmethod
    def create_payment(self, payment: PaymentCreate, *, idempotency_key: str) -> ProviderPayment:
        """Adaptadores futuros deverão encaminhar a chave de idempotência."""
        raise NotImplementedError

    @abstractmethod
    def fetch_payment(self, provider_payment_id: str) -> ProviderPayment:
        """Consulta confiável que poderá confirmar o estado no provedor."""
        raise NotImplementedError
