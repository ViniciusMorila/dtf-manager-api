"""Credenciais recebidas nos fluxos de autenticação."""

from pydantic import AliasChoices, EmailStr, Field, SecretStr, field_validator

from app.shared.schemas import InputSchema
from app.shared.utils.machine import MachineId


class LoginRequest(InputSchema):
    email: EmailStr
    password: SecretStr = Field(min_length=1, exclude=True, repr=False,
                                validation_alias=AliasChoices("password", "senha"))
    # Obrigatório para contas comuns; administradores não ficam presos a máquina.
    id_maquina: MachineId | None = None

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class RefreshRequest(InputSchema):
    refresh_token: SecretStr = Field(min_length=1, exclude=True, repr=False)


class LogoutRequest(RefreshRequest):
    """Identifica a sessão a revogar sem aceitar token_hash interno."""
