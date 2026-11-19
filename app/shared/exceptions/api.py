"""Erros públicos com código estável, mensagem em português e dados opcionais."""

from typing import Any, ClassVar


class ApiError(Exception):
    code: ClassVar[str] = "ERRO_INTERNO"
    message: ClassVar[str] = "Não foi possível concluir a operação."
    status_code: ClassVar[int] = 500

    def __init__(self, *, extra: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(self.message)
        # Campos adicionais do corpo (ex.: licenca), já serializáveis em JSON.
        self.extra: dict[str, Any] = extra or {}
        self.headers: dict[str, str] = headers or {}


class TooManyRequests(ApiError):
    code = "MUITAS_TENTATIVAS"
    message = "Muitas tentativas. Aguarde um minuto e tente novamente."
    status_code = 429


class AccessDenied(ApiError):
    code = "ACESSO_NEGADO"
    message = "Acesso restrito a administradores."
    status_code = 403
