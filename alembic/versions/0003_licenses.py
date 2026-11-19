"""Licenças do aplicativo, chaves de ativação, trava de teste, resets e perfil de usuário.

Revision ID: 0003_licenses
Revises: 0002_plan_price

Somente adições: nenhuma linha existente é apagada ou alterada, exceto tornar
users.cpf opcional (o cadastro de licenças não exige CPF).
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_licenses"
down_revision: str | Sequence[str] | None = "0002_plan_price"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_role = postgresql.ENUM("USER", "ADMIN", name="user_role", create_type=False)
license_plan = postgresql.ENUM("TESTE", "MENSAL", "SEMESTRAL", "ANUAL", "VITALICIO",
                               name="license_plan", create_type=False)
license_status = postgresql.ENUM("ATIVA", "EXPIRADA", "BLOQUEADA", name="license_status", create_type=False)
activation_key_status = postgresql.ENUM("DISPONIVEL", "USADA", "CANCELADA",
                                        name="activation_key_status", create_type=False)
ENUMS: tuple[postgresql.ENUM, ...] = (user_role, license_plan, license_status, activation_key_status)
LICENSE_TABLES: tuple[str, ...] = ("licenses", "activation_keys", "trial_usages", "license_resets")
# ALTER TABLE users/refresh_tokens pede lock exclusivo; sem limite, a espera por ele
# bloquearia todas as consultas a essas tabelas. Com 5 s, a migration falha e pode ser repetida.
LOCK_TIMEOUT: str = "5s"
DOWNGRADE_CONFIRMATION_VARIABLE: str = "CONFIRMAR_DOWNGRADE_LICENCAS"
DOWNGRADE_CONFIRMATION_VALUE: str = "apagar-dados-de-licencas"


class DowngradeRefused(RuntimeError):
    """O downgrade apagaria licenças, chaves vendidas e histórico sem confirmação."""


def ensure_downgrade_allowed(bind: sa.engine.Connection, confirmation: str | None) -> dict[str, int]:
    """Permite o downgrade com as tabelas vazias; com dados, exige a confirmação explícita."""
    counts: dict[str, int] = {
        table: bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() for table in LICENSE_TABLES
    }
    if any(counts.values()) and confirmation != DOWNGRADE_CONFIRMATION_VALUE:
        summary: str = ", ".join(f"{table}={count}" for table, count in counts.items())
        raise DowngradeRefused(
            f"Downgrade recusado: ele apagaria definitivamente dados de licença ({summary}). "
            f"Faça backup (pg_dump) e repita com {DOWNGRADE_CONFIRMATION_VARIABLE}="
            f"{DOWNGRADE_CONFIRMATION_VALUE}.")
    return counts


def set_lock_timeout() -> None:
    """Vale só para a transação da migration (SET LOCAL)."""
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")


def upgrade() -> None:
    set_lock_timeout()
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind, checkfirst=False)

    op.add_column("users", sa.Column("role", user_role, server_default="USER", nullable=False))
    op.alter_column("users", "cpf", existing_type=sa.String(length=11), nullable=True)
    op.add_column("refresh_tokens", sa.Column("machine_id", sa.String(length=128), nullable=True))

    op.create_table(
        "licenses",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plan", license_plan, nullable=False),
        sa.Column("status", license_status, server_default="ATIVA", nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("machine_id", sa.String(length=128), nullable=True),
        sa.Column("phone", sa.String(length=11), nullable=True),
        sa.Column("reset_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(plan = 'VITALICIO' AND expires_at IS NULL) OR (plan <> 'VITALICIO' AND expires_at IS NOT NULL)",
            name=op.f("ck_licenses_lifetime_expiration"),
        ),
        sa.CheckConstraint("reset_count >= 0", name=op.f("ck_licenses_nonnegative_reset_count")),
        sa.CheckConstraint("phone IS NULL OR phone ~ '^[0-9]{10,11}$'", name=op.f("ck_licenses_phone_digits")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_licenses_user_id_users"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_licenses")),
        sa.UniqueConstraint("user_id", name=op.f("uq_licenses_user_id")),
    )
    op.create_index(op.f("ix_licenses_plan"), "licenses", ["plan"], unique=False)
    op.create_index(op.f("ix_licenses_status"), "licenses", ["status"], unique=False)
    op.create_index(op.f("ix_licenses_expires_at"), "licenses", ["expires_at"], unique=False)
    op.create_index(op.f("ix_licenses_phone"), "licenses", ["phone"], unique=False)
    op.create_index("ux_licenses_active_machine", "licenses", ["machine_id"], unique=True,
                    postgresql_where=sa.text("status = 'ATIVA' AND machine_id IS NOT NULL"))

    op.create_table(
        "activation_keys",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_suffix", sa.String(length=4), nullable=False),
        sa.Column("plan", license_plan, nullable=False),
        sa.Column("status", activation_key_status, server_default="DISPONIVEL", nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("customer_contact", sa.String(length=200), nullable=False),
        sa.Column("license_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"],
                                name=op.f("fk_activation_keys_license_id_licenses"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"],
                                name=op.f("fk_activation_keys_created_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activation_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_activation_keys_key_hash")),
    )
    op.create_index(op.f("ix_activation_keys_plan"), "activation_keys", ["plan"], unique=False)
    op.create_index(op.f("ix_activation_keys_status"), "activation_keys", ["status"], unique=False)
    op.create_index(op.f("ix_activation_keys_license_id"), "activation_keys", ["license_id"], unique=False)
    op.create_index(op.f("ix_activation_keys_created_by"), "activation_keys", ["created_by"], unique=False)

    op.create_table(
        "trial_usages",
        sa.Column("machine_id", sa.String(length=128), nullable=False),
        sa.Column("phone", sa.String(length=11), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_trial_usages_user_id_users"),
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trial_usages")),
        sa.UniqueConstraint("machine_id", name=op.f("uq_trial_usages_machine_id")),
        sa.UniqueConstraint("phone", name=op.f("uq_trial_usages_phone")),
    )
    op.create_index(op.f("ix_trial_usages_user_id"), "trial_usages", ["user_id"], unique=False)

    op.create_table(
        "license_resets",
        sa.Column("license_id", sa.Uuid(), nullable=False),
        sa.Column("previous_machine_id", sa.String(length=128), nullable=True),
        sa.Column("performed_by", sa.Uuid(), nullable=True),
        sa.Column("forced", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"],
                                name=op.f("fk_license_resets_license_id_licenses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["performed_by"], ["users.id"],
                                name=op.f("fk_license_resets_performed_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_license_resets")),
    )
    op.create_index("ix_license_resets_license_created", "license_resets", ["license_id", "created_at"],
                    unique=False)
    op.create_index(op.f("ix_license_resets_performed_by"), "license_resets", ["performed_by"], unique=False)


def downgrade() -> None:
    """Remove as estruturas de licença. users.cpf só volta a NOT NULL se nenhuma conta estiver sem CPF.

    Apaga licenças, chaves (inclusive vendidas e não usadas) e histórico de resets.
    Com dados presentes, exige CONFIRMAR_DOWNGRADE_LICENCAS=apagar-dados-de-licencas.
    """
    set_lock_timeout()
    if op.get_context().as_sql:
        op.execute("-- ATENCAO: este SQL apaga definitivamente licencas, chaves e historico de resets. "
                   "Faca backup (pg_dump) antes de aplicar.")
    else:
        ensure_downgrade_allowed(op.get_bind(), os.environ.get(DOWNGRADE_CONFIRMATION_VARIABLE))
    op.drop_table("license_resets")
    op.drop_table("trial_usages")
    op.drop_table("activation_keys")
    op.drop_table("licenses")
    op.drop_column("refresh_tokens", "machine_id")
    op.drop_column("users", "role")
    bind = op.get_bind()
    # Em modo offline (--sql) não há como contar linhas; o SQL gerado mantém a coluna opcional.
    if not op.get_context().as_sql:
        missing: int = bind.execute(sa.text("SELECT count(*) FROM users WHERE cpf IS NULL")).scalar_one()
        if missing == 0:
            op.alter_column("users", "cpf", existing_type=sa.String(length=11), nullable=False)
        else:
            # Restaurar NOT NULL exigiria apagar ou inventar CPFs; preserva-se a coluna opcional.
            print(f"AVISO: {missing} usuário(s) sem CPF; users.cpf permanece opcional após o downgrade.")
    for enum in reversed(ENUMS):
        enum.drop(bind, checkfirst=False)
