"""Licenças para contas antigas sem licença: python -m scripts.backfill_licenses.

Opcional e separado da migration. Por padrão só SIMULA e lista o que faria;
grava somente com --aplicar. Administradores recebem VITALICIO; os demais,
TESTE de 7 dias a partir de agora. Sem telefone e sem máquina: a máquina é
vinculada na primeira validação. Não cria registros em trial_usages (não há
máquina/telefone conhecidos), portanto não consome a trava de teste.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.base import load_models
from app.db.session import get_engine
from app.modules.licenses.models import License, LicensePlanCode, LicenseState
from app.modules.licenses.rules import plan_end
from app.modules.users.models import User, UserRole


@dataclass(frozen=True)
class BackfillItem:
    email: str
    plan: LicensePlanCode


def backfill_licenses(session: Session, *, apply: bool) -> list[BackfillItem]:
    now: datetime = datetime.now(UTC)
    transaction = session.begin()
    try:
        users = session.scalars(
            select(User).outerjoin(License, License.user_id == User.id)
            .where(License.id.is_(None)).order_by(User.created_at).with_for_update(of=User)).all()
        items: list[BackfillItem] = []
        for user in users:
            plan: LicensePlanCode = LicensePlanCode.VITALICIO if user.role == UserRole.ADMIN else LicensePlanCode.TESTE
            items.append(BackfillItem(user.email, plan))
            if apply:
                session.add(License(user_id=user.id, plan=plan, status=LicenseState.ATIVA, activated_at=now,
                                    expires_at=plan_end(plan, now), machine_id=None, phone=None, reset_count=0))
        session.flush()
        if apply:
            transaction.commit()
        else:
            transaction.rollback()
    except BaseException:
        if transaction.is_active:
            transaction.rollback()
        raise
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cria licença para usuários existentes sem licença.")
    parser.add_argument("--aplicar", action="store_true", help="Grava no banco. Sem esta opção, apenas simula.")
    args = parser.parse_args(argv)
    load_models()
    try:
        with Session(bind=get_engine(), expire_on_commit=False) as session:
            items: list[BackfillItem] = backfill_licenses(session, apply=args.aplicar)
    except (SQLAlchemyError, ValueError):
        print("Falha: verifique DATABASE_URL, a conexão e se alembic upgrade head foi aplicado.", file=sys.stderr)
        return 1
    for item in items:
        print(f"  {item.email}: {item.plan}")
    verb: str = "criada(s)" if args.aplicar else "seria(m) criada(s) — simulação, nada gravado; use --aplicar"
    print(f"{len(items)} licença(s) {verb}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
