"""Configuração central da API, sem conexão com serviços externos."""

from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Lê o ambiente e o .env do diretório de execução, nessa prioridade."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="API DTF Manager", validation_alias="DTF_APP_NAME")
    database_url: SecretStr | None = None
    mercado_pago_webhook_diagnostics: bool = False
    mercado_pago_webhook_capture: bool = False
    mercado_pago_checkout_test_seller: bool = False
    mercado_pago_application_id: str | None = Field(default=None, pattern=r"^[0-9]{1,64}$")
    mercado_pago_access_token: SecretStr | None = None
    mercado_pago_webhook_secret: SecretStr | None = None
    mercado_pago_environment: Literal["test", "production"] | None = None
    api_public_base_url: HttpUrl | None = None
    jwt_access_secret: SecretStr | None = None
    jwt_refresh_secret: SecretStr | None = None
    jwt_access_expire_minutes: int = Field(default=15, gt=0)
    jwt_refresh_expire_days: int = Field(default=30, gt=0)
    environment: Literal["development", "testing", "staging", "production"] = "development"
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    # Rate limit de cadastro, login e ativação de chave.
    rate_limit_enabled: bool = True
    # Login e ativar-chave: por IP e por rota.
    rate_limit_requests: int = Field(default=10, gt=0)
    rate_limit_window_seconds: int = Field(default=60, gt=0)
    # Cadastro: mais restrito, por IP e por e-mail.
    rate_limit_register_requests: int = Field(default=5, gt=0)
    rate_limit_register_email_requests: int = Field(default=3, gt=0)
    rate_limit_register_window_seconds: int = Field(default=3600, gt=0)
    # Senhas erradas por conta (login e ativar-chave com e-mail/senha somados), de qualquer IP.
    rate_limit_account_requests: int = Field(default=10, gt=0)
    rate_limit_account_window_seconds: int = Field(default=900, gt=0)
    # Só ligue depois de conferir nos logs que o proxy adiciona o IP real ao final de X-Forwarded-For.
    rate_limit_trust_proxy: bool = False
    # Diagnóstico temporário: registra X-Forwarded-For e o IP escolhido (contém IPs; desligue depois).
    rate_limit_diagnostics: bool = False
    # Usadas somente por scripts/seed_admin.py; nunca lidas pelas rotas.
    admin_email: str | None = None
    admin_senha: SecretStr | None = None
    admin_nome: str = Field(default="Administrador", min_length=1, max_length=200)

    @field_validator("api_public_base_url")
    @classmethod
    def validate_public_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and (value.scheme != "https" or value.username or value.password
                                  or value.query or value.fragment or value.path not in (None, "/")):
            raise ValueError("API_PUBLIC_BASE_URL deve ser uma origem HTTPS sem credenciais, caminho ou query.")
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Self:
        """Recusa ausência, exemplos, valores triviais e reutilização em produção."""
        if self.environment != "production":
            return self
        self.validate_jwt_secrets()
        if any((self.mercado_pago_access_token, self.mercado_pago_webhook_secret,
                self.mercado_pago_environment)):
            self.validate_mercado_pago()
        return self

    def validate_mercado_pago(self) -> None:
        """Integração opcional; uso exige configuração completa e não fictícia."""
        if self.mercado_pago_environment is None:
            raise ValueError("MERCADO_PAGO_ENVIRONMENT obrigatório para pagamentos.")
        if self.mercado_pago_checkout_test_seller and (
                self.mercado_pago_environment != "test" or self.environment != "testing"
                or self.mercado_pago_application_id is None):
            raise ValueError("Checkout com vendedor de teste exige ENVIRONMENT=testing, ambiente test e application ID.")
        for secret in (self.mercado_pago_access_token, self.mercado_pago_webhook_secret):
            value: str = secret.get_secret_value() if secret else ""
            if (len(value) < 32 or len(set(value)) < 8 or any(c.isspace() for c in value)
                    or any(s in value.lower() for s in ("trocar", "exemplo", "example", "changeme"))):
                raise ValueError("Credenciais Mercado Pago ausentes ou inseguras.")
        if self.mercado_pago_access_token == self.mercado_pago_webhook_secret:
            raise ValueError("Credenciais Mercado Pago devem ser distintas.")

    def validate_jwt_secrets(self) -> None:
        """Exige duas chaves seguras antes de qualquer operação JWT."""
        for name, secret in (
            ("JWT_ACCESS_SECRET", self.jwt_access_secret),
            ("JWT_REFRESH_SECRET", self.jwt_refresh_secret),
        ):
            value: str = secret.get_secret_value() if secret is not None else ""
            placeholders: tuple[str, ...] = (
                "trocar", "changeme", "change_me", "change-me", "example",
                "exemplo", "password", "secret", "replace", "senha",
            )
            if (
                len(value) < 32
                or len(set(value)) < 8
                or any(character.isspace() for character in value)
                or any(marker in value.casefold() for marker in placeholders)
            ):
                raise ValueError(
                    f"{name} ausente ou inseguro: forneça um valor "
                    "aleatório com pelo menos 32 caracteres, sem espaços ou placeholders."
                )

        if self.jwt_access_secret == self.jwt_refresh_secret:
            raise ValueError("Os secrets de acesso e refresh devem ser diferentes.")
