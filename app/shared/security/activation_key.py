"""Chaves de ativação XXXX-XXXX-XXXX-XXXX geradas com o módulo secrets."""

import re
import secrets
from hashlib import sha256

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


def hash_activation_key(key: str) -> str:
    """Digest SHA-256 da forma normalizada; somente ele é persistido."""
    return sha256(key.encode("utf-8")).hexdigest()


def mask_activation_key(suffix: str) -> str:
    return f"****-****-****-{suffix}"
