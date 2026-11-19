"""Resposta explícita da sessão; não contém secrets de assinatura JWT."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from app.modules.licenses.schemas.contract import LicencaPublica
from app.shared.schemas import UTCDateTime


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    # Tokens são entregues intencionalmente ao titular, nunca anexados à conta.
    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str = Field(min_length=1, repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_in: PositiveInt


class LogoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    status: Literal["ok"] = "ok"


class LoginResponse(TokenResponse):
    """Sessão e estado da licença no mesmo retorno, com a hora do servidor."""

    licenca: LicencaPublica | None
    servidor_agora: UTCDateTime
