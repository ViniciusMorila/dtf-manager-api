"""Contratos dos models e validação read-only do schema migrado."""

from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import DateTime, Enum, Numeric, UniqueConstraint, Uuid, inspect
from sqlalchemy.orm import configure_mappers

from app.core.config import Settings
from app.db.base import Base, load_models
from app.db.session import get_engine
from app.modules.auth.models import RefreshToken
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.plans.models import Plan, PlanCode
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.users.models import User

load_models()


def test_columns_types_and_nullability() -> None:
    assert set(Base.metadata.tables) == {"users", "plans", "subscriptions", "payments", "refresh_tokens"}
    for table in Base.metadata.tables.values():
        assert list(table.primary_key.columns.keys()) == ["id"]
        assert isinstance(table.c.id.type, Uuid)
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone
        assert not table.c.created_at.nullable

    assert "password" not in User.__table__.c
    assert "token" not in RefreshToken.__table__.c
    assert not User.__table__.c.password_hash.nullable
    assert not RefreshToken.__table__.c.token_hash.nullable
    assert Payment.__table__.c.metadata.nullable
    assert Payment.__mapper__.attrs.payment_metadata.columns[0].name == "metadata"
    assert isinstance(Payment.__table__.c.amount.type, Numeric)
    assert Payment.__table__.c.amount.type.python_type is Decimal
    assert Payment.__table__.c.amount.type.scale == 2
    for name in ("starts_at", "expires_at", "activated_at", "cancelled_at"):
        assert Subscription.__table__.c[name].nullable


def test_unique_constraints_and_foreign_key_indexes() -> None:
    def unique_columns(table_name: str) -> set[tuple[str, ...]]:
        return {
            tuple(constraint.columns.keys())
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, UniqueConstraint)
        }

    assert {("cpf",), ("email",)} <= unique_columns("users")
    assert ("code",) in unique_columns("plans")
    assert ("token_hash",) in unique_columns("refresh_tokens")
    assert ("provider", "provider_payment_id") in unique_columns("payments")
    assert Payment.__table__.c.provider_payment_id.nullable
    for table in Base.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            assert any(next(iter(index.columns)) is foreign_key.parent for index in table.indexes)


def test_enum_values_and_pending_defaults() -> None:
    assert set(SubscriptionStatus) == {"PENDING", "ACTIVE", "EXPIRED", "CANCELLED", "SUSPENDED"}
    assert set(PaymentStatus) == {"PENDING", "APPROVED", "REJECTED", "CANCELLED", "REFUNDED"}
    assert set(PlanCode) == {"MONTHLY", "SEMIANNUAL", "ANNUAL", "LIFETIME"}
    for model in (Subscription, Payment):
        column = model.__table__.c.status
        assert isinstance(column.type, Enum)
        assert column.type.native_enum
        assert column.default.arg == "PENDING"
        assert str(column.server_default.arg) == "PENDING"


def test_relationships() -> None:
    configure_mappers()
    user: User = User()
    plan: Plan = Plan()
    subscription: Subscription = Subscription(user=user, plan=plan)
    payment: Payment = Payment(user=user, subscription=subscription, amount=Decimal("29.90"))
    token: RefreshToken = RefreshToken(user=user)
    assert user.subscriptions == [subscription]
    assert plan.subscriptions == [subscription]
    assert user.payments == subscription.payments == [payment]
    assert user.refresh_tokens == [token]


def test_migrated_postgresql_schema() -> None:
    """Inspeciona o banco configurado sem executar migrations nem inserir dados."""
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: tabelas reais ainda não verificadas")
    with get_engine().connect() as connection:
        context: MigrationContext = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True},
        )
        assert context.get_current_revision() == "0002_plan_price"
        assert compare_metadata(context, Base.metadata) == []
        inspector = inspect(connection)
        for table in Base.metadata.tables.values():
            assert table.name in inspector.get_table_names()
            assert inspector.get_pk_constraint(table.name)["constrained_columns"] == ["id"]
            actual_unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name)}
            for constraint in table.constraints:
                if isinstance(constraint, UniqueConstraint):
                    assert tuple(constraint.columns.keys()) in actual_unique
        assert connection.exec_driver_sql("SHOW TIME ZONE").scalar_one() == "UTC"
