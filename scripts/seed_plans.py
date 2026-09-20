"""Cadastro idempotente: python -m scripts.seed_plans."""

import sys
from dataclasses import dataclass
from decimal import Decimal

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
    price: Decimal


STANDARD_PLANS: tuple[StandardPlan, ...] = (
    StandardPlan(PlanCode.MONTHLY, "1 mês", 1, False, Decimal("79.90")),
    StandardPlan(PlanCode.SEMIANNUAL, "6 meses", 6, False, Decimal("399.90")),
    StandardPlan(PlanCode.ANNUAL, "1 ano", 12, False, Decimal("699.90")),
    StandardPlan(PlanCode.LIFETIME, "Vitalício", None, True, Decimal("1499.90")),
)


def build_seed_statement() -> Insert:
    """A constraint única de code resolve também conflitos concorrentes."""
    statement: Insert = (
        insert(Plan.__table__)
        .values([
            {
                "code": plan.code,
                "name": plan.name,
                "duration_months": plan.duration_months,
                "is_lifetime": plan.is_lifetime,
                "is_active": True,
                "price": plan.price,
            }
            for plan in STANDARD_PLANS
        ])
    )
    return (
        statement.on_conflict_do_update(
            index_elements=[Plan.__table__.c.code],
            set_={"price": statement.excluded.price},
            where=Plan.__table__.c.price.is_distinct_from(statement.excluded.price),
        )
        .returning(Plan.__table__.c.code)
    )


def seed_plans(connection: Connection) -> int:
    """Insere ausentes ou atualiza somente preços divergentes, na transação do chamador."""
    return len(connection.execute(build_seed_statement()).scalars().all())


def main() -> int:
    """Executa os quatro cadastros em uma única transação."""
    engine: Engine | None = None
    try:
        engine = get_engine()
        with engine.begin() as connection:
            affected: int = seed_plans(connection)
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
    print(f"Seed concluído: {affected} plano(s) inserido(s) ou com preço atualizado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
