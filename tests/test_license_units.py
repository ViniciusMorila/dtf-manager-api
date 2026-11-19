"""Regras puras de licença, telefone, chave, rate limit e página admin (sem banco)."""

import asyncio
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.main import create_app
from app.modules.licenses.models import License, LicensePlanCode, LicenseState
from app.modules.licenses.rules import (
    days_remaining,
    expire_if_due,
    plan_end,
    renewed_expiration,
    state_after_change,
)
from app.shared.exceptions.license import LicenseAlreadyLifetime
from app.shared.security.activation_key import (
    ALPHABET,
    generate_activation_key,
    hash_activation_key,
    normalize_activation_key,
)
from app.shared.security.rate_limit import RateLimiter
from app.shared.utils.calendar import add_calendar_months
from app.shared.utils.machine import normalize_machine_id
from app.shared.utils.phone import normalize_phone

NOW: datetime = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(("raw", "expected"), [
    ("(11) 91234-5678", "11912345678"), ("11912345678", "11912345678"), ("+55 11 91234-5678", "11912345678"),
    ("5511912345678", "11912345678"), ("(21) 3456-7890", "2134567890"), (" 61.99876.5432 ", "61998765432"),
])
def test_phone_normalized_to_digits(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "1234", "(10) 91234-5678", "(20) 91234-5678", "(11) 81234-5678", "(11) 1234-5678",
    "11 9123a-5678", "(11) 99999-9999", "+1 415 555 0100", "1191234567890", "11/91234-5678", "１１９１２３４５６７８",
])
def test_phone_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_machine_id_normalization() -> None:
    assert normalize_machine_id("  ABCDEF12-3456  ") == "abcdef12-3456"
    for invalid in ("curto", "com espaço no meio", "x" * 129, "<script>alert</script>"):
        with pytest.raises(ValueError):
            normalize_machine_id(invalid)


def test_activation_key_format_entropy_and_normalization() -> None:
    keys: set[str] = {generate_activation_key() for _ in range(500)}
    assert len(keys) == 500
    for key in keys:
        assert re.fullmatch(rf"[{ALPHABET}]{{4}}(-[{ALPHABET}]{{4}}){{3}}", key)
        assert normalize_activation_key(key.lower().replace("-", " ")) == key
        assert hash_activation_key(key) != key and len(hash_activation_key(key)) == 64
    for invalid in ("", "ABCD-EFGH", "ABCD-EFGH-JKMN-PQR0", "ABCD-EFGH-JKMN-PQRS-TVWX", "ABCD_EFGH_JKMN_PQRS"):
        assert normalize_activation_key(invalid) is None


def make_license(plan: LicensePlanCode, status: LicenseState, expires_at: datetime | None) -> License:
    return License(plan=plan, status=status, activated_at=NOW - timedelta(days=1), expires_at=expires_at)


@pytest.mark.parametrize(("plan", "expected"), [
    (LicensePlanCode.TESTE, NOW + timedelta(days=7)),
    (LicensePlanCode.MENSAL, datetime(2026, 2, 28, 12, 0, tzinfo=UTC)),
    (LicensePlanCode.SEMESTRAL, datetime(2026, 7, 31, 12, 0, tzinfo=UTC)),
    (LicensePlanCode.ANUAL, datetime(2027, 1, 31, 12, 0, tzinfo=UTC)),
    (LicensePlanCode.VITALICIO, None),
])
def test_plan_durations_count_from_start(plan: LicensePlanCode, expected: datetime | None) -> None:
    assert plan_end(plan, NOW) == expected


def test_renewal_adds_to_valid_expiration() -> None:
    current: datetime = NOW + timedelta(days=10)
    license: License = make_license(LicensePlanCode.MENSAL, LicenseState.ATIVA, current)
    assert renewed_expiration(license, LicensePlanCode.ANUAL, NOW) == add_calendar_months(current, 12)


@pytest.mark.parametrize("status", [LicenseState.ATIVA, LicenseState.EXPIRADA])
def test_renewal_of_expired_starts_now(status: LicenseState) -> None:
    license: License = make_license(LicensePlanCode.MENSAL, status, NOW - timedelta(days=3))
    assert renewed_expiration(license, LicensePlanCode.MENSAL, NOW) == add_calendar_months(NOW, 1)


