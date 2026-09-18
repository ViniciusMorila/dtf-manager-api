"""Resultado público da avaliação centralizada de licença."""

from pydantic import BaseModel, ConfigDict

from app.modules.plans.models import PlanCode
from app.modules.subscriptions.models import SubscriptionStatus
from app.shared.schemas import UTCDateTime


class LicensePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: PlanCode
    name: str


class LicenseStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    active: bool
    status: SubscriptionStatus | None
    plan: LicensePlan | None
    starts_at: UTCDateTime | None
    expires_at: UTCDateTime | None
    is_lifetime: bool
