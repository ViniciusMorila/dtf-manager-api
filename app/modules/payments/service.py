"""Persistência idempotente e aprovação atômica, sem chamadas externas."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.base import load_models
from app.modules.payments.contracts import (
    CheckoutCreate,
    PaymentCreate,
    PaymentRecord,
    PaymentUpdate,
    ProviderPayment,
)
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.provider import PaymentProvider
from app.modules.payments.schemas import CheckoutResponse, PaymentResponse
from app.modules.plans.models import Plan
from app.modules.subscriptions.models import Subscription
from app.modules.subscriptions.service import (
    SubscriptionActivationError,
    SubscriptionService,
)
from app.modules.users.models import User


class PaymentError(Exception):
    """Falha interna com mensagem que não expõe dados do provedor ou SQL."""


class PaymentConflict(PaymentError):
    """Identificador reutilizado com dados incompatíveis ou transição inválida."""


class PaymentNotFound(PaymentError):
    """Confirmação sem intenção local de pagamento correspondente."""


class PaymentService:
    def __init__(self, session: Session) -> None:
        load_models()
        self._session: Session = session

    @staticmethod
    def _public_payment(payment: Payment) -> PaymentResponse:
        return PaymentResponse(payment_id=payment.id, status=payment.status,
                               amount=payment.amount, currency=payment.currency)

    def get_user_payment(self, payment_id: UUID, user_id: UUID) -> PaymentResponse:
        try:
            with self._session.begin():
                payment = self._session.scalar(select(Payment).where(
                    Payment.id == payment_id, Payment.user_id == user_id))
                if payment is None:
                    raise PaymentNotFound("Pagamento não encontrado.")
                return self._public_payment(payment)
        except SQLAlchemyError:
            raise PaymentError("Consulta de pagamento indisponível.") from None

    def create_checkout(self, *, user_id: UUID, subscription_id: UUID,
                        provider: PaymentProvider, notification_url: str) -> CheckoutResponse:
        """Uma preferência por assinatura. Resultado de rede incerto nunca causa novo POST.

        O marcador creating é confirmado ANTES da rede. Concorrentes retornam conflito;
        sucesso persiste a URL. Falha/crash exige conciliação operacional, sem recriação.
        """
        payment_id = uuid5(NAMESPACE_URL, f"dtf-manager:{provider.name}:{subscription_id}")
        try:
            with self._session.begin():
                subscription = self._session.scalar(select(Subscription).where(
                    Subscription.id == subscription_id, Subscription.user_id == user_id).with_for_update())
                if subscription is None:
                    raise PaymentNotFound("Assinatura não encontrada.")
                user = self._session.get(User, user_id)
                plan = self._session.scalar(select(Plan).where(Plan.id == subscription.plan_id)
                                            .with_for_update(read=True))
                if (user is None or not user.is_active or plan is None or not plan.is_active
                        or plan.price is None or plan.price <= 0 or subscription.status.value != "PENDING"
                        or subscription.activated_at is not None or subscription.cancelled_at is not None):
                    raise PaymentConflict("Assinatura não permite checkout.")
                # Inclui pagamentos legados, evitando abrir checkout ao lado de outro fluxo.
                existing = self._session.scalars(select(Payment).where(
                    Payment.subscription_id == subscription_id).with_for_update()).all()
                if existing:
                    if len(existing) != 1:
                        raise PaymentConflict("Assinatura já possui pagamentos.")
                    payment = existing[0]
                    metadata = payment.payment_metadata or {}
                    if (payment.id != payment_id or payment.user_id != user_id or payment.provider != provider.name
                            or payment.status != PaymentStatus.PENDING or payment.amount != plan.price
                            or payment.currency != "BRL" or metadata.get("checkout_state") != "ready"):
                        raise PaymentConflict("Checkout existente ou criação com resultado incerto.")
                    expiration = metadata.get("checkout_expires_at")
                    url = metadata.get("init_point")
                    if (not isinstance(expiration, str) or not isinstance(url, str)
                            or datetime.fromisoformat(expiration) <= datetime.now(UTC)):
                        raise PaymentConflict("Checkout expirado; requer conciliação antes de nova cobrança.")
                    return CheckoutResponse(**self._public_payment(payment).model_dump(), init_point=url)
                expiration_at = datetime.now(UTC) + timedelta(hours=24)
                description = f"DTF Manager - {plan.name} ({plan.code.value})"
                payment = Payment(id=payment_id, user_id=user_id, subscription_id=subscription_id,
                    provider=provider.name, amount=plan.price, currency="BRL", status=PaymentStatus.PENDING,
                    payment_metadata={"checkout_state": "creating", "description": description,
                                      "checkout_expires_at": expiration_at.isoformat(),
                                      "notification_url": notification_url})
                self._session.add(payment)
                self._session.flush()
                request = CheckoutCreate(user_id=user_id, subscription_id=subscription_id, payment_id=payment_id,
                    provider=provider.name, amount=plan.price, currency="BRL", description=description,
                    expires_at=expiration_at, notification_url=notification_url)
            preference = provider.create_checkout(request)
            with self._session.begin():
                payment = self._session.scalar(select(Payment).where(Payment.id == payment_id)
                    .with_for_update().execution_options(populate_existing=True))
                if payment is None:
                    raise PaymentNotFound("Pagamento não encontrado.")
                payment.payment_metadata = {**(payment.payment_metadata or {}), "checkout_state": "ready",
                    "preference_id": preference.preference_id, "init_point": preference.init_point}
                self._session.flush()
                return CheckoutResponse(**self._public_payment(payment).model_dump(), init_point=preference.init_point)
        except SQLAlchemyError:
            raise PaymentError("Checkout temporariamente indisponível.") from None

    def create_provider_charge(self, *, user_id: UUID, subscription_id: UUID,
                               expected_amount: Decimal, description: str,
                               provider: PaymentProvider) -> PaymentRecord:
        """Cobrança inicial interna; identidade estável por assinatura/provedor.

        Recarrega usuário, assinatura e plano. Persiste intenção antes da rede.
        Retry reutiliza corpo persistido, inclusive depois de timeout/reinício.
        """
        if not isinstance(expected_amount, Decimal) or not expected_amount.is_finite():
            raise PaymentConflict("Valor esperado deve ser Decimal finito.")
        payment_id: UUID = uuid5(NAMESPACE_URL, f"dtf-manager:{provider.name}:{subscription_id}")
        try:
            with self._session.begin():
                subscription: Subscription | None = self._session.scalar(select(Subscription)
                    .where(Subscription.id == subscription_id).with_for_update())
                if subscription is None or subscription.user_id != user_id:
                    raise PaymentConflict("Assinatura incompatível.")
                user: User | None = self._session.get(User, user_id)
                plan: Plan | None = self._session.scalar(select(Plan).where(Plan.id == subscription.plan_id)
                    .with_for_update(read=True))
                if (user is None or not user.is_active or plan is None or not plan.is_active
                        or plan.price is None or plan.price <= 0 or plan.price != expected_amount):
                    raise PaymentConflict("Conta, plano ou preço incompatível.")
                payment: Payment | None = self._session.get(Payment, payment_id)
                if payment is not None and (payment.payment_metadata or {}).get("checkout_state"):
                    raise PaymentConflict("Assinatura já possui Checkout Pro.")
                if payment is None:
                    if subscription.status.value != "PENDING":
                        raise PaymentConflict("Assinatura não aguarda pagamento.")
                    data: PaymentCreate = PaymentCreate(user_id=user.id, subscription_id=subscription.id,
                        payment_id=payment_id, provider=provider.name, amount=plan.price, currency="BRL",
                        description=description, payer_email=user.email)
                    payment = Payment(id=payment_id, user_id=user.id, subscription_id=subscription.id,
                        provider=provider.name, amount=data.amount, currency="BRL", status=PaymentStatus.PENDING,
                        payment_metadata={"description": data.description, "payer_email": data.payer_email})
                    self._session.add(payment)
                    self._session.flush()
                if (payment.user_id != user.id or payment.subscription_id != subscription.id
                        or payment.provider != provider.name or payment.amount != plan.price or payment.currency != "BRL"):
                    raise PaymentConflict("Cobrança existente incompatível.")
                metadata = payment.payment_metadata or {}
                data = PaymentCreate(user_id=payment.user_id, subscription_id=payment.subscription_id,
                    payment_id=payment.id, provider=payment.provider, amount=payment.amount, currency=payment.currency,
                    description=metadata.get("description"), payer_email=metadata.get("payer_email"))
                external_id: str | None = payment.provider_payment_id
        except SQLAlchemyError:
            raise PaymentError("Não foi possível preparar a cobrança.") from None
        confirmation: ProviderPayment = (provider.fetch_payment(external_id) if external_id else
            provider.create_payment(data, idempotency_key=str(payment_id)))
        return self.apply_provider_payment(payment_id, provider.name, confirmation)

    def create_payment(self, data: PaymentCreate) -> PaymentRecord:
        """Cria PENDING; repetições usam ID externo ou UUID local fornecido."""
        payment_id: UUID = data.payment_id or uuid4()
        try:
            with self._session.begin():
                subscription: Subscription | None = self._session.scalar(
                    select(Subscription).where(Subscription.id == data.subscription_id)
                    .execution_options(populate_existing=True),
                )
                if subscription is None or subscription.user_id != data.user_id:
                    raise PaymentConflict("Assinatura incompatível com o usuário.")
                inserted = self._session.execute(
                    insert(Payment.__table__).values(id=payment_id, user_id=data.user_id,
                        subscription_id=data.subscription_id, provider=data.provider,
                        provider_payment_id=data.provider_payment_id, amount=data.amount,
                        currency=data.currency, status=PaymentStatus.PENDING)
                    .on_conflict_do_nothing().returning(Payment.id),
                ).scalar_one_or_none()
                record: Payment | None = None
                if inserted is not None:
                    record = self._session.scalar(select(Payment).where(Payment.id == inserted))
                elif data.provider_payment_id is not None:
                    record = self._session.scalar(select(Payment).where(
                        Payment.provider == data.provider, Payment.provider_payment_id == data.provider_payment_id))
                if record is None:
                    record = self._session.scalar(select(Payment).where(Payment.id == payment_id))
                if record is None or (
                    record.user_id != data.user_id or record.subscription_id != data.subscription_id
                    or record.provider != data.provider or record.provider_payment_id != data.provider_payment_id
                    or record.amount != data.amount or record.currency != data.currency
                ):
                    raise PaymentConflict("Identificador de pagamento já utilizado com outros dados.")
                result: PaymentRecord = PaymentRecord.model_validate(record)
            return result
        except SQLAlchemyError:
            raise PaymentError("Não foi possível criar o pagamento.") from None

    def update_payment(self, payment_id: UUID, data: PaymentUpdate) -> PaymentRecord:
        """Aplica atualização confiável; APPROVED sempre passa pela ativação."""
        return self._update(payment_id, data)

    def process_approval(self, payment_id: UUID) -> PaymentRecord:
        """Somente chamadores internos com confirmação definitiva do pagamento."""
        return self._update(payment_id, PaymentUpdate(status=PaymentStatus.APPROVED))

    def apply_provider_payment(self, payment_id: UUID, provider: str, data: ProviderPayment) -> PaymentRecord:
        """Confere provedor/valor/moeda antes de aplicar um resultado confiável."""
        return self._update(payment_id, PaymentUpdate(status=data.status,
            provider_payment_id=data.provider_payment_id), provider=provider, confirmation=data)

    def _update(self, payment_id: UUID, data: PaymentUpdate, *, provider: str | None = None,
                confirmation: ProviderPayment | None = None) -> PaymentRecord:
        try:
            with self._session.begin():
                payment: Payment | None = self._session.scalar(select(Payment).where(Payment.id == payment_id)
                    .with_for_update().execution_options(populate_existing=True))
                if payment is None:
                    raise PaymentNotFound("Pagamento não encontrado.")
                if confirmation is not None and (payment.provider != provider or payment.amount != confirmation.amount
                                                  or payment.currency != confirmation.currency):
                    raise PaymentConflict("Confirmação incompatível com o pagamento.")
                if confirmation is not None and (confirmation.external_reference is not None
                                                  or confirmation.payment_id is not None):
                    subscription_check: Subscription | None = self._session.scalar(select(Subscription)
                        .where(Subscription.id == payment.subscription_id).with_for_update())
                    plan_check: Plan | None = (self._session.scalar(select(Plan)
                        .where(Plan.id == subscription_check.plan_id).with_for_update(read=True))
                        if subscription_check is not None else None)
                    if (confirmation.external_reference != payment.subscription_id or confirmation.payment_id != payment.id
                            or subscription_check is None or subscription_check.user_id != payment.user_id
                            or plan_check is None or plan_check.price != payment.amount):
                        raise PaymentConflict("Referência, assinatura ou preço divergente.")
                if data.provider_payment_id is not None:
                    if payment.provider_payment_id not in (None, data.provider_payment_id):
                        raise PaymentConflict("O identificador externo não pode ser substituído.")
                    payment.provider_payment_id = data.provider_payment_id
                allowed: dict[PaymentStatus, set[PaymentStatus]] = {
                    PaymentStatus.PENDING: {PaymentStatus.APPROVED, PaymentStatus.REJECTED, PaymentStatus.CANCELLED},
                    PaymentStatus.APPROVED: {PaymentStatus.REFUNDED},
                    PaymentStatus.REJECTED: set(), PaymentStatus.CANCELLED: set(), PaymentStatus.REFUNDED: set(),
                }
                if data.status != payment.status:
                    if data.status not in allowed[payment.status]:
                        raise PaymentConflict("Transição de pagamento não permitida.")
                    subscription: Subscription | None = self._session.scalar(select(Subscription)
                        .where(Subscription.id == payment.subscription_id).with_for_update()
                        .execution_options(populate_existing=True))
                    if subscription is None or subscription.user_id != payment.user_id:
                        raise PaymentConflict("Assinatura incompatível com o pagamento.")
                    payment.status = data.status
                    self._session.flush()
                    if data.status == PaymentStatus.APPROVED:
                        SubscriptionService(self._session).activate_subscription(payment.subscription_id)
                self._session.flush()
                result: PaymentRecord = PaymentRecord.model_validate(payment)
            return result
        except IntegrityError:
            raise PaymentConflict("Identificador externo já associado a outro pagamento.") from None
        except (SQLAlchemyError, SubscriptionActivationError):
            raise PaymentError("Não foi possível atualizar o pagamento e a assinatura.") from None