def test_lifetime_rules() -> None:
    license: License = make_license(LicensePlanCode.ANUAL, LicenseState.ATIVA, NOW + timedelta(days=1))
    assert renewed_expiration(license, LicensePlanCode.VITALICIO, NOW) is None
    lifetime: License = make_license(LicensePlanCode.VITALICIO, LicenseState.ATIVA, None)
    with pytest.raises(LicenseAlreadyLifetime):
        renewed_expiration(lifetime, LicensePlanCode.MENSAL, NOW)
    assert days_remaining(lifetime, NOW) is None
    assert expire_if_due(lifetime, NOW + timedelta(days=10_000)) is False


def test_expiration_and_days_remaining() -> None:
    license: License = make_license(LicensePlanCode.TESTE, LicenseState.ATIVA, NOW + timedelta(days=6, hours=1))
    assert days_remaining(license, NOW) == 7
    assert expire_if_due(license, NOW) is False
    license.expires_at = NOW
    assert expire_if_due(license, NOW) is True and license.status == LicenseState.EXPIRADA
    assert days_remaining(license, NOW) == 0
    blocked: License = make_license(LicensePlanCode.TESTE, LicenseState.BLOQUEADA, NOW - timedelta(days=1))
    assert expire_if_due(blocked, NOW) is False and blocked.status == LicenseState.BLOQUEADA
    assert state_after_change(blocked, NOW) == LicenseState.BLOQUEADA


def test_rate_limiter_sliding_window() -> None:
    limiter: RateLimiter = RateLimiter()
    assert all(limiter.hit("login", "1.1.1.1", 3, 60, now=100.0) is None for _ in range(3))
    assert limiter.hit("login", "1.1.1.1", 3, 60, now=101.0) == 60
    assert limiter.hit("login", "2.2.2.2", 3, 60, now=101.0) is None
    assert limiter.hit("register", "1.1.1.1", 3, 60, now=101.0) is None
    assert limiter.hit("login", "1.1.1.1", 3, 60, now=160.5) is None


def call(method: str, path: str, **kwargs: object) -> Response:
    """Sem banco: a sessão é um mock que estes testes nunca devem alcançar."""
    application = create_app()
    session: MagicMock = MagicMock(spec=Session)
    session.begin.side_effect = AssertionError("banco não deveria ser acessado")
    application.dependency_overrides[get_session] = lambda: session

    async def run() -> Response:
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)  # type: ignore[arg-type]

    return asyncio.run(run())


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))


@pytest.mark.parametrize(("path", "body"), [
    ("/auth/login", {"email": "invalido", "password": "x"}),
    ("/auth/register", {"email": "invalido"}),
    ("/licencas/ativar-chave", {"chave": ""}),
])
def test_rate_limit_applies_before_validation(isolated: None, monkeypatch: pytest.MonkeyPatch,
                                              path: str, body: dict[str, str]) -> None:
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "3")
    statuses: list[int] = [call("POST", path, json=body).status_code for _ in range(4)]
    assert statuses[:3] == [422, 422, 422]
    blocked: Response = call("POST", path, json=body)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "MUITAS_TENTATIVAS"
    assert int(blocked.headers["retry-after"]) >= 1


def test_rate_limit_uses_last_forwarded_hop(isolated: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "2")
    body: dict[str, str] = {"email": "invalido", "password": "x"}
    for _ in range(2):
        call("POST", "/auth/login", json=body, headers={"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
    # Trocar o primeiro valor (controlado pelo cliente) não escapa do limite.
    spoofed: Response = call("POST", "/auth/login", json=body, headers={"X-Forwarded-For": "8.8.8.8, 10.0.0.1"})
    assert spoofed.status_code == 429
    other: Response = call("POST", "/auth/login", json=body, headers={"X-Forwarded-For": "8.8.8.8, 10.0.0.2"})
    assert other.status_code == 422


def test_license_routes_require_authentication(isolated: None) -> None:
    for method, path in (("GET", "/licencas/status"), ("GET", "/admin/licencas"), ("POST", "/admin/chaves")):
        response: Response = call(method, path, json={} if method == "POST" else None)
        assert response.status_code in (401, 422)
    unauthenticated: Response = call("GET", "/admin/licencas")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"
    no_credentials: Response = call("POST", "/licencas/ativar-chave", json={"chave": "ABCD-EFGH-JKMN-PQRS"})
    assert no_credentials.status_code == 401


def test_admin_panel_is_static_with_nonce_csp(isolated: None) -> None:
    response: Response = call("GET", "/admin")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    nonce: str = re.search(r"script-src 'nonce-([^']+)'", response.headers["content-security-policy"]).group(1)
    assert f'<script nonce="{nonce}">' in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "innerHTML" not in response.text
    assert call("GET", "/admin").headers["content-security-policy"] != response.headers["content-security-policy"]
