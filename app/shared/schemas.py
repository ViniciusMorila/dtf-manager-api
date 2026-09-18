"""Bases explícitas para contratos de entrada e projeções públicas de saída."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict


def as_utc(value: datetime) -> datetime:
    """Normaliza datas já validadas com timezone para UTC."""
    return value.astimezone(UTC)


UTCDateTime = Annotated[AwareDatetime, AfterValidator(as_utc)]


class InputSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class OutputSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore", hide_input_in_errors=True)
