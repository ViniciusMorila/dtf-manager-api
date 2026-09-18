"""Erros públicos de cadastro, sem dados pessoais ou detalhes do banco."""

from typing import ClassVar


class RegistrationError(Exception):
    code: ClassVar[str] = "REGISTRATION_FAILED"
    message: ClassVar[str] = "Não foi possível concluir o cadastro."
    status_code: ClassVar[int] = 500

    def __init__(self) -> None:
        super().__init__(self.message)


class EmailAlreadyExists(RegistrationError):
    code = "EMAIL_ALREADY_EXISTS"
    message = "Email já cadastrado."
    status_code = 409


class CPFAlreadyExists(RegistrationError):
    code = "CPF_ALREADY_EXISTS"
    message = "CPF já cadastrado."
    status_code = 409


class InvalidCPF(RegistrationError):
    code = "INVALID_CPF"
    message = "CPF inválido."
    status_code = 422


class InvalidPlan(RegistrationError):
    code = "INVALID_PLAN"
    message = "Plano inválido ou indisponível."
    status_code = 422


class WeakPassword(RegistrationError):
    code = "WEAK_PASSWORD"
    message = "A senha deve ter pelo menos 8 caracteres, uma letra e um número."
    status_code = 422


class RegistrationUnavailable(RegistrationError):
    code = "REGISTRATION_UNAVAILABLE"
    message = "Cadastro temporariamente indisponível."
    status_code = 503
