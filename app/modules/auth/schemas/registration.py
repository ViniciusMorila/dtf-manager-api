"""Resposta pública do cadastro, sem tokens ou credenciais."""

from pydantic import BaseModel, ConfigDict

from app.modules.licenses.schemas.contract import LicencaPublica
from app.modules.subscriptions.schemas.output import SubscriptionRead
from app.modules.users.schemas.output import UserRead


class RegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserRead
    # Presente somente quando plan_code foi informado (fluxo de pagamento).
    subscription: SubscriptionRead | None
    licenca: LicencaPublica


class ErrorDetail(BaseModel):
    code: str
    message: str


class RegistrationErrorResponse(BaseModel):
    error: ErrorDetail
