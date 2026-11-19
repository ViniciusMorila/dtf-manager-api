"""Cria ou promove o administrador: python -m scripts.seed_admin.

Lê ADMIN_EMAIL e ADMIN_SENHA (e ADMIN_NOME, opcional) do ambiente ou do .env.
Idempotente: conta existente é promovida a ADMIN sem trocar a senha, a menos
que --redefinir-senha seja informado. A licença fica VITALICIO, ATIVA e sem
trava de máquina. A senha nunca é exibida nem registrada.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import load_models
from app.db.session import get_engine
from app.modules.licenses.models import License, LicensePlanCode, LicenseState
from app.modules.users.models import User, UserRole
from app.shared.security.password import hash_password, validate_password_strength


@dataclass(frozen=True)
class AdminSeedResult:
    created: bool
    password_updated: bool
    license_created: bool


def seed_admin(session: Session, *, email: str, password: str, name: str,
               reset_password: bool = False) -> AdminSeedResult:
    """Executa em uma transação; falhas revertem conta e licença juntas."""
    validate_password_strength(password)
    with session.begin():
        user: User | None = session.scalar(
            select(User).where(func.lower(User.email) == email).with_for_update())
        created: bool = user is None
        password_updated: bool = False
        if user is None:
            user = User(name=name, email=email, cpf=None, password_hash=hash_password(password),
                        is_active=True, role=UserRole.ADMIN)
            session.add(user)
            session.flush()
        else:
            user.role = UserRole.ADMIN
            user.is_active = True
            if reset_password:
                user.password_hash = hash_password(password)
                password_updated = True
        license: License | None = session.scalar(
            select(License).where(License.user_id == user.id).with_for_update())
        license_created: bool = license is None
        if license is None:
            session.add(License(user_id=user.id, plan=LicensePlanCode.VITALICIO, status=LicenseState.ATIVA,
                                activated_at=datetime.now(UTC), expires_at=None, machine_id=None, phone=None,
                                reset_count=0))
        else:
            license.plan = LicensePlanCode.VITALICIO
            license.expires_at = None
            license.status = LicenseState.ATIVA
            license.machine_id = None
        session.flush()
    return AdminSeedResult(created, password_updated, license_created)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cria ou promove o administrador a partir de ADMIN_EMAIL/ADMIN_SENHA.")
    parser.add_argument("--redefinir-senha", action="store_true",
                        help="Se a conta já existir, substitui a senha pela de ADMIN_SENHA.")
    args = parser.parse_args(argv)
    try:
        settings: Settings = Settings()
    except ValidationError:
        print("Falha: configuração inválida (verifique as variáveis de ambiente).", file=sys.stderr)
        return 1
    if not settings.admin_email or settings.admin_senha is None:
        print("Falha: defina ADMIN_EMAIL e ADMIN_SENHA no ambiente.", file=sys.stderr)
        return 1
    try:
        email: str = str(TypeAdapter(EmailStr).validate_python(settings.admin_email)).lower()
    except ValidationError:
        print("Falha: ADMIN_EMAIL inválido.", file=sys.stderr)
        return 1
    load_models()
    try:
        with Session(bind=get_engine(), expire_on_commit=False) as session:
            result: AdminSeedResult = seed_admin(
                session, email=email, password=settings.admin_senha.get_secret_value(),
                name=settings.admin_nome.strip(), reset_password=args.redefinir_senha)
    except ValueError:
        print("Falha: ADMIN_SENHA fraca (mínimo 8 caracteres, com letra e número) ou DATABASE_URL ausente.",
              file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Falha no seed do administrador: verifique a conexão e se alembic upgrade head foi aplicado.",
              file=sys.stderr)
        return 1
    print(f"Administrador {'criado' if result.created else 'promovido/atualizado'}; "
          f"senha {'redefinida' if result.password_updated else 'mantida' if not result.created else 'definida'}; "
          f"licença VITALICIO {'criada' if result.license_created else 'atualizada'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
