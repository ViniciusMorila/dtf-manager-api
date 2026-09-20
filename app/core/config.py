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
