"""Projeção da assinatura sem pagamentos ou dados de outros usuários."""

from uuid import UUID

from app.modules.subscriptions.models import SubscriptionStatus
from app.shared.schemas import OutputSchema, UTCDateTime


class SubscriptionRead(OutputSchema):
    id: UUID
    plan_id: UUID
    status: SubscriptionStatus
    starts_at: UTCDateTime | None
    expires_at: UTCDateTime | None
    activated_at: UTCDateTime | None
    cancelled_at: UTCDateTime | None
    created_at: UTCDateTime
    updated_at: UTCDateTime
