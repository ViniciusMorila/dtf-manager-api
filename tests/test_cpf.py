"""Casos matemáticos de CPF; não representam confirmação de identidade."""

import pytest

from app.shared.utils.cpf import normalize_cpf, validate_cpf


@pytest.mark.parametrize("cpf", ["12345678909", "123.456.789-09", " 123 .456.789 - 09 ", "123\t456\n78909"])
def test_valid_cpf_formatted_or_unformatted(cpf: str) -> None:
    assert normalize_cpf(cpf) == "12345678909"
    assert validate_cpf(cpf)


@pytest.mark.parametrize("cpf", ["12345678919", "12345678900", "123.456.789-08", "", "123", "123456789090", " .- "])
def test_invalid_cpf(cpf: str) -> None:
    assert not validate_cpf(cpf)


@pytest.mark.parametrize("digit", list("0123456789"))
def test_repeated_digits_are_invalid(digit: str) -> None:
    assert not validate_cpf(digit * 11)
    assert not validate_cpf(f"{digit * 3}.{digit * 3}.{digit * 3}-{digit * 2}")


@pytest.mark.parametrize("cpf", ["123.456.789-09a", "a12345678909", "123/456/78909", "+12345678909", "１２３４５６７８９０９", "12345678909\x00"])
def test_invalid_characters_are_not_silently_removed(cpf: str) -> None:
    with pytest.raises(ValueError, match="caracteres inválidos"):
        normalize_cpf(cpf)
    assert not validate_cpf(cpf)


def test_leading_zero_is_preserved() -> None:
    assert normalize_cpf("012.345.678-90") == "01234567890"
    assert validate_cpf("012.345.678-90")


def test_normalization_is_idempotent_and_does_not_validate_checksum() -> None:
    normalized: str = normalize_cpf("123.456.789-00")
    assert normalize_cpf(normalized) == normalized
    assert not validate_cpf(normalized)
