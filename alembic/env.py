"""Ambiente Alembic: schema controlado exclusivamente por migrations."""

from logging.config import fileConfig

from sqlalchemy import MetaData, create_engine, pool
from sqlalchemy.engine import URL, Engine

from alembic import context
from app.core.config import Settings
from app.db.base import Base, load_models
from app.db.session import get_database_url

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

load_models()
target_metadata: MetaData = Base.metadata


def run_migrations_offline() -> None:
    """Gera SQL sem conexão; não coloca a URL no arquivo INI."""
    url: URL = get_database_url(Settings())
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Executa migrations usando uma conexão PostgreSQL sem pool persistente."""
    engine: Engine = create_engine(
        get_database_url(Settings()),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
        hide_parameters=True,
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
