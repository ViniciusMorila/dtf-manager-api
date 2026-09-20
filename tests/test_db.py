"""Testes da infraestrutura, sem criar tabelas ou usar um banco substituto."""

import asyncio
import secrets
from collections.abc import Generator
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.schema import CreateTable

from alembic import command
from app.core.config import Settings
from app.db.base import Base, load_models
from app.db.session import (
    SessionDependency,
    get_database_url,
    get_engine,
    get_session,
    get_session_factory,
)


@pytest.fixture
def configured_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[Engine, None, None]:
    """URL fictícia: criar o engine não abre conexão."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/dtf_test")
    get_engine.cache_clear()
    engine: Engine = get_engine()
    try:
        yield engine
    finally:
        engine.dispose()
        get_engine.cache_clear()


def test_engine_and_session_factory(configured_engine: Engine) -> None:
    assert get_engine() is configured_engine
    assert configured_engine.dialect.name == "postgresql"
    assert configured_engine.dialect.driver == "psycopg"
    factory: sessionmaker[Session] = get_session_factory(configured_engine)
    with factory() as session:
        assert session.get_bind() is configured_engine
        assert session.autoflush is False
        assert session.expire_on_commit is False


@pytest.mark.parametrize("value", [None, "invalid", "sqlite://", "postgresql+psycopg2://localhost/db", "postgresql+psycopg://localhost"])
def test_invalid_database_url(value: str | None) -> None:
    settings: Settings = Settings.model_construct(
        database_url=SecretStr(value) if value is not None else None,
    )
    with pytest.raises(ValueError, match="DATABASE_URL"):
        get_database_url(settings)


@pytest.mark.parametrize("driver", ["postgresql", "postgresql+psycopg"])
def test_database_url_preserves_components(driver: str) -> None:
    original: URL = URL.create(driver, username="test_user",
        password=secrets.token_urlsafe(32) + "@:/%#", host="::1", port=5433,
        database="dtf_test", query={"sslmode": "require", "application_name": "dtf"})
    raw: str = original.render_as_string(hide_password=False)
    settings: Settings = Settings.model_construct(database_url=SecretStr(raw))
    normalized: URL = get_database_url(settings)
    assert normalized == make_url(raw).set(drivername="postgresql+psycopg")
    assert settings.database_url.get_secret_value() == raw
    assert original.password not in repr(normalized)


@pytest.mark.parametrize("fail", [False, True])
def test_session_cleanup(fail: bool) -> None:
    session: MagicMock = MagicMock(spec=Session)
    session.__enter__.return_value = session
    factory: MagicMock = MagicMock(return_value=session)
    dependency: Generator[Session, None, None] = get_session(factory)
    assert next(dependency) is session
    if fail:
        with pytest.raises(RuntimeError, match="test failure"):
            dependency.throw(RuntimeError("test failure"))
        session.rollback.assert_called_once()
    else:
        with pytest.raises(StopIteration):
            next(dependency)
        session.rollback.assert_not_called()
    session.__exit__.assert_called_once()
    session.commit.assert_not_called()


def test_fastapi_injects_distinct_sessions(configured_engine: Engine) -> None:
    application: FastAPI = FastAPI()
    received: list[Session] = []

    @application.get("/session-test")
    def session_route(session: SessionDependency) -> dict[str, bool]:
        received.append(session)
        return {"bound": session.get_bind() is configured_engine}

    async def request_twice() -> None:
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
            for _ in range(2):
                response: Response = await client.get("/session-test")
                assert response.status_code == 200
                assert response.json() == {"bound": True}

    asyncio.run(request_twice())
    assert received[0] is not received[1]


def test_model_discovery_and_modern_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Um model temporário comprova descoberta real; nenhum model de domínio é criado."""
    import sys

    import app.modules

    domain: Path = tmp_path / "discovery_probe"
    domain.mkdir()
    (domain / "__init__.py").write_text("", encoding="utf-8")
    (domain / "models.py").write_text(
        "from sqlalchemy.orm import Mapped, mapped_column\n"
        "from app.db.base import Base\n"
        "class Probe(Base):\n"
        "    __tablename__ = 'discovery_probe'\n"
        "    id: Mapped[int] = mapped_column(primary_key=True)\n",
        encoding="utf-8",
    )

    class TestBase(DeclarativeBase):
        pass

    monkeypatch.setattr("app.db.base.Base", TestBase)
    monkeypatch.setattr(app.modules, "__path__", [str(tmp_path)])
    try:
        load_models()
        assert "discovery_probe" in TestBase.metadata.tables
        ddl: str = str(CreateTable(TestBase.metadata.tables["discovery_probe"]).compile(dialect=postgresql.dialect()))
        assert "PRIMARY KEY" in ddl
        assert "discovery_probe" not in Base.metadata.tables
    finally:
        TestBase.registry.dispose()
        sys.modules.pop("app.modules.discovery_probe.models", None)
        sys.modules.pop("app.modules.discovery_probe", None)
        monkeypatch.delattr(app.modules, "discovery_probe", raising=False)


@pytest.mark.parametrize("driver", ["postgresql", "postgresql+psycopg"])
def test_alembic_offline_initial_revision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, driver: str) -> None:
    config: Config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == ["0002_plan_price"]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("DATABASE_URL", f"{driver}://localhost/dtf_test")
    output: StringIO = StringIO()
    config.output_buffer = output
    command.upgrade(config, "head", sql=True)
    for name in ("users", "plans", "subscriptions", "payments", "refresh_tokens"):
        assert f"CREATE TABLE {name}" in output.getvalue()
    assert "CREATE TYPE subscription_status AS ENUM" in output.getvalue()
    assert "CREATE TYPE payment_status AS ENUM" in output.getvalue()
    assert "ALTER TABLE plans ADD COLUMN price NUMERIC(18, 2)" in output.getvalue()
    assert "ck_plans_positive_price" in output.getvalue()
    output.truncate(0)
    output.seek(0)
    command.downgrade(config, "0002_plan_price:base", sql=True)
    assert "DROP COLUMN price" in output.getvalue()
    assert "DROP TYPE payment_status" in output.getvalue()
    assert "DROP TYPE subscription_status" in output.getvalue()
    assert "DROP TYPE plan_code" in output.getvalue()


def test_postgresql_connection() -> None:
    """Executa somente SELECT 1 quando há DATABASE_URL real configurada."""
    settings: Settings = Settings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL ausente: conexão real PostgreSQL não verificada")
    from sqlalchemy import create_engine

    engine: Engine = create_engine(get_database_url(settings), connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()
