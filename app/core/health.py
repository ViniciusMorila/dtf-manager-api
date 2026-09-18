"""Verificação de disponibilidade do processo da API."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router: APIRouter = APIRouter()


class HealthResponse(BaseModel):
    """Contrato da resposta de saúde."""

    status: Literal["ok"] = "ok"


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Informa que a API está respondendo, sem consultar serviços externos."""
    return HealthResponse()
