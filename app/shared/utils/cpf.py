"""Normalização e validação matemática de CPF, sem uso em tokens ou licenças."""


def normalize_cpf(cpf: str) -> str:
    """Remove pontos, hífens e espaços; rejeita outros caracteres não numéricos."""
    digits: list[str] = []
    for character in cpf:
        if "0" <= character <= "9":
            digits.append(character)
        elif character in ".-" or character.isspace():
            continue
        else:
            raise ValueError("CPF contém caracteres inválidos.")
    return "".join(digits)


def validate_cpf(cpf: str) -> bool:
    """Valida 11 dígitos e ambos os verificadores, aceitando entrada formatada."""
    try:
        normalized: str = normalize_cpf(cpf)
    except ValueError:
        return False
    if len(normalized) != 11 or len(set(normalized)) == 1:
        return False

    for position in (9, 10):
        total: int = sum(
            int(normalized[index]) * (position + 1 - index)
            for index in range(position)
        )
        remainder: int = total % 11
        expected: int = 0 if remainder < 2 else 11 - remainder
        if int(normalized[position]) != expected:
            return False
    return True
