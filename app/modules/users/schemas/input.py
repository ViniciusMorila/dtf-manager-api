"""Entrada validada e normalizada do cadastro, sem campos administrativos."""

from typing import Annotated

from pydantic import EmailStr, Field, SecretStr, StringConstraints, field_validator
from pydantic_core import PydanticCustomError

from app.modules.plans.models import PlanCode
from app.shared.exceptions.registration import InvalidCPF, WeakPassword
from app.shared.schemas import InputSchema
from app.shared.security.password import validate_password_strength
from app.shared.utils.cpf import normalize_cpf, validate_cpf


class UserCreate(InputSchema):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    cpf: str
    email: EmailStr
    password: SecretStr = Field(min_length=1, exclude=True, repr=False)
    plan_code: PlanCode

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: EmailStr) -> str:
        return str(value).lower()

    @field_validator("cpf")
    @classmethod
    def normalized_valid_cpf(cls, value: str) -> str:
        if not validate_cpf(value):
            raise PydanticCustomError(InvalidCPF.code, InvalidCPF.message)
        return normalize_cpf(value)

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: SecretStr) -> SecretStr:
        try:
            validate_password_strength(value.get_secret_value())
        except ValueError:
            raise PydanticCustomError(WeakPassword.code, WeakPassword.message) from None
        return value
