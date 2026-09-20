"""Webhook público autenticado pela assinatura do provedor, sem JWT."""
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.core.config import Settings
from app.db.session import SessionDependency
from app.modules.auth.dependencies import CurrentUserDependency
from app.modules.payments.mercado_pago import (
    InvalidSignature,
    MercadoPagoPaymentProvider,
    ProviderInvalidPayment,
    ProviderUnavailable,
)
from app.modules.payments.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    PaymentResponse,
)
from app.modules.payments.service import (
    PaymentConflict,
    PaymentError,
    PaymentNotFound,
    PaymentService,
)

router: APIRouter = APIRouter(prefix="/payments/mercado-pago", tags=["payments"])
checkout_router: APIRouter = APIRouter(prefix="/payments", tags=["payments"])
logger: logging.Logger = logging.getLogger(__name__)


def get_provider() -> MercadoPagoPaymentProvider:
    try:
        return MercadoPagoPaymentProvider(Settings())
    except (ProviderUnavailable, ValueError):
        raise HTTPException(503, "Pagamentos indisponíveis.") from None


@checkout_router.post("/checkout", response_model=CheckoutResponse)
def checkout(data: CheckoutRequest, user: CurrentUserDependency, session: SessionDependency,
             response: Response,
             provider: Annotated[MercadoPagoPaymentProvider, Depends(get_provider)]) -> CheckoutResponse:
    settings = Settings()
    if settings.api_public_base_url is None:
        raise HTTPException(503, "URL pública da API não configurada.")
    response.headers["Cache-Control"] = "no-store"
    try:
        return PaymentService(session).create_checkout(user_id=user.id, subscription_id=data.subscription_id,
            provider=provider, notification_url=str(settings.api_public_base_url).rstrip("/")
            + "/payments/mercado-pago/webhook")
    except PaymentNotFound:
        raise HTTPException(404, "Assinatura não encontrada.") from None
    except PaymentConflict:
        raise HTTPException(409, "Checkout indisponível para esta assinatura; consulte o pagamento ou o suporte.") from None
    except (PaymentError, ProviderUnavailable, ProviderInvalidPayment):
        raise HTTPException(503, "Checkout temporariamente indisponível.") from None


@checkout_router.get("/{payment_id}", response_model=PaymentResponse)
def payment_status(payment_id: UUID, user: CurrentUserDependency,
                   session: SessionDependency, response: Response) -> PaymentResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return PaymentService(session).get_user_payment(payment_id, user.id)
    except PaymentNotFound:
        raise HTTPException(404, "Pagamento não encontrado.") from None
    except PaymentError:
        raise HTTPException(503, "Consulta de pagamento indisponível.") from None


@router.post("/webhook")
def webhook(request: Request, session: SessionDependency,
            provider: Annotated[MercadoPagoPaymentProvider, Depends(get_provider)]) -> dict[str, str]:
    # Somente data.id da URL é coberto pela assinatura. Body não decide ID/status.
    ids: list[str] = request.query_params.getlist("data.id")
    try:
        if len(ids) != 1:
            raise InvalidSignature()
        provider.validate_signature(request.headers.get("x-signature", ""),
                                    request.headers.get("x-request-id", ""), ids[0])
        confirmed = provider.fetch_payment(ids[0])
        if confirmed.payment_id is None or confirmed.external_reference is None:
            raise ProviderInvalidPayment()
        PaymentService(session).apply_provider_payment(confirmed.payment_id, provider.name, confirmed)
    except InvalidSignature:
        logger.warning("mercado_pago webhook: assinatura inválida")
        raise HTTPException(401, "Notificação não autenticada.") from None
    except (ProviderInvalidPayment, PaymentConflict, PaymentNotFound):
        logger.warning("mercado_pago webhook: confirmação incompatível")
        raise HTTPException(409, "Pagamento não conciliado.") from None
    except (ProviderUnavailable, PaymentError):
        logger.warning("mercado_pago webhook: processamento indisponível")
        raise HTTPException(503, "Pagamento temporariamente indisponível.") from None
    logger.info("mercado_pago webhook: pagamento conciliado")
    return {"status": "ok"}
