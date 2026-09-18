"""A licença será consultada pela identidade autenticada, sem claims do cliente."""

from app.shared.schemas import InputSchema


class LicenseCheckRequest(InputSchema):
    """Contrato vazio: rejeita CPF, usuário, status ou vigência enviados no corpo."""
