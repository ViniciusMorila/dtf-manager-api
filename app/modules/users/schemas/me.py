"""Resposta mínima da conta autenticada e da assinatura selecionada."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from app.modules.subscriptions.models import SubscriptionStatus
from app.modules.subscriptions.schemas.license import LicensePlan
from app.shared.schemas import UTCDateTime


class AccountSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SubscriptionStatus | None
    plan: LicensePlan | None
    starts_at: UTCDateTime | None
    expires_at: UTCDateTime | None


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str
    email: EmailStr
    subscription: AccountSubscription
