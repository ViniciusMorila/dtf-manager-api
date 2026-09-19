"""Evita divergência entre as dependências de produção e a entrada do Railpack."""

import tomllib
from pathlib import Path


def test_railpack_requirements_match_production_dependencies() -> None:
    root: Path = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements: list[str] = [
        line.strip()
        for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert sorted(requirements) == sorted(project["project"]["dependencies"])
