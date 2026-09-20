"""Catálogo público somente leitura, com preços consultados no banco."""
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import field_serializer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionDependency
from app.modules.plans.models import Plan, PlanCode
from app.shared.schemas import OutputSchema

router = APIRouter(prefix="/plans", tags=["plans"])


class PublicPlan(OutputSchema):
    code: PlanCode
    name: str
    price: Decimal | None
    is_lifetime: bool

    @field_serializer("price")
    def serialize_price(self, value: Decimal | None) -> str | None:
        return format(value, ".2f") if value is not None else None


@router.get("", response_model=list[PublicPlan])
def list_plans(session: SessionDependency) -> list[PublicPlan]:
    try:
        with session.begin():
            plans = session.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.code)).all()
            return [PublicPlan.model_validate(plan) for plan in plans]
    except SQLAlchemyError:
        raise HTTPException(503, "Planos temporariamente indisponíveis.") from None
