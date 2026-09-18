"""Testes básicos do contrato HTTP da etapa 1."""

import asyncio

from httpx import ASGITransport, AsyncClient, Response

from app.main import create_app


async def request_health(method: str) -> Response:
    """Consulta a aplicação ASGI usando httpx, sem servidor externo."""
    transport: ASGITransport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, "/health")


def test_health_returns_ok() -> None:
    """A API responde sem configuração de banco ou credenciais."""
    response: Response = asyncio.run(request_health("GET"))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


def test_health_rejects_post() -> None:
    """O endpoint de saúde permite consulta, sem operação de escrita."""
    response: Response = asyncio.run(request_health("POST"))

    assert response.status_code == 405
