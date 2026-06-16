"""
alembic/env.py — Sabueso de Licitaciones
Configuración de Alembic en tiempo de ejecución.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Importamos nuestro proyecto (funciona gracias a pip install -e .)
import config as proyecto_config
from src.models import Base

alembic_config = context.config

# Sobreescribimos la URL con la que nuestro proyecto usa realmente
alembic_config.set_main_option("sqlalchemy.url", proyecto_config.DATABASE_URL)

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# Le decimos a Alembic cuáles son nuestras tablas
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Modo offline: genera SQL sin tocar la BD."""
    url = alembic_config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Imprescindible para SQLite (su ALTER TABLE es muy limitado)
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo online: aplica las migraciones directamente contra la BD."""
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()