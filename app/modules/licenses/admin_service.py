"""Operações administrativas; o chamador já confirmou o perfil ADMIN."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.auth.models import RefreshToken
from app.modules.licenses.models import (
    ActivationKey,
    ActivationKeyStatus,
    License,
    LicensePlanCode,
    LicenseReset,
    LicenseState,
)
from app.modules.licenses.rules import (
    RESET_LIMIT,
    RESET_WINDOW_MONTHS,
    days_remaining,
    expire_if_due,
    state_after_change,
)
from app.modules.licenses.schemas.contract import (
    AlterarLicencaRequest,
    ChaveAdmin,
    ChaveGerada,
    GerarChaveRequest,
    LicencaAdmin,
    ListaChaves,
    ListaLicencas,
    UsuarioResumo,
)
from app.modules.licenses.service import translate_integrity_error, utc_now
from app.modules.users.models import User
from app.shared.exceptions.license import (
    ActivationKeyNotCancellable,
    ActivationKeyNotFound,
    AdminLicenseNotFound,
    InvalidLicenseChange,
    LicenseError,
    ResetLimitReached,
)
from app.shared.security.activation_key import (
    activation_key_secret,
    generate_activation_key,
    hash_activation_key,
    mask_activation_key,
)
from app.shared.utils.calendar import add_calendar_months

MAX_PAGE: int = 200


def to_admin(license: License, user: User, now: datetime) -> LicencaAdmin:
    return LicencaAdmin(
        id=license.id, usuario=UsuarioResumo(id=user.id, nome=user.name, email=user.email),
        plano=license.plan, status=license.status, data_ativacao=license.activated_at,
        data_expiracao=license.expires_at, dias_restantes=days_remaining(license, now),
        id_maquina=license.machine_id, telefone=license.phone, contador_resets=license.reset_count,
        data_ultimo_reset=license.last_reset_at, criado_em=license.created_at, atualizado_em=license.updated_at,
    )


def key_to_admin(key: ActivationKey) -> ChaveAdmin:
    return ChaveAdmin(
        id=key.id, chave_mascarada=mask_activation_key(key.key_suffix), plano=key.plan, status=key.status,
        nome_cliente=key.customer_name, contato_cliente=key.customer_contact, licenca_id=key.license_id,
        criada_em=key.created_at, usada_em=key.used_at, cancelada_em=key.cancelled_at,
    )


def _like(term: str) -> str:
    escaped: str = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _count(session: Session, statement: Select[Any]) -> int:
    return session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0


def _run[T](session: Session, operation: Callable[[], T]) -> T:
    """Transação única; falhas de banco viram LICENCA_INDISPONIVEL sem detalhes."""
    try:
        with session.begin():
            result: T = operation()
        return result
    except IntegrityError as exc:
        raise (translate_integrity_error(exc) or LicenseError()) from None
    except SQLAlchemyError:
        raise LicenseError() from None


def generate_key(session: Session, *, admin_id: UUID, data: GerarChaveRequest) -> ChaveGerada:
    try:
        secret: str = activation_key_secret()
    except ValueError:
        raise LicenseError() from None

    def operation() -> ChaveGerada:
        key: str = generate_activation_key()
        row: ActivationKey = ActivationKey(
            key_hash=hash_activation_key(key, secret), key_suffix=key[-4:], plan=data.plano,
            status=ActivationKeyStatus.DISPONIVEL, customer_name=data.nome_cliente,
            customer_contact=data.contato_cliente, created_by=admin_id,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return ChaveGerada(chave=key, **key_to_admin(row).model_dump())

    return _run(session, operation)


def list_keys(session: Session, *, status: ActivationKeyStatus | None, plan: LicensePlanCode | None,
              search: str | None, limit: int, offset: int) -> ListaChaves:
    def operation() -> ListaChaves:
        statement = select(ActivationKey)
        if status is not None:
            statement = statement.where(ActivationKey.status == status)
        if plan is not None:
            statement = statement.where(ActivationKey.plan == plan)
        if search:
            pattern: str = _like(search.strip())
            statement = statement.where(or_(ActivationKey.customer_name.ilike(pattern, escape="\\"),
                                            ActivationKey.customer_contact.ilike(pattern, escape="\\")))
        total: int = _count(session, statement)
        rows = session.scalars(statement.order_by(ActivationKey.created_at.desc(), ActivationKey.id)
                               .limit(min(limit, MAX_PAGE)).offset(offset)).all()
        return ListaChaves(total=total, itens=[key_to_admin(row) for row in rows])

    return _run(session, operation)


def cancel_key(session: Session, key_id: UUID) -> ChaveAdmin:
    def operation() -> ChaveAdmin:
        row: ActivationKey | None = session.scalar(
            select(ActivationKey).where(ActivationKey.id == key_id)
            .with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise ActivationKeyNotFound()
        if row.status != ActivationKeyStatus.DISPONIVEL:
            raise ActivationKeyNotCancellable()
        row.status = ActivationKeyStatus.CANCELADA
        row.cancelled_at = utc_now()
        session.flush()
        return key_to_admin(row)

    return _run(session, operation)


def list_licenses(session: Session, *, status: LicenseState | None, plan: LicensePlanCode | None,
                  search: str | None, limit: int, offset: int) -> ListaLicencas:
    def operation() -> ListaLicencas:
        now: datetime = utc_now()
        # Mantém o painel coerente com o relógio do servidor antes de filtrar por status.
        session.execute(update(License).where(
            License.status == LicenseState.ATIVA, License.expires_at.is_not(None), License.expires_at <= now,
        ).values(status=LicenseState.EXPIRADA))
        statement = select(License, User).join(User, License.user_id == User.id)
        if status is not None:
            statement = statement.where(License.status == status)
        if plan is not None:
            statement = statement.where(License.plan == plan)
        if search:
            term: str = search.strip()
            conditions = [User.name.ilike(_like(term), escape="\\"), User.email.ilike(_like(term), escape="\\")]
            digits: str = "".join(character for character in term if character.isdigit())
            if digits:
                conditions.append(License.phone.like(_like(digits), escape="\\"))
            statement = statement.where(or_(*conditions))
        total: int = _count(session, statement)
        rows = session.execute(statement.order_by(License.created_at.desc(), License.id)
                               .limit(min(limit, MAX_PAGE)).offset(offset)
                               .execution_options(populate_existing=True)).all()
        return ListaLicencas(total=total, itens=[to_admin(license, user, now) for license, user in rows])

    return _run(session, operation)


def revoke_sessions(session: Session, user_id: UUID, now: datetime) -> int:
    """Bloqueio e reset encerram as sessões: o refresh deixa de funcionar na hora."""
    return session.execute(update(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None),
    ).values(revoked_at=now)).rowcount


def _locked(session: Session, license_id: UUID) -> tuple[License, User]:
    row = session.execute(
        select(License, User).join(User, License.user_id == User.id).where(License.id == license_id)
        .with_for_update(of=License).execution_options(populate_existing=True),
    ).one_or_none()
    if row is None:
        raise AdminLicenseNotFound()
    return row[0], row[1]


def block_license(session: Session, license_id: UUID) -> LicencaAdmin:
    def operation() -> LicencaAdmin:
        license, user = _locked(session, license_id)
        license.status = LicenseState.BLOQUEADA
        revoke_sessions(session, user.id, utc_now())
        session.flush()
        session.refresh(license)
        return to_admin(license, user, utc_now())

    return _run(session, operation)


def unblock_license(session: Session, license_id: UUID) -> LicencaAdmin:
    def operation() -> LicencaAdmin:
        now: datetime = utc_now()
        license, user = _locked(session, license_id)
        if license.status == LicenseState.BLOQUEADA:
            license.status = LicenseState.ATIVA
            expire_if_due(license, now)
        session.flush()
        session.refresh(license)
        return to_admin(license, user, now)

    return _run(session, operation)


def reset_machine(session: Session, *, license_id: UUID, admin_id: UUID, force: bool) -> LicencaAdmin:
    """Libera a licença para a próxima máquina; no máximo 3 resets em 12 meses sem forcar."""

    def operation() -> LicencaAdmin:
        now: datetime = utc_now()
        license, user = _locked(session, license_id)
        recent: int = session.scalar(select(func.count(LicenseReset.id)).where(
            LicenseReset.license_id == license.id,
            LicenseReset.created_at > add_calendar_months(now, -RESET_WINDOW_MONTHS),
        )) or 0
        over_limit: bool = recent >= RESET_LIMIT
        if over_limit and not force:
            raise ResetLimitReached(extra={"resets_ultimos_12_meses": recent, "limite": RESET_LIMIT})
        session.add(LicenseReset(license_id=license.id, user_email=user.email,
                                 previous_machine_id=license.machine_id,
                                 performed_by=admin_id, forced=over_limit, created_at=now))
        license.machine_id = None
        license.reset_count += 1
        license.last_reset_at = now
        revoke_sessions(session, user.id, now)
        session.flush()
        session.refresh(license)
        return to_admin(license, user, now)

    return _run(session, operation)


def update_license(session: Session, *, license_id: UUID, data: AlterarLicencaRequest) -> LicencaAdmin:
    """Ajuste/cortesia: troca plano e/ou expiração e recalcula o status (bloqueio preservado)."""

    def operation() -> LicencaAdmin:
        now: datetime = utc_now()
        license, user = _locked(session, license_id)
        plan: LicensePlanCode = data.plano or license.plan
        if plan == LicensePlanCode.VITALICIO:
            if data.data_expiracao is not None:
                raise InvalidLicenseChange()
            expires_at: datetime | None = None
        else:
            expires_at = data.data_expiracao if data.data_expiracao is not None else license.expires_at
            if expires_at is None:
                raise InvalidLicenseChange()
        license.plan = plan
        license.expires_at = expires_at
        license.status = state_after_change(license, now)
        session.flush()
        session.refresh(license)
        return to_admin(license, user, now)

    return _run(session, operation)
