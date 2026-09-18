"""Resultado explícito de uma futura consulta ao banco, não derivado do JWT."""

from pydantic import BaseModel, ConfigDict

from app.modules.plans.models import PlanCode
from app.modules.subscriptions.models import SubscriptionStatus
from app.shared.schemas import UTCDateTime


class LicenseRead(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    is_valid: bool
    plan_code: PlanCode | None
    status: SubscriptionStatus | None
    expires_at: UTCDateTime | None
    checked_at: UTCDateTime
