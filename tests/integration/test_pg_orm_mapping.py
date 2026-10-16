"""Declarative schema maps deployed tables; never uses create_all on live DB."""
from sqlalchemy import create_engine, inspect
from roboport_api.models import Base
from test_pg_baseline import DSN


def test_orm_core_columns_exist_in_real_alembic_schema():
    engine=create_engine(DSN)
    try:
        with engine.connect() as conn:
            inspector=inspect(conn)
            for table in Base.metadata.sorted_tables:
                reflected={row['name'] for row in inspector.get_columns(table.name)}
                assert set(table.columns.keys())<=reflected, (table.name,set(table.columns.keys())-reflected)
                assert {col.name for col in table.primary_key.columns}<=reflected
            ticket_foreign_keys={fk['referred_table'] for fk in inspector.get_foreign_keys('service_ticket')}
            assert {'customer','robot_device'}<=ticket_foreign_keys
            assert any(set(u['column_names'])=={'ticket_id','version'} for u in inspector.get_unique_constraints('estimate'))
    finally:
        engine.dispose()
