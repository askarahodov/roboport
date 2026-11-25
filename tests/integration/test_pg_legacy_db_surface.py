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


def test_inventory_keeps_cross_schema_edges_and_trigger_functions_without_data():
    from uuid import uuid4
    from psycopg import sql

    suffix=uuid4().hex[:10]
    schema="cutover_probe_"+suffix
    table="auto_inventory_probe_"+suffix
    view="probe_view_"+suffix
    # Unique disposable fixture objects, in one transaction; rollback
    # removes them even if assertions fail. No other object's DDL changes.
    with psycopg.connect(DSN) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("CREATE TABLE {} (id int PRIMARY KEY, note text)").format(
                sql.Identifier("public",table)))
            conn.execute(sql.SQL("CREATE TABLE {} (id int REFERENCES {}(id), marker text)").format(
                sql.Identifier(schema,"legacy_probe"),sql.Identifier("public",table)))
            conn.execute(sql.SQL("CREATE VIEW {} AS SELECT id,note FROM {}").format(
                sql.Identifier(schema,view),sql.Identifier("public",table)))
            conn.execute(sql.SQL("CREATE VIEW {} AS SELECT id,marker FROM {}").format(
                sql.Identifier("public",view),sql.Identifier(schema,"legacy_probe")))
            conn.execute(sql.SQL("""CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql
                AS $$ BEGIN RETURN NEW; END $$""").format(sql.Identifier(schema,"probe_trigger")))
            conn.execute(sql.SQL("CREATE TRIGGER probe BEFORE INSERT ON {} FOR EACH ROW EXECUTE FUNCTION {}()").format(
                sql.Identifier("public",table),sql.Identifier(schema,"probe_trigger")))
            conn.execute(sql.SQL("INSERT INTO {} VALUES(1,%s)").format(
                sql.Identifier("public",table)),("SYNTHETIC_ROW_NOT_IN_REPORT",))
            report=inspect_schema(conn)
            assert report==inspect_schema(conn)
            assert report["dropAllowed"] is False and report["releaseAllowed"] is False
            edges=[x for x in report["foreignKeys"] if x["to"]==table]
            assert len(edges)==1
            assert edges[0]["fromSchema"]==schema and edges[0]["toSchema"]=="public"
            assert edges[0]["crossBoundary"] is True
            views=[x for x in report["viewDependencies"] if x["view"]==view]
            assert len(views)==2
            assert {(x["viewSchema"],x["relationSchema"]) for x in views}=={
                (schema,"public"),("public",schema)}
            assert all(x["crossBoundary"] for x in views)
            assert report["summary"]["viewDependencies"]==len(report["viewDependencies"])
            assert report["summary"]["crossBoundaryViewDependencies"]==sum(
                x["crossBoundary"] for x in report["viewDependencies"])
            assert {"table":table,"name":"probe","functionSchema":schema,
                    "function":"probe_trigger"} in report["triggers"]
            assert "SYNTHETIC_ROW_NOT_IN_REPORT" not in json.dumps(report)
        finally:
            conn.rollback()
