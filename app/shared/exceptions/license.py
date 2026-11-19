"""Códigos estáveis de licença tratados individualmente pelo aplicativo."""

from app.shared.exceptions.api import ApiError


class LicenseError(ApiError):
    code = "LICENCA_INDISPONIVEL"
    message = "Validação de licença temporariamente indisponível."
    status_code = 503


class TrialAlreadyUsed(LicenseError):
    code = "TESTE_JA_UTILIZADO"
    message = "O período de teste já foi utilizado nesta máquina ou com este telefone."
    status_code = 409


class MachineMismatch(LicenseError):
    code = "MAQUINA_DIFERENTE"
    message = "Esta licença está vinculada a outra máquina. Solicite o reset ao suporte."
    status_code = 403


class MachineInUse(LicenseError):
    code = "MAQUINA_EM_USO"
    message = "Esta máquina já possui uma licença ativa de outra conta."
    status_code = 409


class MachineIdInvalid(LicenseError):
    code = "ID_MAQUINA_INVALIDO"
    message = "Identificador da máquina ausente ou inválido."
    status_code = 422


class LicenseExpired(LicenseError):
    code = "LICENCA_EXPIRADA"
    message = "Sua licença expirou. Ative uma nova chave para continuar."
    status_code = 403


class LicenseBlocked(LicenseError):
    code = "LICENCA_BLOQUEADA"
    message = "Sua licença está bloqueada. Entre em contato com o suporte."
    status_code = 403


class LicenseNotFound(LicenseError):
    code = "LICENCA_NAO_ENCONTRADA"
    message = "Nenhuma licença encontrada para esta conta. Ative uma chave para continuar."
    status_code = 404


class AdminLicenseNotFound(LicenseNotFound):
    message = "Licença não encontrada."


class LicenseAlreadyLifetime(LicenseError):
    code = "LICENCA_JA_VITALICIA"
    message = "Sua licença já é vitalícia; nenhuma chave precisa ser ativada e a chave não foi utilizada."
    status_code = 409


class InvalidActivationKey(LicenseError):
    code = "CHAVE_INVALIDA"
    message = "Chave de ativação inválida, já utilizada ou cancelada."
    status_code = 422


class ActivationKeyNotCancellable(LicenseError):
    code = "CHAVE_INVALIDA"
    message = "Somente chaves disponíveis podem ser canceladas."
    status_code = 409


class ActivationKeyNotFound(LicenseError):
    code = "CHAVE_NAO_ENCONTRADA"
    message = "Chave de ativação não encontrada."
    status_code = 404


class ResetLimitReached(LicenseError):
    code = "LIMITE_RESETS"
    message = "Limite de 3 resets de máquina em 12 meses atingido."
    status_code = 409


class InvalidPhone(LicenseError):
    code = "TELEFONE_INVALIDO"
    message = "Telefone inválido. Informe DDD e número, por exemplo (11) 91234-5678."
    status_code = 422


class InvalidLicenseChange(LicenseError):
    code = "ALTERACAO_INVALIDA"
    message = "Plano e expiração incompatíveis: o vitalício não tem data de expiração e os demais planos exigem uma."
    status_code = 422
