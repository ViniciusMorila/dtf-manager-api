"""Planos disponíveis; escolher um plano não ativa uma assinatura."""

from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    Integer,
    Numeric,
    String,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.modules.subscriptions.models import Subscription


class PlanCode(StrEnum):
    MONTHLY = "MONTHLY"
    SEMIANNUAL = "SEMIANNUAL"
    ANNUAL = "ANNUAL"
    LIFETIME = "LIFETIME"


class Plan(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("price IS NULL OR price > 0", name="positive_price"),
        CheckConstraint(
            "(code = 'LIFETIME' AND is_lifetime AND duration_months IS NULL) OR "
            "(code = 'MONTHLY' AND NOT is_lifetime AND duration_months IS NOT NULL AND duration_months = 1) OR "
            "(code = 'SEMIANNUAL' AND NOT is_lifetime AND duration_months IS NOT NULL AND duration_months = 6) OR "
            "(code = 'ANNUAL' AND NOT is_lifetime AND duration_months IS NOT NULL AND duration_months = 12)",
            name="valid_duration",
        ),
    )

    code: Mapped[PlanCode] = mapped_column(Enum(PlanCode, name="plan_code", validate_strings=True), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2, asdecimal=True))
    duration_months: Mapped[int | None] = mapped_column(Integer)
    is_lifetime: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="plan")
