#!/usr/bin/env python3
"""Read-only PostgreSQL schema dependency inventory for automotive cutover (#165).

Prints catalog metadata only: relations, foreign-key and view dependencies,
user triggers and their function identities.
Never scans business rows, extracts object keys, or authorizes decommission.
Requires an explicitly configured synthetic loopback PostgreSQL instance.
"""
from __future__ import annotations

import json
import os
import sys

import psycopg
from psycopg.conninfo import conninfo_to_dict

RELATION_KINDS = {"r": "table", "p": "partitioned_table", "v": "view",
                  "m": "materialized_view", "S": "sequence"}
# Relations confirmed as dependencies of the current automotive identity,
# staff authorization, vehicle-claim and OIDC flows. "shared-review" is
# deliberately not a drop-safe classification.
SHARED_RELATIONS = frozenset({
    "alembic_version",
    "app_user",
    "local_credential",
    "user_session",
    "user_session_csrf_rotation",
    "auth_login_throttle",
    "role",
    "user_role",
    "customer",
    "account_customer_link",
    "oidc_identity",
})


def connection_parameters(raw: str, synthetic_flag: str) -> tuple[str, str] | None:
    """Require explicit loopback host, forbid service/hostaddr overrides."""
    if synthetic_flag != "1" or not raw:
        return None
    normalized = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        options = conninfo_to_dict(normalized)
    except (ValueError, psycopg.Error):
        return None
    if (options.get("host") not in {"localhost", "127.0.0.1", "::1"}
            or "hostaddr" in options or "service" in options
            or options.get("dbname") in {None, "", "postgres", "template0", "template1"}):
        return None
    resolved = "::1" if options["host"] == "::1" else "127.0.0.1"
    return normalized, resolved


def classify(name: str) -> str:
    if name.startswith("auto_"):
        return "automotive"
    if name in SHARED_RELATIONS:
        return "shared-review"
    return "legacy-or-unclassified"


def inspect_schema(conn: psycopg.Connection) -> dict[str, object]:
    """Metadata only; caller must establish a read-only transaction."""
    relations = [
        {"schema": schema, "name": name, "kind": RELATION_KINDS[kind],
         "classification": classify(name)}
        for schema, name, kind in conn.execute("""
            SELECT n.nspname, c.relname, c.relkind
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')
              AND NOT c.relispartition
            ORDER BY n.nspname, c.relname
        """).fetchall()
    ]
    foreign_keys = [
        {"name": name, "from": src, "to": dst,
         "fromSchema": src_schema, "toSchema": dst_schema,
         "crossBoundary": src_schema != dst_schema or classify(src) != classify(dst)}
        for name, src_schema, src, dst_schema, dst in conn.execute("""
            SELECT co.conname, ns.nspname, src.relname, nd.nspname, dst.relname
            FROM pg_constraint co
            JOIN pg_class src ON src.oid=co.conrelid
            JOIN pg_class dst ON dst.oid=co.confrelid
            JOIN pg_namespace ns ON ns.oid=src.relnamespace
            JOIN pg_namespace nd ON nd.oid=dst.relnamespace
            WHERE co.contype='f' AND (ns.nspname='public' OR nd.nspname='public')
            ORDER BY ns.nspname, src.relname, nd.nspname, dst.relname, co.conname
        """).fetchall()
    ]
    triggers = [
        {"table": table, "name": name, "functionSchema": fn_schema,
         "function": fn_name}
        for table, name, fn_schema, fn_name in conn.execute("""
            SELECT c.relname, t.tgname, pn.nspname, p.proname
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_proc p ON p.oid=t.tgfoid
            JOIN pg_namespace pn ON pn.oid=p.pronamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal
            ORDER BY c.relname, t.tgname
        """).fetchall()
    ]
    # Deduplicate column dependencies; never deparse SQL definitions.
    view_dependencies = [
        {"viewSchema": vs, "view": view, "relationSchema": rs,
         "relation": relation,
         "crossBoundary": vs != rs or classify(view) != classify(relation)}
        for vs, view, rs, relation in conn.execute("""
            SELECT DISTINCT vn.nspname, v.relname, rn.nspname, r.relname
            FROM pg_rewrite rw
            JOIN pg_class v ON v.oid=rw.ev_class AND v.relkind IN ('v','m')
            JOIN pg_namespace vn ON vn.oid=v.relnamespace
            JOIN pg_depend dep ON dep.classid='pg_rewrite'::regclass
                AND dep.objid=rw.oid AND dep.refclassid='pg_class'::regclass
            JOIN pg_class r ON r.oid=dep.refobjid
            JOIN pg_namespace rn ON rn.oid=r.relnamespace
            WHERE v.oid<>r.oid
              AND (vn.nspname='public' OR rn.nspname='public')
            ORDER BY vn.nspname, v.relname, rn.nspname, r.relname
        """).fetchall()
    ]
    return {
        "scope": "public-schema-metadata-only",
        "status": "INVENTORY_ONLY_NOT_DECOMMISSION_APPROVAL",
        "releaseAllowed": False, "dropAllowed": False,
        "relations": relations, "foreignKeys": foreign_keys, "triggers": triggers,
        "viewDependencies": view_dependencies,
        "summary": {
            "relations": len(relations), "foreignKeys": len(foreign_keys),
            "crossBoundaryForeignKeys": sum(item["crossBoundary"] for item in foreign_keys),
            "triggers": len(triggers),
            "viewDependencies": len(view_dependencies),
            "crossBoundaryViewDependencies": sum(
                item["crossBoundary"] for item in view_dependencies),
        },
    }


def main() -> int:
    values = connection_parameters(
        os.environ.get("ROBOport_DATABASE_URL", ""),
        os.environ.get("ROBOport_SYNTHETIC_AUTO_API", ""),
    )
    if values is None:
        print("BLOCKED: synthetic explicit loopback PostgreSQL required", file=sys.stderr)
        return 2
    dsn, hostaddr = values
    try:
        with psycopg.connect(dsn, hostaddr=hostaddr, connect_timeout=5) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            result = inspect_schema(conn)
    except psycopg.Error:
        # Never log DSN, DB errors, or connection credentials.
        print("BLOCKED: PostgreSQL schema inventory unavailable", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
