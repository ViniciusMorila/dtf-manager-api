"""Cadastro atômico de usuário, licença TESTE e, opcionalmente, assinatura pendente."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.auth.schemas.registration import RegistrationResponse
from app.modules.licenses.models import License
from app.modules.licenses.service import (
    create_trial,
    to_public,
    translate_integrity_error,
    utc_now,
)
from app.modules.plans.models import Plan
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.subscriptions.schemas.output import SubscriptionRead
from app.modules.users.models import User, UserRole
from app.modules.users.schemas.input import UserCreate
from app.modules.users.schemas.output import UserRead
from app.shared.exceptions.license import LicenseError
from app.shared.exceptions.registration import (
    CPFAlreadyExists,
    EmailAlreadyExists,
    InvalidPlan,
    RegistrationError,
    RegistrationUnavailable,
)
from app.shared.security.password import hash_password


def register_user(data: UserCreate, session: Session) -> RegistrationResponse:
    """Um único commit; qualquer falha (inclusive a trava de teste) reverte também o usuário."""
    try:
        with session.begin():
            if session.scalar(select(User.id).where(func.lower(User.email) == str(data.email))) is not None:
                raise EmailAlreadyExists()
            if data.cpf is not None and session.scalar(select(User.id).where(User.cpf == data.cpf)) is not None:
                raise CPFAlreadyExists()
            plan: Plan | None = None
            if data.plan_code is not None:
                plan = session.scalar(
                    select(Plan).where(Plan.code == data.plan_code).with_for_update(read=True),
                )
                if plan is None or not plan.is_active:
                    raise InvalidPlan()

            user: User = User(
                name=data.name, cpf=data.cpf, email=str(data.email),
                password_hash=hash_password(data.password.get_secret_value()), is_active=True,
                role=UserRole.USER,
            )
            session.add(user)
            session.flush()
            now: datetime = utc_now()
            license: License = create_trial(session, user_id=user.id, phone=data.phone,
                                            machine_id=data.machine_id, now=now)
            subscription: Subscription | None = None
            if plan is not None:
                subscription = Subscription(
                    user_id=user.id, plan_id=plan.id, status=SubscriptionStatus.PENDING,
                    starts_at=None, expires_at=None, activated_at=None, cancelled_at=None,
                )
                session.add(subscription)
                session.flush()
            result: RegistrationResponse = RegistrationResponse(
                user=UserRead.model_validate(user),
                subscription=SubscriptionRead.model_validate(subscription) if subscription else None,
                licenca=to_public(license, now),
            )
        return result
    except IntegrityError as exc:
        # Prechecks ajudam o usuário; constraints resolvem corridas concorrentes.
        license_error: LicenseError | None = translate_integrity_error(exc)
        if license_error is not None:
            raise license_error from None
        constraint: str | None = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if getattr(exc.orig, "sqlstate", None) == "23505":
            if constraint == "uq_users_email":
                raise EmailAlreadyExists() from None
            if constraint == "uq_users_cpf":
                raise CPFAlreadyExists() from None
        raise RegistrationError() from None
    except OperationalError:
        raise RegistrationUnavailable() from None
    except SQLAlchemyError:
        raise RegistrationError() from None
