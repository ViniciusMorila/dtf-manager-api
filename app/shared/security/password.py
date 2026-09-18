"""Hash e verificação com Argon2id, sem registrar credenciais."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

_password_hasher: PasswordHasher = PasswordHasher(type=Type.ID)


def validate_password_strength(password: str) -> None:
    """Exige 8 caracteres, uma letra e um número; não modifica a senha."""
    if (
        len(password) < 8
        or not any(character.isalpha() for character in password)
        or not any(character.isdecimal() for character in password)
    ):
        raise ValueError("A senha deve ter pelo menos 8 caracteres, uma letra e um número.")


def hash_password(password: str) -> str:
    """Valida a força e gera um hash Argon2id com salt aleatório da biblioteca."""
    validate_password_strength(password)
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Retorna False para senha incorreta ou hash inválido, sem aplicar política nova."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
