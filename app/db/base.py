"""Base e descoberta dos models usados pelo Alembic."""

from importlib import import_module
from pkgutil import walk_packages

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

import app.modules


class Base(DeclarativeBase):
    """Models futuros herdam desta base e usam Mapped e mapped_column."""

    metadata: MetaData = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


def load_models() -> None:
    """Importa models.py e pacotes models dos domínios para registrar metadados."""
    for module in walk_packages(app.modules.__path__, prefix="app.modules."):
        if "models" in module.name.split(".")[2:]:
            import_module(module.name)
