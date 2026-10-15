"""Disposable PostgreSQL-only migrations; never connect to production automatically."""
import os
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
url = os.environ.get("ROBOport_DATABASE_URL")
if not url:
    raise RuntimeError("ROBOport_DATABASE_URL required; migrations never use fallback DB")
config.set_main_option("sqlalchemy.url", url)

if context.is_offline_mode():
    context.configure(url=url, target_metadata=None, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
