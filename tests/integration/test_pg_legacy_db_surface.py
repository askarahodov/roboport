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
        # The prior metadata projection still exposes neither secrets nor
        # passwords. Catalog identities may legitimately contain such words
        # in object names; the fixture below tests exclusion of actual values.
        serialized = json.dumps({k:report[k] for k in
            ("relations","foreignKeys","triggers","viewDependencies")})
        assert "secret" not in serialized.lower()
        assert "password" not in serialized.lower()
        for key in ("catalogObjects","catalogDependencies","inheritance","sequenceOwnership"):
            assert report["summary"][key]==len(report[key])
        assert report["catalogCoverage"]["completeDependencyGraph"] is False
        assert all(edge["source"] in report["catalogObjects"] and
                   edge["target"] in report["catalogObjects"]
                   for edge in report["catalogDependencies"])
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


def test_catalog_graph_covers_routines_defaults_policies_partitions_and_sequence_ownership():
    from uuid import uuid4
    from psycopg import sql

    suffix=uuid4().hex[:10]
    schema="cutover_graph_"+suffix
    table="auto_catalog_probe_"+suffix
    child="partition_probe_"+suffix
    fn="inventory_overload_"+suffix
    sequence="legacy_sequence_"+suffix
    policy="rls_probe_"+suffix
    marker="SYNTHETIC_BODY_OR_POLICY_VALUE_"+suffix
    public_table=sql.Identifier("public",table)
    with psycopg.connect(DSN) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("CREATE TABLE {} (id bigint GENERATED ALWAYS AS IDENTITY, part int NOT NULL, note text DEFAULT {}) PARTITION BY RANGE(part)").format(public_table,sql.Literal(marker)))
            conn.execute(sql.SQL("CREATE TABLE {} PARTITION OF {} FOR VALUES FROM (0) TO (10)").format(
                sql.Identifier(schema,child),public_table))
            conn.execute(sql.SQL("CREATE SEQUENCE {}").format(sql.Identifier("public",sequence)))
            conn.execute(sql.SQL("ALTER SEQUENCE {} OWNED BY {}.note").format(
                sql.Identifier("public",sequence),public_table))
            # SQL-standard body records dependencies in pg_depend; string
            # bodies do not. Both overloaded routine identities must survive.
            conn.execute(sql.SQL("CREATE FUNCTION {}(int) RETURNS bigint LANGUAGE SQL BEGIN ATOMIC SELECT count(*) FROM {}; END").format(
                sql.Identifier(schema,fn),public_table))
            conn.execute(sql.SQL("CREATE FUNCTION {}(text) RETURNS text LANGUAGE SQL AS {}").format(
                sql.Identifier("public",fn),sql.Literal("SELECT "+repr(marker)+"::text")))
            conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(public_table))
            conn.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(public_table))
            conn.execute(sql.SQL("CREATE POLICY {} ON {} AS RESTRICTIVE FOR SELECT TO PUBLIC USING (note <> {})").format(
                sql.Identifier(policy),public_table,sql.Literal(marker)))
            report=inspect_schema(conn)
            assert report==inspect_schema(conn)
            assert report["releaseAllowed"] is False and report["dropAllowed"] is False
            objects=report["catalogObjects"]
            assert {"schema":"public","kind":"function","name":fn,"ownerRelation":None,
                    "argumentTypes":["pg_catalog.text"]} in objects
            assert {"schema":schema,"kind":"function","name":fn,"ownerRelation":None,
                    "argumentTypes":["pg_catalog.int4"]} in objects
            edges=report["catalogDependencies"]
            assert any(e["source"]["name"]==fn and e["source"]["schema"]==schema
                and e["target"]["name"]==table and e["target"]["schema"]=="public"
                and e["crossSchema"] for e in edges)
            assert any(e["source"]["kind"]=="column_default"
                and e["source"]["ownerRelation"]==table and e["target"]["name"]==table
                for e in edges)
            assert any(e["source"]["kind"]=="policy" and e["source"]["name"]==policy
                and e["target"]["name"]==table for e in edges)
            assert any(x["parent"]==table and x["child"]==child
                and x["childSchema"]==schema and x["isPartition"] and x["crossSchema"]
                for x in report["inheritance"])
            owned=[x for x in report["sequenceOwnership"] if x["table"]==table]
            assert {x["dependencyType"] for x in owned}=={"a","i"}
            assert any(x["sequence"]==sequence and x["columnOrdinal"]==3 for x in owned)
            rls=next(x for x in report["rowSecurity"] if x["table"]==table)
            assert rls["enabled"] and rls["forced"]
            assert rls["policies"]==[{"name":policy,"command":"r","permissive":False,
                                      "roleCount":1,"includesPublic":True}]
            assert marker not in json.dumps(report)
            assert report["catalogCoverage"]["completeDependencyGraph"] is False
            assert "dynamic-sql-and-routine-bodies" in report["catalogCoverage"]["exclusions"]
        finally:
            conn.rollback()
