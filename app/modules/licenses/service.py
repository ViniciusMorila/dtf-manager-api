"""Decisão de acesso, criação de teste e ativação de chave, sempre com locks de linha.

Toda decisão usa o banco e o relógio do servidor (UTC); nada vindo do aplicativo
além do ID da máquina e da chave é considerado.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.licenses.models import (
    ACTIVE_MACHINE_INDEX,
    TRIAL_MACHINE_CONSTRAINT,
    TRIAL_PHONE_CONSTRAINT,
    ActivationKey,
    ActivationKeyStatus,
    License,
    LicensePlanCode,
    LicenseState,
    TrialUsage,
)
from app.modules.licenses.rules import (
    days_remaining,
    expire_if_due,
    plan_end,
    renewed_expiration,
)
from app.modules.licenses.schemas.contract import (
    AtivarChaveResponse,
    LicencaPublica,
    LicencaStatusResponse,
)
from app.shared.exceptions.license import (
    InvalidActivationKey,
    LicenseBlocked,
    LicenseError,
    LicenseExpired,
    LicenseNotFound,
    MachineIdInvalid,
    MachineInUse,
    MachineMismatch,
    TrialAlreadyUsed,
)
from app.shared.security.activation_key import (
    hash_activation_key,
    normalize_activation_key,
)

_TRIAL_MACHINE_LOCK: int = 41_001
_TRIAL_PHONE_LOCK: int = 41_002


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_public(license: License, now: datetime) -> LicencaPublica:
    return LicencaPublica(
        plano=license.plan, status=license.status, data_ativacao=license.activated_at,
        data_expiracao=license.expires_at, dias_restantes=days_remaining(license, now),
        vitalicia=license.plan == LicensePlanCode.VITALICIO,
    )


def license_payload(public: LicencaPublica | None, now: datetime) -> dict[str, Any]:
    return {"licenca": public.model_dump(mode="json") if public else None, "servidor_agora": now.isoformat()}


def translate_integrity_error(exc: IntegrityError) -> LicenseError | None:
    """Converte corridas resolvidas pelas constraints únicas em códigos de licença."""
    constraint: str | None = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if getattr(exc.orig, "sqlstate", None) != "23505":
        return None
    if constraint in (TRIAL_MACHINE_CONSTRAINT, TRIAL_PHONE_CONSTRAINT):
        return TrialAlreadyUsed()
    if constraint == ACTIVE_MACHINE_INDEX:
        return MachineInUse()
    return None


def lock_license(session: Session, user_id: UUID) -> License | None:
    return session.scalar(
        select(License).where(License.user_id == user_id)
        .with_for_update().execution_options(populate_existing=True),
    )


def bind_machine(session: Session, license: License, machine_id: str | None) -> LicenseError | None:
    """Vincula na primeira validação (ou após reset); depois exige a mesma máquina."""
    if machine_id is None:
        return MachineIdInvalid()
    if license.machine_id is None:
        in_use: UUID | None = session.scalar(select(License.id).where(
            License.machine_id == machine_id, License.status == LicenseState.ATIVA, License.id != license.id,
        ))
        if in_use is not None:
            return MachineInUse()
        license.machine_id = machine_id
        return None
    if license.machine_id != machine_id:
        return MachineMismatch()
    return None


@dataclass(frozen=True)
class AccessDecision:
    public: LicencaPublica | None
    error: LicenseError | None


def check_access(session: Session, *, user_id: UUID, is_admin: bool, machine_id: str | None,
                 now: datetime) -> AccessDecision:
    """Executa dentro da transação do chamador; a expiração é gravada mesmo quando recusa.

    O chamador deve concluir o commit antes de lançar decision.error.
    Administradores nunca são recusados nem presos a máquina.
    """
    license: License | None = lock_license(session, user_id)
    if license is not None:
        expire_if_due(license, now)
    if is_admin:
        session.flush()
        return AccessDecision(to_public(license, now) if license else None, None)
    if license is None:
        return AccessDecision(None, LicenseNotFound())
    error: LicenseError | None = None
    if license.status == LicenseState.BLOQUEADA:
        error = LicenseBlocked()
    elif license.status == LicenseState.EXPIRADA:
        error = LicenseExpired()
    else:
        error = bind_machine(session, license, machine_id)
    session.flush()
    public: LicencaPublica = to_public(license, now)
    if error is not None:
        error.extra = license_payload(public, now)
    return AccessDecision(public, error)


def license_status(session: Session, *, user_id: UUID, is_admin: bool, machine_id: str | None) -> LicencaStatusResponse:
    now: datetime = utc_now()
    try:
        with session.begin():
            decision: AccessDecision = check_access(session, user_id=user_id, is_admin=is_admin,
                                                    machine_id=machine_id, now=now)
    except IntegrityError as exc:
        raise (translate_integrity_error(exc) or LicenseError()) from None
    except SQLAlchemyError:
        raise LicenseError() from None
    if decision.error is not None:
        raise decision.error
    return LicencaStatusResponse(licenca=decision.public, servidor_agora=now)


def create_trial(session: Session, *, user_id: UUID, phone: str, machine_id: str, now: datetime) -> License:
    """Cria o teste de 7 dias se nem a máquina nem o telefone já usaram teste.

    Locks consultivos por máquina e por telefone serializam cadastros simultâneos;
    as constraints únicas de trial_usages garantem a regra mesmo sem eles.
    """
    session.execute(text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:value))"),
                    {"namespace": _TRIAL_MACHINE_LOCK, "value": machine_id})
    session.execute(text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:value))"),
                    {"namespace": _TRIAL_PHONE_LOCK, "value": phone})
    used: UUID | None = session.scalar(select(TrialUsage.id).where(
        (TrialUsage.machine_id == machine_id) | (TrialUsage.phone == phone)).limit(1))
    if used is not None:
        raise TrialAlreadyUsed()
    in_use: UUID | None = session.scalar(select(License.id).where(
        License.machine_id == machine_id, License.status == LicenseState.ATIVA).limit(1))
    if in_use is not None:
        raise MachineInUse()
    session.add(TrialUsage(machine_id=machine_id, phone=phone, user_id=user_id))
    license: License = License(
        user_id=user_id, plan=LicensePlanCode.TESTE, status=LicenseState.ATIVA, activated_at=now,
        expires_at=plan_end(LicensePlanCode.TESTE, now), machine_id=machine_id, phone=phone,
        reset_count=0, last_reset_at=None,
    )
    session.add(license)
    session.flush()
    return license


def redeem_key(session: Session, *, user_id: UUID, is_admin: bool, raw_key: str,
               machine_id: str | None) -> AtivarChaveResponse:
    """Marca a chave como usada e aplica o plano conforme a regra de renovação."""
    key: str | None = normalize_activation_key(raw_key)
    if key is None:
        raise InvalidActivationKey()
    now: datetime = utc_now()
    try:
        with session.begin():
            # Ordem fixa de locks (licença, depois chave) evita deadlock entre ativações.
            license: License | None = lock_license(session, user_id)
            activation: ActivationKey | None = session.scalar(
                select(ActivationKey).where(ActivationKey.key_hash == hash_activation_key(key))
                .with_for_update().execution_options(populate_existing=True),
            )
            if activation is None or activation.status != ActivationKeyStatus.DISPONIVEL:
                raise InvalidActivationKey()
            if license is None:
                if not is_admin:
                    if machine_id is None:
                        raise MachineIdInvalid()
                    if session.scalar(select(License.id).where(
                            License.machine_id == machine_id, License.status == LicenseState.ATIVA)) is not None:
                        raise MachineInUse()
                license = License(
                    user_id=user_id, plan=activation.plan, status=LicenseState.ATIVA, activated_at=now,
                    expires_at=plan_end(activation.plan, now), machine_id=None if is_admin else machine_id,
                    phone=None, reset_count=0, last_reset_at=None,
                )
                session.add(license)
                session.flush()
            else:
                expire_if_due(license, now)
                if license.status == LicenseState.BLOQUEADA:
                    raise LicenseBlocked()
                if not is_admin:
                    machine_error: LicenseError | None = bind_machine(session, license, machine_id)
                    if machine_error is not None:
                        raise machine_error
                license.expires_at = renewed_expiration(license, activation.plan, now)
                license.plan = activation.plan
                license.status = LicenseState.ATIVA
                license.activated_at = now
            activation.status = ActivationKeyStatus.USADA
            activation.used_at = now
            activation.license_id = license.id
            session.flush()
            result: AtivarChaveResponse = AtivarChaveResponse(
                licenca=to_public(license, now), servidor_agora=now, plano_ativado=activation.plan,
            )
        return result
    except IntegrityError as exc:
        raise (translate_integrity_error(exc) or LicenseError()) from None
    except SQLAlchemyError:
        raise LicenseError() from None
