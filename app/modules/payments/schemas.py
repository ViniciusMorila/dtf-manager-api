"""Contratos HTTP mínimos; valores financeiros são strings decimais."""
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import field_serializer

from app.modules.payments.models import PaymentStatus
from app.shared.schemas import InputSchema, OutputSchema


class CheckoutRequest(InputSchema):
    subscription_id: UUID


class PaymentResponse(OutputSchema):
    payment_id: UUID
    status: PaymentStatus
    amount: Decimal
    currency: Literal["BRL"]

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return format(value, ".2f")


class CheckoutResponse(PaymentResponse):
    init_point: str
