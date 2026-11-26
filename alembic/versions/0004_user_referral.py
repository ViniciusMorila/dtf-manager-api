"""Indicação (origem do cliente) no cadastro de usuários.

Revision ID: 0004_user_referral
Revises: 0003_licenses

Somente adiciona users.indicacao (texto até 100, NULL por padrão). users.created_at
já existe desde a 0001, então nenhuma coluna de data é criada nem preenchida.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_user_referral"
down_revision: str | Sequence[str] | None = "0003_licenses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TABLE pede lock exclusivo; sem limite, a espera bloquearia as consultas a users.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("users", sa.Column("indicacao", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("users", "indicacao")
