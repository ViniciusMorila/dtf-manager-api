"""Contrato do seed e repetição transacional em PostgreSQL, quando disponível."""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.session import get_engine
from app.modules.plans.models import Plan
from scripts.seed_plans import STANDARD_PLANS, build_seed_statement, main, seed_plans


def test_standard_plan_values() -> None:
    assert [(plan.code.value, plan.name, plan.duration_months, plan.is_lifetime) for plan in STANDARD_PLANS] == [
        ("MONTHLY", "1 mês", 1, False),
        ("SEMIANNUAL", "6 meses", 6, False),
        ("ANNUAL", "1 ano", 12, False),
        ("LIFETIME", "Vitalício", None, True),
    ]


def test_seed_uses_postgresql_unique_conflict_handling() -> None:
    sql: str = str(build_seed_statement().compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (code) DO NOTHING" in sql
    assert "RETURNING plans.code" in sql


def test_cli_missing_configuration_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    get_engine.cache_clear()
    assert main() == 1
    output = capsys.readouterr()
    assert "Falha no seed" in output.err
    assert "Seed concluído" not in output.out


def test_seed_repeated_execution_in_postgresql() -> None:
    """Verifica idempotência real e faz rollback de todas as inserções de teste."""
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: idempotência no PostgreSQL não verificada")
    table = Plan.__table__
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            before = {row["code"]: dict(row) for row in connection.execute(select(table)).mappings()}
            inserted: int = seed_plans(connection)
            first = {row["code"]: dict(row) for row in connection.execute(select(table)).mappings()}
            assert inserted == sum(plan.code not in before for plan in STANDARD_PLANS)
            assert seed_plans(connection) == 0
            assert seed_plans(connection) == 0
            after = {row["code"]: dict(row) for row in connection.execute(select(table)).mappings()}
            assert first == after
            for plan in STANDARD_PLANS:
                row = after[plan.code]
                if plan.code in before:
                    assert row == before[plan.code]
                else:
                    assert row["name"] == plan.name
                    assert row["duration_months"] == plan.duration_months
                    assert row["is_lifetime"] == plan.is_lifetime
                    assert row["is_active"] is True
        finally:
            transaction.rollback()
