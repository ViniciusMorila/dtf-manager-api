"""Validação da configuração sem banco ou credenciais reais."""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Evita ler o .env e as configurações reais do desenvolvedor."""
    names: set[str] = {name.upper() for name in Settings.model_fields} | {"DTF_APP_NAME"}
    for name in tuple(os.environ):
        if name.upper() in names:
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gera valores efêmeros e independentes, sem secrets hardcoded."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))


def test_development_defaults() -> None:
    settings: Settings = Settings()
    assert settings.environment == "development"
    assert settings.database_url is None
    assert settings.jwt_access_secret is None
    assert settings.jwt_refresh_secret is None
    assert settings.jwt_access_expire_minutes == 15
    assert settings.jwt_refresh_expire_days == 30
    assert settings.port == 8000


def test_dotenv_and_environment_priority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg://usuario:senha@localhost:5432/dtf_manager\n"
        "JWT_ACCESS_SECRET=trocar\nJWT_REFRESH_SECRET=trocar\n"
        "JWT_ACCESS_EXPIRE_MINUTES=10\nJWT_REFRESH_EXPIRE_DAYS=20\n"
        "ENVIRONMENT=development\nHOST=0.0.0.0\nPORT=8001\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PORT", "9000")
    settings: Settings = Settings()
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://")
    assert settings.jwt_access_secret is not None
    assert settings.jwt_access_secret.get_secret_value() == "trocar"
    assert settings.jwt_refresh_secret == settings.jwt_access_secret
    assert settings.jwt_access_expire_minutes == 10
    assert settings.jwt_refresh_expire_days == 20
    assert settings.environment == "development"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


def test_mercado_pago_dotenv_and_environment_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    access: str = secrets.token_urlsafe(40)
    webhook: str = secrets.token_urlsafe(40)
    replacement: str = secrets.token_urlsafe(40)
    (tmp_path / ".env").write_text(
        f"MERCADO_PAGO_ACCESS_TOKEN={access}\n"
        f"MERCADO_PAGO_WEBHOOK_SECRET={webhook}\n"
        "MERCADO_PAGO_ENVIRONMENT=test\n",
        encoding="utf-8",
    )
    settings: Settings = Settings()
    settings.validate_mercado_pago()
    assert settings.mercado_pago_access_token is not None
    assert settings.mercado_pago_access_token.get_secret_value() == access
    assert settings.mercado_pago_webhook_secret is not None
    assert settings.mercado_pago_webhook_secret.get_secret_value() == webhook
    assert settings.mercado_pago_environment == "test"
    monkeypatch.setenv("MERCADO_PAGO_ACCESS_TOKEN", replacement)
    assert Settings().mercado_pago_access_token.get_secret_value() == replacement
    assert access not in repr(settings) and webhook not in repr(settings)


@pytest.mark.parametrize("name", ["JWT_ACCESS_SECRET", "JWT_REFRESH_SECRET"])
@pytest.mark.parametrize("value", [None, "", "trocar", "a" * 64, "example-" * 8, " " * 40])
def test_production_rejects_unsafe_secrets(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str | None,
) -> None:
    production_environment(monkeypatch)
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=name):
        Settings()


def test_production_rejects_reused_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    production_environment(monkeypatch)
    monkeypatch.setenv("JWT_REFRESH_SECRET", os.environ["JWT_ACCESS_SECRET"])
    with pytest.raises(ValidationError, match="diferentes"):
        Settings()


def test_production_accepts_generated_secrets_and_masks_values(monkeypatch: pytest.MonkeyPatch) -> None:
    production_environment(monkeypatch)
    settings: Settings = Settings()
    assert settings.environment == "production"
    for name in ("JWT_ACCESS_SECRET", "JWT_REFRESH_SECRET"):
        assert os.environ[name] not in repr(settings)
        assert os.environ[name] not in settings.model_dump_json()


@pytest.mark.parametrize(
    ("name", "value"),
    [("PORT", "0"), ("PORT", "65536"), ("PORT", "invalid"),
     ("JWT_ACCESS_EXPIRE_MINUTES", "0"), ("JWT_REFRESH_EXPIRE_DAYS", "-1"),
     ("ENVIRONMENT", "prodution")],
)
def test_invalid_settings(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings()


def test_validation_error_does_not_expose_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    production_environment(monkeypatch)
    monkeypatch.delenv("JWT_REFRESH_SECRET")
    with pytest.raises(ValidationError) as error:
        Settings()
    assert os.environ["JWT_ACCESS_SECRET"] not in str(error.value)


@pytest.mark.parametrize("valid", [False, True])
def test_asgi_import_validates_production_settings(monkeypatch: pytest.MonkeyPatch, valid: bool) -> None:
    production_environment(monkeypatch)
    if not valid:
        monkeypatch.delenv("JWT_ACCESS_SECRET")
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-c", "from app.main import app"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert (result.returncode == 0) is valid
    if not valid:
        assert "JWT_ACCESS_SECRET" in result.stderr
        assert os.environ["JWT_REFRESH_SECRET"] not in result.stderr
