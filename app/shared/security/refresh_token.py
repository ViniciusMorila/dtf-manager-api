"""Digest de tokens de alta entropia para persistência e futura consulta."""

from hashlib import sha256


def hash_refresh_token(token: str) -> str:
    """Retorna SHA-256 hexadecimal; nunca armazena ou registra o token recebido."""
    return sha256(token.encode("utf-8")).hexdigest()
