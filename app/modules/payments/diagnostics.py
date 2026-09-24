"""Diagnóstico por allowlist: nunca serializa exceções, SQL, headers ou bodies."""
import json
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Somente literais conhecidos podem sair de respostas externas para os logs.
# Mensagens desconhecidas são omitidas integralmente, pois podem ecoar credenciais.
SAFE_CODES = frozenset({
    "bad_request", "unauthorized", "forbidden", "not_found", "internal_error",
    "invalid_access_token", "invalid_token", "invalid_items", "invalid_date",
    "invalid_expiration_date_to", "invalid_expiration_date_from", "invalid_collector_id",
    "invalid_sponsor_id", "invalid_collector_email", "invalid_operation_type",
    "invalid_back_urls", "invalid_payment_methods", "invalid_marketplace_fee",
    "invalid_id", "invalid_shipments", "invalid_binary_mode",
    "collector_does_not_comply_with_current_regulation", "PA_UNAUTHORIZED_RESULT_FROM_POLICIES",
})
SAFE_MESSAGES = frozenset({
    "unit_price invalid.", "access denied.", "invalid access token", "Unauthorized",
    "Forbidden", "expiration_date_to invalid.", "expiration_date_from invalid.",
    "invalid date of expiration.", "collector_id invalid.",
    "At least one policy returned UNAUTHORIZED.",
})


def safe_value(value: Any, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and value in allowed else "[REDACTED]"


def log_checkout_failure(*, stage: str, payment_id: UUID | None = None,
                         error: Exception | None = None, result: Any = None) -> None:
    """JSON no próprio message para aparecer também no formatter padrão do Uvicorn."""
    result = result if isinstance(result, dict) else {}
    status = result.get("status")
    body = result.get("response")
    body = body if isinstance(body, dict) else {}
    causes = body.get("cause", body.get("causes", []))
    causes = causes if isinstance(causes, list) else []
    event = {
        "event": "checkout_failure", "provider": "mercado_pago", "stage": stage,
        "payment_id": str(payment_id) if payment_id else None,
        "exception_type": type(error).__name__ if error is not None else None,
        "provider_http_status": status if type(status) is int and 100 <= status <= 599 else None,
        "provider_code": safe_value(body.get("error", body.get("code")), SAFE_CODES),
        "provider_causes": [
            {"code": safe_value(cause.get("code"), SAFE_CODES),
             "message": safe_value(cause.get("description", cause.get("message")), SAFE_MESSAGES)}
            for cause in causes[:5] if isinstance(cause, dict)
        ],
        "message": safe_value(body.get("message"), SAFE_MESSAGES)
                   or "Checkout failed; raw exception and response omitted.",
    }
    logger.warning(json.dumps(event, ensure_ascii=True))
