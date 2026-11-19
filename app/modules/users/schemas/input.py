"""Entrada validada e normalizada do cadastro, sem campos administrativos."""

from typing import Annotated

from pydantic import (
    AliasChoices,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.modules.plans.models import PlanCode
from app.shared.exceptions.license import InvalidPhone, MachineIdInvalid
from app.shared.exceptions.registration import InvalidCPF, WeakPassword
from app.shared.schemas import InputSchema
from app.shared.security.password import validate_password_strength
from app.shared.utils.cpf import normalize_cpf, validate_cpf
from app.shared.utils.machine import normalize_machine_id
from app.shared.utils.phone import normalize_phone


class UserCreate(InputSchema):
    """Cadastro do aplicativo: cria a conta e a licença vinculada à máquina e ao telefone.

    Sem `chave_ativacao` a licença é o TESTE de 7 dias; com ela, o plano da chave.

    `cpf` e `plan_code` continuam aceitos (opcionais) para o fluxo de pagamento existente.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, populate_by_name=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] = Field(
        validation_alias=AliasChoices("name", "nome"))
    email: EmailStr
    password: SecretStr = Field(min_length=1, exclude=True, repr=False,
                                validation_alias=AliasChoices("password", "senha"))
    phone: str = Field(validation_alias=AliasChoices("telefone", "phone"))
    machine_id: str = Field(validation_alias=AliasChoices("id_maquina", "machine_id"))
    cpf: str | None = None
    plan_code: PlanCode | None = None
    # Com chave: a licença do plano da chave substitui o teste de 7 dias. Validada no cadastro.
    activation_key: str | None = Field(
        default=None, max_length=64, exclude=True, repr=False,
        validation_alias=AliasChoices("chave_ativacao", "activation_key"))

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: EmailStr) -> str:
        return str(value).lower()

    @field_validator("cpf")
    @classmethod
    def normalized_valid_cpf(cls, value: str | None) -> str | None:
        if value is None:
            return None
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

    @field_validator("phone")
    @classmethod
    def normalized_phone(cls, value: str) -> str:
        try:
            return normalize_phone(value)
        except ValueError:
            raise PydanticCustomError(InvalidPhone.code, InvalidPhone.message) from None

    @field_validator("machine_id")
    @classmethod
    def normalized_machine(cls, value: str) -> str:
        try:
            return normalize_machine_id(value)
        except ValueError:
            raise PydanticCustomError(MachineIdInvalid.code, MachineIdInvalid.message) from None
