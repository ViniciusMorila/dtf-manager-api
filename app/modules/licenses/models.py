"""Licenças do aplicativo desktop, chaves de ativação, trava de teste e resets."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import CreatedAt, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.modules.users.models import User


class LicensePlanCode(StrEnum):
    """Códigos estáveis do contrato com o aplicativo."""

    TESTE = "TESTE"
    MENSAL = "MENSAL"
    SEMESTRAL = "SEMESTRAL"
    ANUAL = "ANUAL"
    VITALICIO = "VITALICIO"


class LicenseState(StrEnum):
    ATIVA = "ATIVA"
    EXPIRADA = "EXPIRADA"
    BLOQUEADA = "BLOQUEADA"


class ActivationKeyStatus(StrEnum):
    DISPONIVEL = "DISPONIVEL"
    USADA = "USADA"
    CANCELADA = "CANCELADA"


# Uma mesma máquina não pode ter licenças ATIVAS de contas diferentes.
ACTIVE_MACHINE_INDEX: str = "ux_licenses_active_machine"
TRIAL_MACHINE_CONSTRAINT: str = "uq_trial_usages_machine_id"
TRIAL_PHONE_CONSTRAINT: str = "uq_trial_usages_phone"


class License(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "licenses"
    __table_args__ = (
        CheckConstraint(
            "(plan = 'VITALICIO' AND expires_at IS NULL) OR (plan <> 'VITALICIO' AND expires_at IS NOT NULL)",
            name="lifetime_expiration",
        ),
        CheckConstraint("reset_count >= 0", name="nonnegative_reset_count"),
        CheckConstraint("phone IS NULL OR phone ~ '^[0-9]{10,11}$'", name="phone_digits"),
        Index(
            ACTIVE_MACHINE_INDEX, "machine_id", unique=True,
            postgresql_where=text("status = 'ATIVA' AND machine_id IS NOT NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    plan: Mapped[LicensePlanCode] = mapped_column(
        Enum(LicensePlanCode, name="license_plan", validate_strings=True), index=True,
    )
    status: Mapped[LicenseState] = mapped_column(
        Enum(LicenseState, name="license_status", validate_strings=True),
        default=LicenseState.ATIVA, server_default="ATIVA", index=True,
    )
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    machine_id: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(11), index=True)
    reset_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="license")
    resets: Mapped[list["LicenseReset"]] = relationship(back_populates="license")


class ActivationKey(UUIDPrimaryKey, CreatedAt, Base):
    """Somente o digest da chave é persistido; a chave completa é exibida uma vez."""

    __tablename__ = "activation_keys"

    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_suffix: Mapped[str] = mapped_column(String(4))
    plan: Mapped[LicensePlanCode] = mapped_column(
        Enum(LicensePlanCode, name="license_plan", validate_strings=True), index=True,
    )
    status: Mapped[ActivationKeyStatus] = mapped_column(
        Enum(ActivationKeyStatus, name="activation_key_status", validate_strings=True),
        default=ActivationKeyStatus.DISPONIVEL, server_default="DISPONIVEL", index=True,
    )
    customer_name: Mapped[str] = mapped_column(String(200))
    customer_contact: Mapped[str] = mapped_column(String(200))
    license_id: Mapped[UUID | None] = mapped_column(ForeignKey("licenses.id", ondelete="SET NULL"), index=True)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrialUsage(UUIDPrimaryKey, CreatedAt, Base):
    """Trava permanente do teste: sobrevive à exclusão da conta (user_id vira nulo).

    phone é nulo quando a máquina foi registrada ao vincular uma licença TESTE sem
    telefone (contas antigas do backfill) ou uma máquina nova após reset.
    """

    __tablename__ = "trial_usages"

    machine_id: Mapped[str] = mapped_column(String(128), unique=True)
    phone: Mapped[str | None] = mapped_column(String(11), unique=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class LicenseReset(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "license_resets"
    __table_args__ = (Index("ix_license_resets_license_created", "license_id", "created_at"),)

    license_id: Mapped[UUID] = mapped_column(ForeignKey("licenses.id", ondelete="CASCADE"))
    previous_machine_id: Mapped[str | None] = mapped_column(String(128))
    performed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    forced: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    license: Mapped[License] = relationship(back_populates="resets")
