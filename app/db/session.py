"""Engine PostgreSQL e sessões síncronas por requisição, sem DDL automático."""

from collections.abc import Generator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def get_database_url(settings: Settings) -> URL:
    """Exige uma URL PostgreSQL com psycopg 3 sem expor credenciais em erros."""
    if settings.database_url is None:
        raise ValueError("DATABASE_URL é obrigatória para acessar PostgreSQL ou executar Alembic.")
    try:
        url: URL = make_url(settings.database_url.get_secret_value())
    except (ArgumentError, ValueError):
        raise ValueError("DATABASE_URL inválida.") from None
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg" or not url.database:
        raise ValueError("DATABASE_URL deve usar postgresql+psycopg e informar o banco.")
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Cria um pool por processo; conexões são abertas apenas quando usadas."""
    return create_engine(
        get_database_url(Settings()),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
        hide_parameters=True,
    )


def get_session_factory(
    engine: Annotated[Engine, Depends(get_engine)],
) -> sessionmaker[Session]:
    """Fornece a fábrica vinculada ao engine da aplicação."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session(
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> Generator[Session, None, None]:
    """Fecha a sessão sempre; commits são responsabilidade explícita do serviço."""
    with factory() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


SessionDependency = Annotated[Session, Depends(get_session)]
