import json
import logging

import pytest

from app.modules.payments.diagnostics import capture_webhook_headers


@pytest.mark.parametrize("enabled,payment,emits", [
    (False, "182705996051", False),
    (True, "182705996051", True),
    (True, "999", False),
])
def test_capture_is_opt_in_and_scoped(enabled: bool, payment: str, emits: bool,
                                    caplog: pytest.LogCaptureFixture,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging.getLogger("app.modules.payments.diagnostics"), "disabled", False)
    signature = "ts=1742505638683,v1=" + "a" * 64
    request_id = "01234567-89ab-cdef-0123-456789abcdef"
    capture_webhook_headers(enabled=enabled, data_ids=[payment], signatures=[signature],
                            request_ids=[request_id])
    assert bool(caplog.records) == emits
    if emits:
        assert json.loads(caplog.records[0].message) == {
            "event": "mercado_pago_webhook_capture", "data.id": payment,
            "x-signature": signature, "x-request-id": request_id,
        }


def test_capture_redacts_injection_and_duplicates(caplog: pytest.LogCaptureFixture,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging.getLogger("app.modules.payments.diagnostics"), "disabled", False)
    capture_webhook_headers(enabled=True, data_ids=["182705996051"],
                            signatures=["untrusted\nheader"], request_ids=["one", "two"])
    event = json.loads(caplog.records[0].message)
    assert event["x-signature"] == event["x-request-id"] == "[REDACTED]"
    assert "untrusted" not in caplog.text
    with monkeypatch.context() as context:
        context.setattr("app.modules.payments.diagnostics.logger.warning",
                        lambda *args: (_ for _ in ()).throw(RuntimeError()))
        capture_webhook_headers(enabled=True, data_ids=["182705996051"],
                                signatures=[], request_ids=[])
