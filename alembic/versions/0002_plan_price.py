"""Preço BRL configurado pelo servidor; planos existentes sem preço não cobram."""
import sqlalchemy as sa

from alembic import op

revision: str = "0002_plan_price"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("price", sa.Numeric(18, 2), nullable=True))
    op.create_check_constraint(op.f("ck_plans_positive_price"), "plans", "price IS NULL OR price > 0")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_plans_positive_price"), "plans", type_="check")
    op.drop_column("plans", "price")
