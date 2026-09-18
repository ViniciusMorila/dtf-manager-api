"""Webhook público autenticado pela assinatura do provedor, sem JWT."""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import Settings
from app.db.session import SessionDependency
from app.modules.payments.mercado_pago import (
    InvalidSignature,
    MercadoPagoPaymentProvider,
    ProviderInvalidPayment,
    ProviderUnavailable,
)
from app.modules.payments.service import (
    PaymentConflict,
    PaymentError,
    PaymentNotFound,
    PaymentService,
)

router: APIRouter = APIRouter(prefix="/payments/mercado-pago", tags=["payments"])
logger: logging.Logger = logging.getLogger(__name__)


def get_provider() -> MercadoPagoPaymentProvider:
    try:
        return MercadoPagoPaymentProvider(Settings())
    except (ProviderUnavailable, ValueError):
        raise HTTPException(503, "Pagamentos indisponíveis.") from None


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
