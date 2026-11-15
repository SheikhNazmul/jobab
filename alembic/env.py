import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.db.all_models  # noqa: F401
from alembic import context
from app.core.config import get_settings
from app.db.base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
config.set_main_option(
    "sqlalchemy.url",
    get_settings().DATABASE_URL.render_as_string(hide_password=False).replace("%", "%%"),
)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata autogenerate compares the database against.
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Teach autogenerate how to write the pgvector column type."""
    if type_ == "type" and obj.__class__.__module__.startswith("pgvector."):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        dim = getattr(obj, "dim", None)
        return f"pgvector.sqlalchemy.Vector({dim})" if dim else "pgvector.sqlalchemy.Vector()"
    return False


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: print SQL instead of running it."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    schema = get_settings().DB_SCHEMA
    # The Docker database creates the schema in scripts/init-db.sql. Creating
    # it here too makes migrations work on any other Postgres as well.
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    connection.execute(text(f'SET search_path TO "{schema}", public'))
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an Engine and associate a connection with the context."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
