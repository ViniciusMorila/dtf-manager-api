"""Testes locais com credenciais fictícias e valores aleatórios efêmeros."""

import logging
import secrets

import pytest
from argon2 import PasswordHasher

from app.shared.security.password import (
    hash_password,
    validate_password_strength,
    verify_password,
)


@pytest.mark.parametrize("password", ["", "abc1234", "abcdefgh", "12345678", "        "])
def test_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(ValueError, match="8 caracteres"):
        validate_password_strength(password)
    with pytest.raises(ValueError):
        hash_password(password)


@pytest.mark.parametrize("password", ["abcdefg1", "ABCDEFG1", "áéíóúçã1", "frase longa 1", "  abcde1  "])
def test_accepts_reasonable_passwords(password: str) -> None:
    assert validate_password_strength(password) is None


def test_hash_round_trip_and_random_salt() -> None:
    password: str = secrets.token_urlsafe(24) + "a1"
    first: str = hash_password(password)
    second: str = hash_password(password)
    assert first.startswith("$argon2id$")
    assert second.startswith("$argon2id$")
    assert first != second
    assert password not in first
    assert verify_password(password, first)
    assert verify_password(password, second)
    assert PasswordHasher().verify(first, password)
    assert not verify_password(password + "different", first)


def test_preserves_spaces_case_and_unicode() -> None:
    password: str = "  áBcdef1  "
    stored: str = hash_password(password)
    assert verify_password(password, stored)
    assert not verify_password(password.strip(), stored)
    assert not verify_password(password.lower(), stored)


@pytest.mark.parametrize("stored", ["", "not-a-hash", "$argon2id$invalid", "$argon2id$v=19$m=65536,t=3,p=4$invalid$invalid"])
def test_invalid_hash_returns_false(stored: str) -> None:
    assert not verify_password(secrets.token_urlsafe(16), stored)


def test_verification_does_not_reapply_strength_policy() -> None:
    """Uma senha legada válida continua verificável mesmo fora da política atual."""
    legacy: str = secrets.token_hex(2)
    stored: str = PasswordHasher().hash(legacy)
    assert verify_password(legacy, stored)


def test_no_credentials_in_logs_or_validation_error(caplog: pytest.LogCaptureFixture) -> None:
    password: str = secrets.token_urlsafe(16) + "a1"
    weak: str = secrets.token_hex(2)
    with caplog.at_level(logging.DEBUG):
        stored: str = hash_password(password)
        assert verify_password(password, stored)
        assert not verify_password(password + "different", stored)
        assert not verify_password(password, "invalid")
        with pytest.raises(ValueError) as error:
            hash_password(weak)
    assert weak not in str(error.value)
    assert password not in caplog.text
    assert stored not in caplog.text
    assert weak not in caplog.text
