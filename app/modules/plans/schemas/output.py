"""Campos públicos do catálogo de planos."""

from uuid import UUID

from pydantic import PositiveInt

from app.modules.plans.models import PlanCode
from app.shared.schemas import OutputSchema


class PlanRead(OutputSchema):
    id: UUID
    code: PlanCode
    name: str
    duration_months: PositiveInt | None
    is_lifetime: bool
    is_active: bool
