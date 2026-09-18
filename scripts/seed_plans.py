"""Cadastro idempotente: python -m scripts.seed_plans."""

import sys
from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine
from app.modules.plans.models import Plan, PlanCode


@dataclass(frozen=True)
class StandardPlan:
    code: PlanCode
    name: str
    duration_months: int | None
    is_lifetime: bool


STANDARD_PLANS: tuple[StandardPlan, ...] = (
    StandardPlan(PlanCode.MONTHLY, "1 mês", 1, False),
    StandardPlan(PlanCode.SEMIANNUAL, "6 meses", 6, False),
    StandardPlan(PlanCode.ANNUAL, "1 ano", 12, False),
    StandardPlan(PlanCode.LIFETIME, "Vitalício", None, True),
)


def build_seed_statement() -> Insert:
    """A constraint única de code resolve também conflitos concorrentes."""
    return (
        insert(Plan.__table__)
        .values([
            {
                "code": plan.code,
                "name": plan.name,
                "duration_months": plan.duration_months,
                "is_lifetime": plan.is_lifetime,
                "is_active": True,
            }
            for plan in STANDARD_PLANS
        ])
        .on_conflict_do_nothing(index_elements=[Plan.__table__.c.code])
        .returning(Plan.__table__.c.code)
    )


def seed_plans(connection: Connection) -> int:
    """Insere apenas códigos ausentes; a transação pertence ao chamador."""
    return len(connection.execute(build_seed_statement()).scalars().all())


def main() -> int:
    """Executa os quatro cadastros em uma única transação."""
    engine: Engine | None = None
    try:
        engine = get_engine()
        with engine.begin() as connection:
            inserted: int = seed_plans(connection)
    except (ValueError, SQLAlchemyError):
        print(
            "Falha no seed: verifique as configurações, a conexão PostgreSQL "
            "e se alembic upgrade head foi aplicado.",
            file=sys.stderr,
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()
            get_engine.cache_clear()
    print(f"Seed concluído: {inserted} plano(s) inserido(s). Planos existentes preservados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
