"""Diagnóstico por allowlist: nunca serializa exceções, SQL, headers ou bodies."""
import json
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


def capture_webhook_headers(*, enabled: bool, data_ids: list[str],
                            signatures: list[str], request_ids: list[str]) -> None:
    """Temporary, opt-in capture for one payment; never accepts other headers."""
    if not enabled or data_ids != ["183729938334"]:
        return
    import re

    def safe_header(values: list[str], pattern: str, maximum: int) -> str | None:
        if not values:
            return None
        if len(values) != 1 or len(values[0]) > maximum:
            return "[REDACTED]"
        return values[0] if re.fullmatch(pattern, values[0]) else "[REDACTED]"

    try:
        event = {
            "event": "mercado_pago_webhook_capture",
            "data.id": data_ids[0],
            "x-signature": safe_header(signatures,
                r" *(?:ts=[0-9]{1,20} *, *v1=[0-9a-fA-F]{64}|v1=[0-9a-fA-F]{64} *, *ts=[0-9]{1,20}) *", 128),
            "x-request-id": safe_header(request_ids,
                r" *[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12} *", 80),
        }
        logger.warning(json.dumps(event, ensure_ascii=True))
    except Exception:
        return

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


# Temporary opt-in diagnostics. Never log arbitrary header/body/exception values.
def _emit_webhook_diagnostic(*, enabled: bool, stage: str, result: str,
                           data_id: str = "", request_id: str = "",
                           signature: str = "") -> None:
    """Emit bounded identifiers and signature shape, never signature/hash material."""
    if not enabled:
        return
    import os
    import re
    from importlib.metadata import version

    def identifier(value: str, *, numeric: bool = False) -> str | None:
        if not value:
            return None
        pattern = r"[0-9]{1,64}" if numeric else r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        return value if re.fullmatch(pattern, value) else "[REDACTED]"

    timestamps: list[str] = []
    hashes: list[str] = []
    for part in signature.split(","):
        key, separator, value = part.partition("=")
        if separator:
            if key.strip().lower() == "ts":
                timestamps.append(value.strip())
            elif key.strip().lower() == "v1":
                hashes.append(value.strip())
    ts = timestamps[0] if len(timestamps) == 1 else ""
    digest = hashes[0] if len(hashes) == 1 else ""
    safe_results = {"valid", "invalid", "provider_not_found", "provider_response_invalid",
                    "checkout_evidence_missing", "checkout_evidence_mismatch", "seller_mismatch",
                    "environment_mismatch", "local_payment_not_found", "reconciliation_conflict",
                    "provider_unavailable", "persistence_or_activation_failed", "missing_references"}
    event = {
        "event": "mercado_pago_webhook_diagnostic",
        "stage": stage if stage in {"hmac", "reconciliation"} else "unknown",
        "result": result if result in safe_results else "unknown",
        "data_id_received": identifier(data_id, numeric=True),
        "data_id_normalized": identifier(data_id.lower().strip(), numeric=True),
        "request_id": identifier(request_id.strip()),
        "timestamp": identifier(ts, numeric=True),
        "signature_present": bool(signature), "request_id_present": bool(request_id),
        "timestamp_count": len(timestamps), "v1_count": len(hashes),
        "v1_length": len(digest),
        "v1_valid_hex": bool(re.fullmatch(r"[0-9a-fA-F]{64}", digest)),
        "sdk_version": version("mercadopago"),
        "deployment_id": identifier(os.environ.get("RAILWAY_DEPLOYMENT_ID", "")),
    }
    logger.warning(json.dumps(event, ensure_ascii=True))


def log_webhook_diagnostic(*, enabled: bool, stage: str, result: str,
                           data_id: str = "", request_id: str = "",
                           signature: str = "") -> None:
    """Diagnostic failures must never affect authentication or payment processing."""
    if not enabled:
        return
    try:
        _emit_webhook_diagnostic(enabled=True, stage=stage, result=result,
                                 data_id=data_id, request_id=request_id, signature=signature)
    except Exception:
        # No fallback logging: exceptions can contain sensitive input.
        return
