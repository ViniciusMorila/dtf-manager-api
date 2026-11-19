"""Chaves de ativação XXXX-XXXX-XXXX-XXXX geradas com o módulo secrets."""

import hmac
import re
import secrets
from hashlib import sha256

from app.core.config import Settings

# Sem 0/O, 1/I/L e U para evitar confusão na digitação: 30 símbolos, ~78 bits por chave.
ALPHABET: str = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_GROUPS: int = 4
_GROUP_SIZE: int = 4
_NORMALIZED: re.Pattern[str] = re.compile(rf"^[{ALPHABET}]{{{_GROUPS * _GROUP_SIZE}}}$")


def generate_activation_key() -> str:
    raw: str = "".join(secrets.choice(ALPHABET) for _ in range(_GROUPS * _GROUP_SIZE))
    return format_activation_key(raw)


def format_activation_key(raw: str) -> str:
    return "-".join(raw[index:index + _GROUP_SIZE] for index in range(0, len(raw), _GROUP_SIZE))


def normalize_activation_key(value: str) -> str | None:
    """Aceita minúsculas, espaços e hífens; devolve None para formato impossível."""
    compact: str = re.sub(r"[\s\-]", "", value).upper()
    if not _NORMALIZED.fullmatch(compact):
        return None
    return format_activation_key(compact)


def activation_key_secret(settings: Settings | None = None) -> str:
    """ValueError se ACTIVATION_KEY_SECRET estiver ausente ou fraco (nunca expõe o valor)."""
    return (settings or Settings()).validate_activation_key_secret()


def hash_activation_key(key: str, secret: str) -> str:
    """HMAC-SHA256 da forma normalizada com o pepper do servidor; somente ele é persistido.

    Um vazamento do banco não permite testar chaves sem o segredo, que fica fora do banco.
    """
    return hmac.new(secret.encode("utf-8"), key.encode("utf-8"), sha256).hexdigest()


def mask_activation_key(suffix: str) -> str:
    return f"****-****-****-{suffix}"
