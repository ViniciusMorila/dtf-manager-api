"""Cadastro atômico de usuário e assinatura pendente."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.auth.schemas.registration import RegistrationResponse
from app.modules.plans.models import Plan
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.subscriptions.schemas.output import SubscriptionRead
from app.modules.users.models import User
from app.modules.users.schemas.input import UserCreate
from app.modules.users.schemas.output import UserRead
from app.shared.exceptions.registration import (
    CPFAlreadyExists,
    EmailAlreadyExists,
    InvalidPlan,
    RegistrationError,
    RegistrationUnavailable,
)
from app.shared.security.password import hash_password


def register_user(data: UserCreate, session: Session) -> RegistrationResponse:
    """Um único commit; qualquer falha na assinatura reverte também o usuário."""
    try:
        with session.begin():
            if session.scalar(select(User.id).where(func.lower(User.email) == str(data.email))) is not None:
                raise EmailAlreadyExists()
            if session.scalar(select(User.id).where(User.cpf == data.cpf)) is not None:
                raise CPFAlreadyExists()
            plan: Plan | None = session.scalar(
                select(Plan).where(Plan.code == data.plan_code).with_for_update(read=True),
            )
            if plan is None or not plan.is_active:
                raise InvalidPlan()

            user: User = User(
                name=data.name, cpf=data.cpf, email=str(data.email),
                password_hash=hash_password(data.password.get_secret_value()), is_active=True,
            )
            session.add(user)
            session.flush()
            subscription: Subscription = Subscription(
                user_id=user.id, plan_id=plan.id, status=SubscriptionStatus.PENDING,
                starts_at=None, expires_at=None, activated_at=None, cancelled_at=None,
            )
            session.add(subscription)
            session.flush()
            result: RegistrationResponse = RegistrationResponse(
                user=UserRead.model_validate(user),
                subscription=SubscriptionRead.model_validate(subscription),
            )
        return result
    except IntegrityError as exc:
        # Prechecks ajudam o usuário; constraints resolvem corridas concorrentes.
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
