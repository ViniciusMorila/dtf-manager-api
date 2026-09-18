"""Fonte central de autorização de licença, baseada no banco e na hora da API."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.base import load_models
from app.modules.plans.models import Plan, PlanCode
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.subscriptions.schemas.license import LicensePlan, LicenseStatus
from app.modules.subscriptions.schemas.output import SubscriptionRead
from app.modules.users.models import User
from app.shared.utils.calendar import add_calendar_months


class LicenseStatusUnavailable(Exception):
    """Falhas de persistência nunca podem ser interpretadas como autorização."""


class SubscriptionActivationError(Exception):
    """Assinatura ausente, estado inválido ou falha na ativação interna."""


class SubscriptionService:
    def __init__(self, session: Session) -> None:
        load_models()
        self._session: Session = session

    def activate_subscription(self, subscription_id: UUID) -> SubscriptionRead:
        """Ativação inicial interna; o chamador deve ter confirmado o pagamento."""
        try:
            transaction = self._session.begin_nested() if self._session.in_transaction() else self._session.begin()
            with transaction:
                row = self._session.execute(
                    select(Subscription, Plan).join(Plan, Subscription.plan_id == Plan.id)
                    .where(Subscription.id == subscription_id)
                    .with_for_update(of=Subscription).execution_options(populate_existing=True),
                ).one_or_none()
                if row is None:
                    raise SubscriptionActivationError("Assinatura ou plano não encontrado.")
                subscription, plan = row
                # Repetições nunca renovam vigência nem desfazem cancelamento/suspensão.
                if subscription.activated_at is None and subscription.status != SubscriptionStatus.ACTIVE:
                    if subscription.status != SubscriptionStatus.PENDING or subscription.cancelled_at is not None:
                        raise SubscriptionActivationError("Assinatura não permite ativação inicial.")
                    months_by_code: dict[PlanCode, int] = {
                        PlanCode.MONTHLY: 1, PlanCode.SEMIANNUAL: 6, PlanCode.ANNUAL: 12,
                    }
                    now: datetime = datetime.now(UTC)
                    if plan.code == PlanCode.LIFETIME:
                        expires_at: datetime | None = None
                    elif plan.code in months_by_code:
                        expires_at = add_calendar_months(now, months_by_code[plan.code])
                    else:
                        raise SubscriptionActivationError("Plano não permite ativação.")
                    subscription.starts_at = now
                    subscription.expires_at = expires_at
                    subscription.activated_at = now
                    subscription.status = SubscriptionStatus.ACTIVE
                    self._session.flush()
                result: SubscriptionRead = SubscriptionRead.model_validate(subscription)
            return result
        except SQLAlchemyError:
            raise SubscriptionActivationError("Não foi possível ativar a assinatura.") from None

    def get_license_status(self, user_id: UUID) -> LicenseStatus:
        """Consulta sem cache e confirma expirações antes de retornar a decisão."""
        try:
            with self._session.begin():
                user: User | None = self._session.scalar(
                    select(User).where(User.id == user_id).with_for_update(read=True)
                    .execution_options(populate_existing=True),
                )
                if user is None:
                    return LicenseStatus(active=False, status=None, plan=None,
                                         starts_at=None, expires_at=None, is_lifetime=False)
                rows = self._session.execute(
                    select(Subscription, Plan).join(Plan, Subscription.plan_id == Plan.id)
                    .where(Subscription.user_id == user_id)
                    .order_by(Subscription.created_at.desc(), Subscription.id.desc())
                    .with_for_update(of=Subscription).execution_options(populate_existing=True),
                ).all()
                now: datetime = datetime.now(UTC)
                decisions: list[LicenseStatus] = []
                for subscription, plan in rows:
                    lifetime: bool = plan.code == PlanCode.LIFETIME and plan.is_lifetime
                    active: bool = False
                    if subscription.status == SubscriptionStatus.ACTIVE:
                        if not lifetime and subscription.expires_at is not None and subscription.expires_at <= now:
                            subscription.status = SubscriptionStatus.EXPIRED
                        elif lifetime:
                            active = subscription.expires_at is None
                        else:
                            active = (
                                subscription.starts_at is not None and subscription.starts_at <= now
                                and subscription.expires_at is not None and subscription.expires_at > now
                            )
                    decisions.append(LicenseStatus(
                        active=active and user.is_active, status=subscription.status,
                        plan=LicensePlan(code=plan.code, name=plan.name),
                        starts_at=subscription.starts_at, expires_at=subscription.expires_at,
                        is_lifetime=lifetime,
                    ))
                self._session.flush()
                # Uma nova escolha PENDING não invalida uma assinatura paga vigente.
                result: LicenseStatus = next((item for item in decisions if item.active),
                    decisions[0] if decisions else LicenseStatus(active=False, status=None, plan=None,
                                                                 starts_at=None, expires_at=None, is_lifetime=False))
            return result
        except SQLAlchemyError:
            raise LicenseStatusUnavailable("Não foi possível consultar a licença.") from None
