"""Regras puras de vigência; sempre recebem a hora do servidor em UTC."""

from datetime import datetime, timedelta
from math import ceil

from app.modules.licenses.models import License, LicensePlanCode, LicenseState
from app.shared.exceptions.license import LicenseAlreadyLifetime
from app.shared.utils.calendar import add_calendar_months

TRIAL_DAYS: int = 7
PLAN_MONTHS: dict[LicensePlanCode, int] = {
    LicensePlanCode.MENSAL: 1, LicensePlanCode.SEMESTRAL: 6, LicensePlanCode.ANUAL: 12,
}
RESET_LIMIT: int = 3
RESET_WINDOW_MONTHS: int = 12


def plan_end(plan: LicensePlanCode, start: datetime) -> datetime | None:
    """Fim da duração do plano contada a partir de start; vitalício não expira."""
    if plan == LicensePlanCode.VITALICIO:
        return None
    if plan == LicensePlanCode.TESTE:
        return start + timedelta(days=TRIAL_DAYS)
    return add_calendar_months(start, PLAN_MONTHS[plan])


def is_currently_valid(license: License, now: datetime) -> bool:
    if license.status != LicenseState.ATIVA:
        return False
    return license.expires_at is None or license.expires_at > now


def renewed_expiration(license: License, plan: LicensePlanCode, now: datetime) -> datetime | None:
    """Paga válida: soma à expiração atual. Teste ou vencida: conta a partir de agora.

    Os dias que sobravam do teste são descartados. Vitalício: sem expiração.
    """
    if plan == LicensePlanCode.VITALICIO:
        return None
    if license.plan == LicensePlanCode.VITALICIO and is_currently_valid(license, now):
        raise LicenseAlreadyLifetime()
    base: datetime = license.expires_at if (
        license.plan != LicensePlanCode.TESTE and is_currently_valid(license, now)
        and license.expires_at is not None) else now
    return plan_end(plan, base)


def expire_if_due(license: License, now: datetime) -> bool:
    """Persiste EXPIRADA para licença ATIVA vencida; bloqueio nunca é sobrescrito."""
    if license.status == LicenseState.ATIVA and license.expires_at is not None and license.expires_at <= now:
        license.status = LicenseState.EXPIRADA
        return True
    return False


def state_after_change(license: License, now: datetime) -> LicenseState:
    """Status coerente com a vigência depois de um ajuste, preservando o bloqueio."""
    if license.status == LicenseState.BLOQUEADA:
        return LicenseState.BLOQUEADA
    return LicenseState.ATIVA if license.expires_at is None or license.expires_at > now else LicenseState.EXPIRADA


def days_remaining(license: License, now: datetime) -> int | None:
    """Dias inteiros restantes arredondados para cima; 0 quando vencida; nulo no vitalício."""
    if license.expires_at is None:
        return None
    seconds: float = (license.expires_at - now).total_seconds()
    return max(0, ceil(seconds / 86400))
