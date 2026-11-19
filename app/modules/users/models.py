"""Contas de usuários; somente hashes de senha são persistidos."""

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.modules.auth.models import RefreshToken
    from app.modules.licenses.models import License
    from app.modules.payments.models import Payment
    from app.modules.subscriptions.models import Subscription


class UserRole(StrEnum):
    """Perfil da conta; ADMIN só é atribuído pelo script de seed, nunca pelo cadastro."""

    USER = "USER"
    ADMIN = "ADMIN"


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(200))
    # Opcional desde o cadastro de licenças; quando informado continua único e validado.
    cpf: Mapped[str | None] = mapped_column(String(11), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", validate_strings=True),
        default=UserRole.USER, server_default="USER",
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    payments: Mapped[list["Payment"]] = relationship(back_populates="user")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user")
    license: Mapped["License | None"] = relationship(back_populates="user", uselist=False)
