"""O cliente seleciona um código; não define duração nem estado do plano."""

from app.modules.plans.models import PlanCode
from app.shared.schemas import InputSchema


class PlanSelection(InputSchema):
    plan_code: PlanCode
