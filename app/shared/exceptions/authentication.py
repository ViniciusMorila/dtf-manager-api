"""Erros públicos de autenticação sem identificar existência da conta."""

from typing import ClassVar


class AuthenticationError(Exception):
    code: ClassVar[str] = "INVALID_CREDENTIALS"
    message: ClassVar[str] = "Email ou senha inválidos."
    status_code: ClassVar[int] = 401

    def __init__(self) -> None:
        super().__init__(self.message)


class AuthenticationUnavailable(AuthenticationError):
    code = "AUTHENTICATION_UNAVAILABLE"
    message = "Login temporariamente indisponível."
    status_code = 503


class AuthenticationFailed(AuthenticationError):
    code = "AUTHENTICATION_FAILED"
    message = "Não foi possível concluir o login."
    status_code = 500


class InvalidRefreshToken(AuthenticationError):
    code = "INVALID_REFRESH_TOKEN"
    message = "Refresh token inválido ou indisponível."


class RefreshUnavailable(AuthenticationUnavailable):
    message = "Renovação de sessão temporariamente indisponível."


class RefreshFailed(AuthenticationFailed):
    message = "Não foi possível renovar a sessão."


class LogoutUnavailable(AuthenticationUnavailable):
    message = "Logout temporariamente indisponível."


class LogoutFailed(AuthenticationFailed):
    message = "Não foi possível concluir o logout."


class InvalidAccessToken(AuthenticationError):
    code = "INVALID_ACCESS_TOKEN"
    message = "Autenticação inválida ou indisponível."


class AccessUnavailable(AuthenticationUnavailable):
    message = "Consulta autenticada temporariamente indisponível."
