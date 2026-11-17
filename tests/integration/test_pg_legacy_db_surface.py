"""Real PostgreSQL 17 cutover metadata inventory; never reads application rows."""
import json

import psycopg
import pytest

from scripts.inspect_legacy_db_surface import inspect_schema
from test_pg_baseline import DSN


def test_current_schema_dependencies_are_repeatable_metadata_only():
    with psycopg.connect(DSN) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        report = inspect_schema(conn)
        assert report == inspect_schema(conn)
        assert report["status"] == "INVENTORY_ONLY_NOT_DECOMMISSION_APPROVAL"
        assert report["releaseAllowed"] is False
        assert report["dropAllowed"] is False
        names = {row["name"] for row in report["relations"]}
        assert {"auto_vehicle", "service_ticket", "app_user"} <= names
        assert next(x for x in report["relations"] if x["name"] == "service_ticket")[
            "classification"] == "legacy-or-unclassified"
        assert next(x for x in report["relations"] if x["name"] == "app_user")[
            "classification"] == "shared-review"
        assert report["summary"]["relations"] == len(report["relations"])
        assert report["summary"]["foreignKeys"] == len(report["foreignKeys"])
        assert report["summary"]["triggers"] == len(report["triggers"])
        assert report["summary"]["crossBoundaryForeignKeys"] == sum(
            edge["crossBoundary"] for edge in report["foreignKeys"]
        )
        assert report["foreignKeys"]
        assert report["triggers"]
        serialized = json.dumps(report)
        assert "secret" not in serialized.lower()
        assert "password" not in serialized.lower()
        # PostgreSQL, not an application convention, refuses mutations.
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TABLE public.inventory_should_not_write (id integer)")
