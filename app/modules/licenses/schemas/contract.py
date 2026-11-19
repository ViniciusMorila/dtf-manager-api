"""Contrato HTTP das licenças do aplicativo; campos em português, estáveis para o DTF."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)

from app.modules.licenses.models import (
    ActivationKeyStatus,
    LicensePlanCode,
    LicenseState,
)
from app.shared.schemas import InputSchema, UTCDateTime
from app.shared.utils.machine import MachineId


class LicencaPublica(BaseModel):
    """Objeto de licença devolvido ao aplicativo."""

    model_config = ConfigDict(extra="forbid")

    plano: LicensePlanCode
    status: LicenseState
    data_ativacao: UTCDateTime
    data_expiracao: UTCDateTime | None
    dias_restantes: int | None
    vitalicia: bool


class LicencaStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    licenca: LicencaPublica | None
    servidor_agora: UTCDateTime


class AtivarChaveRequest(InputSchema):
    """Bearer é o caminho normal; email e senha permitem renovar quando o login recusa por expiração."""

    chave: str = Field(min_length=1, max_length=64)
    id_maquina: MachineId | None = None
    email: EmailStr | None = None
    senha: SecretStr | None = Field(default=None, min_length=1, exclude=True, repr=False)

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: EmailStr | None) -> str | None:
        return str(value).lower() if value is not None else None


class AtivarChaveResponse(LicencaStatusResponse):
    plano_ativado: LicensePlanCode


# --- Administração -----------------------------------------------------------------

Text200 = Annotated[str, Field(min_length=1, max_length=200)]


def _strip_required(value: str) -> str:
    stripped: str = value.strip()
    if not stripped:
        raise ValueError("Campo obrigatório.")
    return stripped


class GerarChaveRequest(InputSchema):
    plano: LicensePlanCode
    nome_cliente: Annotated[Text200, AfterValidator(_strip_required)]
    contato_cliente: Annotated[Text200, AfterValidator(_strip_required)]


class ChaveAdmin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    chave_mascarada: str
    plano: LicensePlanCode
    status: ActivationKeyStatus
    nome_cliente: str
    contato_cliente: str
    licenca_id: UUID | None
    criada_em: UTCDateTime
    usada_em: UTCDateTime | None
    cancelada_em: UTCDateTime | None


class ChaveGerada(ChaveAdmin):
    """Única resposta que contém a chave completa; ela não pode ser recuperada depois."""

    chave: str


class UsuarioResumo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    nome: str
    email: str


class LicencaAdmin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    usuario: UsuarioResumo
    plano: LicensePlanCode
    status: LicenseState
    data_ativacao: UTCDateTime
    data_expiracao: UTCDateTime | None
    dias_restantes: int | None
    id_maquina: str | None
    telefone: str | None
    contador_resets: int
    data_ultimo_reset: UTCDateTime | None
    criado_em: UTCDateTime
    atualizado_em: UTCDateTime


class ListaLicencas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    itens: list[LicencaAdmin]


class ListaChaves(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    itens: list[ChaveAdmin]


class ResetMaquinaRequest(InputSchema):
    forcar: bool = False


class AlterarLicencaRequest(InputSchema):
    """Ajuste manual: plano e/ou data_expiracao. Vitalício remove a expiração."""

    plano: LicensePlanCode | None = None
    data_expiracao: datetime | None = None

    @field_validator("data_expiracao")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("data_expiracao deve informar o fuso horário.")
        return value
