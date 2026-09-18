"""Resposta pública do cadastro, sem tokens ou credenciais."""

from pydantic import BaseModel, ConfigDict

from app.modules.subscriptions.schemas.output import SubscriptionRead
from app.modules.users.schemas.output import UserRead


class RegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserRead
    subscription: SubscriptionRead


class ErrorDetail(BaseModel):
    code: str
    message: str


class RegistrationErrorResponse(BaseModel):
    error: ErrorDetail
