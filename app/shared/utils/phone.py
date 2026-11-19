"""Telefone brasileiro com DDD, normalizado para somente dígitos (10 ou 11)."""

import re

# DDDs atribuídos pela Anatel.
VALID_AREA_CODES: frozenset[int] = frozenset({
    11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35, 37, 38,
    41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68, 69,
    71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88, 89,
    91, 92, 93, 94, 95, 96, 97, 98, 99,
})
_ALLOWED: re.Pattern[str] = re.compile(r"^\+?[0-9 ().\-]+$")


def normalize_phone(value: str) -> str:
    """Remove formatação, o prefixo +55 opcional e valida DDD e tipo de linha.

    Aceita celular (DDD + 9 + 8 dígitos) e fixo (DDD + 2..5 + 7 dígitos).
    Letras ou outros símbolos provocam ValueError, sem serem descartados.
    """
    candidate: str = value.strip()
    if not candidate or not _ALLOWED.fullmatch(candidate) or not candidate.isascii():
        raise ValueError("Telefone inválido.")
    digits: str = re.sub(r"[^0-9]", "", candidate)
    if len(digits) in (12, 13) and digits.startswith("55"):
        digits = digits[2:]
    if len(digits) not in (10, 11) or int(digits[:2]) not in VALID_AREA_CODES:
        raise ValueError("Telefone inválido.")
    subscriber: str = digits[2:]
    if len(subscriber) == 9 and subscriber[0] != "9":
        raise ValueError("Telefone inválido.")
    if len(subscriber) == 8 and subscriber[0] not in "2345":
        raise ValueError("Telefone inválido.")
    if len(set(subscriber)) == 1:
        raise ValueError("Telefone inválido.")
    return digits
