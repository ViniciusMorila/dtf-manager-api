"""Identificador de máquina enviado pelo aplicativo desktop."""

import re
from typing import Annotated

from pydantic import AfterValidator

_MACHINE_ID: re.Pattern[str] = re.compile(r"^[a-z0-9._:{}\-]{8,128}$")


def normalize_machine_id(value: str) -> str:
    """Remove espaços externos e usa minúsculas para comparação estável.

    O ID não é segredo nem prova de identidade: é só o vínculo da licença ao PC.
    """
    candidate: str = value.strip().lower()
    if not _MACHINE_ID.fullmatch(candidate):
        raise ValueError("ID de máquina inválido.")
    return candidate


MachineId = Annotated[str, AfterValidator(normalize_machine_id)]
