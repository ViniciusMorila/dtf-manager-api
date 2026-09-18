"""Credenciais recebidas somente nos fluxos de autenticação futuros."""

from pydantic import EmailStr, Field, SecretStr, field_validator

from app.shared.schemas import InputSchema


class LoginRequest(InputSchema):
    email: EmailStr
    password: SecretStr = Field(min_length=1, exclude=True, repr=False)

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class RefreshRequest(InputSchema):
    refresh_token: SecretStr = Field(min_length=1, exclude=True, repr=False)


class LogoutRequest(RefreshRequest):
    """Identifica a sessão a revogar sem aceitar token_hash interno."""
