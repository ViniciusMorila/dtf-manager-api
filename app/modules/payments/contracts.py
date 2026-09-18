"""Contratos internos de pagamento; não são endpoints públicos."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.payments.models import PaymentStatus

Money = Annotated[Decimal, Field(strict=True, ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)]
ProviderName = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=50)]
ExternalID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Currency = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{3}$")]


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    subscription_id: UUID
    provider: ProviderName
    amount: Money
    currency: Currency
    provider_payment_id: ExternalID | None = None
    payment_id: UUID | None = None
    description: str | None = Field(default=None, min_length=1, max_length=200)
    payer_email: str | None = None


class PaymentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: PaymentStatus
    provider_payment_id: ExternalID | None = None


class ProviderPayment(BaseModel):
    """Resultado de consulta validada por um adaptador confiável futuro."""
    model_config = ConfigDict(extra="forbid")
    provider_payment_id: ExternalID
    amount: Money
    currency: Currency
    status: PaymentStatus
    external_reference: UUID | None = None
    payment_id: UUID | None = None


class PaymentRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: UUID
    user_id: UUID
    subscription_id: UUID
    provider: str
    provider_payment_id: str | None
    amount: Decimal
    currency: str
    status: PaymentStatus
