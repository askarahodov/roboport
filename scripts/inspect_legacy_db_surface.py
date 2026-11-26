#!/usr/bin/env python3
"""Read-only PostgreSQL schema dependency inventory for automotive cutover (#165).

Prints catalog metadata only: relations, foreign-key and view dependencies,
catalog objects/edges, partition and sequence links, routine identities and RLS flags.
No SQL bodies, defaults, policy predicates, role names or business values.
Never scans business rows, extracts object keys, or authorizes decommission.
Requires an explicitly configured synthetic loopback PostgreSQL instance.
"""
from __future__ import annotations

import argparse
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


# A bounded allowlist of catalog classes. Unlike pg_describe_object(),
# explicit identity fields never deparse stored expressions or routine bodies.
# OIDs are used only within the query; the report is stable across dumps when
# the same names/signatures and column identities are restored.
_CATALOG_GRAPH_SQL = """
    WITH namespaces AS (
        SELECT oid,nspname FROM pg_namespace
        WHERE nspname NOT IN ('pg_catalog','information_schema')
          AND nspname !~ '^pg_'
    ), objects AS (
        SELECT 'pg_class'::regclass::oid AS classid,c.oid,n.nspname AS schema,
               CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'partitioned_table'
                 WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view'
                 WHEN 'S' THEN 'sequence' WHEN 'i' THEN 'index'
                 WHEN 'I' THEN 'partitioned_index' WHEN 'f' THEN 'foreign_table'
                 WHEN 'c' THEN 'composite_relation' END AS kind,
               c.relname::text AS name,NULL::text AS owner_name,
               ARRAY[]::text[] AS arguments
        FROM pg_class c JOIN namespaces n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p','v','m','S','i','I','f','c')
        UNION ALL
        SELECT 'pg_proc'::regclass::oid,p.oid,n.nspname,
               CASE p.prokind WHEN 'p' THEN 'procedure' WHEN 'a' THEN 'aggregate'
                 WHEN 'w' THEN 'window_function' ELSE 'function' END,
               p.proname::text,NULL::text,
               ARRAY(SELECT format('%I.%I',tn.nspname,t.typname)
                     FROM unnest(p.proargtypes::oid[]) WITH ORDINALITY a(type_oid,pos)
                     JOIN pg_type t ON t.oid=a.type_oid
                     JOIN pg_namespace tn ON tn.oid=t.typnamespace ORDER BY a.pos)
        FROM pg_proc p JOIN namespaces n ON n.oid=p.pronamespace
        UNION ALL
        SELECT 'pg_type'::regclass::oid,t.oid,n.nspname,
               'type',t.typname::text,NULL::text,ARRAY[]::text[]
        FROM pg_type t JOIN namespaces n ON n.oid=t.typnamespace
        UNION ALL
        SELECT 'pg_constraint'::regclass::oid,k.oid,n.nspname,
               'constraint',k.conname::text,COALESCE(c.relname,t.typname)::text,ARRAY[]::text[]
        FROM pg_constraint k JOIN namespaces n ON n.oid=k.connamespace
        LEFT JOIN pg_class c ON c.oid=k.conrelid
        LEFT JOIN pg_type t ON t.oid=k.contypid
        UNION ALL
        SELECT 'pg_attrdef'::regclass::oid,d.oid,n.nspname,
               'column_default',a.attname::text,c.relname::text,ARRAY[]::text[]
        FROM pg_attrdef d JOIN pg_class c ON c.oid=d.adrelid
        JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum
        JOIN namespaces n ON n.oid=c.relnamespace
        UNION ALL
        SELECT 'pg_rewrite'::regclass::oid,w.oid,n.nspname,
               'rewrite_rule',w.rulename::text,c.relname::text,ARRAY[]::text[]
        FROM pg_rewrite w JOIN pg_class c ON c.oid=w.ev_class
        JOIN namespaces n ON n.oid=c.relnamespace
        UNION ALL
        SELECT 'pg_trigger'::regclass::oid,t.oid,n.nspname,
               'trigger',t.tgname::text,c.relname::text,ARRAY[]::text[]
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN namespaces n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal
        UNION ALL
        SELECT 'pg_policy'::regclass::oid,p.oid,n.nspname,
               'policy',p.polname::text,c.relname::text,ARRAY[]::text[]
        FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        JOIN namespaces n ON n.oid=c.relnamespace
    ), identities AS (
        SELECT classid,oid,schema,kind,
               jsonb_build_object('schema',schema,'kind',kind,'name',name,
                   'ownerRelation',owner_name,'argumentTypes',arguments) AS identity
        FROM objects
    ), edges AS (
        SELECT DISTINCT src.identity AS source,dst.identity AS target,
               d.objsubid AS source_column,d.refobjsubid AS target_column,
               sa.attname::text AS source_column_name,ta.attname::text AS target_column_name,
               d.deptype::text AS dependency_type,
               src.schema<>dst.schema AS cross_schema
        FROM pg_depend d
        JOIN identities src ON (src.classid,src.oid)=(d.classid,d.objid)
        JOIN identities dst ON (dst.classid,dst.oid)=(d.refclassid,d.refobjid)
        LEFT JOIN pg_attribute sa ON d.classid='pg_class'::regclass
          AND sa.attrelid=d.objid AND sa.attnum=d.objsubid AND d.objsubid>0
        LEFT JOIN pg_attribute ta ON d.refclassid='pg_class'::regclass
          AND ta.attrelid=d.refobjid AND ta.attnum=d.refobjsubid AND d.refobjsubid>0
        WHERE src.schema='public' OR dst.schema='public'
    ), selected AS (
        SELECT identity FROM identities WHERE schema='public'
        UNION SELECT source FROM edges
        UNION SELECT target FROM edges
    )
    SELECT jsonb_build_object(
        'objects',COALESCE((SELECT jsonb_agg(identity ORDER BY identity)
                           FROM selected),'[]'::jsonb),
        'edges',COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'source',source,'target',target,'sourceColumn',source_column,
            'targetColumn',target_column,'sourceColumnName',source_column_name,
            'targetColumnName',target_column_name,'dependencyType',dependency_type,
            'crossSchema',cross_schema)
            ORDER BY source,target,source_column,target_column,dependency_type)
            FROM edges),'[]'::jsonb))
"""


def _catalog_inventory(conn: psycopg.Connection) -> dict[str, object]:
    graph=conn.execute(_CATALOG_GRAPH_SQL).fetchone()[0]
    # pg_inherits includes attached partitions and inheritance, including
    # partitioned indexes; do not infer retention from attachment.
    inheritance=[
        {"parentSchema":ps,"parent":pn,"parentKind":pk,
         "childSchema":cs,"child":cn,"childKind":ck,
         "isPartition":partition,"crossSchema":ps!=cs}
        for ps,pn,pk,cs,cn,ck,partition in conn.execute("""
            SELECT np.nspname,p.relname,p.relkind::text,
                   nc.nspname,c.relname,c.relkind::text,c.relispartition
            FROM pg_inherits i JOIN pg_class p ON p.oid=i.inhparent
            JOIN pg_class c ON c.oid=i.inhrelid
            JOIN pg_namespace np ON np.oid=p.relnamespace
            JOIN pg_namespace nc ON nc.oid=c.relnamespace
            WHERE np.nspname='public' OR nc.nspname='public'
            ORDER BY np.nspname,p.relname,nc.nspname,c.relname
        """).fetchall()
    ]
    # Sequence ownership (a=owned by, i=identity) is distinct from use in a
    # default expression, which is represented by the catalog graph.
    sequences=[
        {"sequenceSchema":ss,"sequence":sn,"tableSchema":ts,"table":tn,
         "columnOrdinal":col,"columnName":column_name,
         "dependencyType":dep,"crossSchema":ss!=ts}
        for ss,sn,ts,tn,col,column_name,dep in conn.execute("""
            SELECT ns.nspname,s.relname,nt.nspname,t.relname,
                   d.refobjsubid,a.attname,d.deptype::text
            FROM pg_depend d
            JOIN pg_class s ON d.classid='pg_class'::regclass AND s.oid=d.objid
              AND s.relkind='S'
            JOIN pg_class t ON d.refclassid='pg_class'::regclass AND t.oid=d.refobjid
              AND t.relkind IN ('r','p')
            JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
            JOIN pg_namespace ns ON ns.oid=s.relnamespace
            JOIN pg_namespace nt ON nt.oid=t.relnamespace
            WHERE d.deptype IN ('a','i') AND d.refobjsubid>0
              AND (ns.nspname='public' OR nt.nspname='public')
            ORDER BY ns.nspname,s.relname,nt.nspname,t.relname,d.refobjsubid
        """).fetchall()
    ]
    # RLS is an inventory signal, not an effective privilege computation.
    # Never include polqual/polwithcheck, pg_get_expr(), rolname or grants.
    rls=[
        {"schema":schema,"table":table,"enabled":enabled,"forced":forced,
         "policies":policies}
        for schema,table,enabled,forced,policies in conn.execute("""
            SELECT n.nspname,c.relname,c.relrowsecurity,c.relforcerowsecurity,
                COALESCE(jsonb_agg(jsonb_build_object('name',p.polname,
                    'command',p.polcmd::text,'permissive',p.polpermissive,
                    'roleCount',cardinality(p.polroles),
                    'includesPublic',0::oid=ANY(p.polroles))
                    ORDER BY p.polname) FILTER(WHERE p.oid IS NOT NULL),'[]'::jsonb)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_policy p ON p.polrelid=c.oid
            WHERE n.nspname='public' AND c.relkind IN ('r','p')
              AND (c.relrowsecurity OR c.relforcerowsecurity OR p.oid IS NOT NULL)
            GROUP BY n.nspname,c.relname,c.relrowsecurity,c.relforcerowsecurity
            ORDER BY n.nspname,c.relname
        """).fetchall()
    ]
    return {
        "catalogObjects":graph["objects"],"catalogDependencies":graph["edges"],
        "inheritance":inheritance,"sequenceOwnership":sequences,"rowSecurity":rls,
        "catalogCoverage":{
            "scope":"public-and-direct-cross-schema-edges",
            "classes":["pg_class","pg_proc","pg_type","pg_constraint",
                       "pg_attrdef","pg_rewrite","pg_trigger","pg_policy"],
            "systemSchemasExcluded":True,
            "completeDependencyGraph":False,
            "exclusions":["dynamic-sql-and-routine-bodies","external-clients",
                          "roles-and-effective-grants","extension-and-other-catalog-classes",
                          "data-and-object-storage","transitive-external-only-edges"],
        },
    }


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
    catalog=_catalog_inventory(conn)
    return {
        "scope": "public-schema-metadata-only",
        "status": "INVENTORY_ONLY_NOT_DECOMMISSION_APPROVAL",
        "releaseAllowed": False, "dropAllowed": False,
        "relations": relations, "foreignKeys": foreign_keys, "triggers": triggers,
        "viewDependencies": view_dependencies,
        **catalog,
        "summary": {
            "catalogObjects":len(catalog["catalogObjects"]),
            "catalogDependencies":len(catalog["catalogDependencies"]),
            "crossSchemaCatalogDependencies":sum(
                edge["crossSchema"] for edge in catalog["catalogDependencies"]),
            "inheritance":len(catalog["inheritance"]),
            "sequenceOwnership":len(catalog["sequenceOwnership"]),
            "rowSecurityTables":len(catalog["rowSecurity"]),
            "relations": len(relations), "foreignKeys": len(foreign_keys),
            "crossBoundaryForeignKeys": sum(item["crossBoundary"] for item in foreign_keys),
            "triggers": len(triggers),
            "viewDependencies": len(view_dependencies),
            "crossBoundaryViewDependencies": sum(
                item["crossBoundary"] for item in view_dependencies),
        },
    }


_RESTORE_SECTIONS=("relations","foreignKeys","triggers","viewDependencies",
                   "catalogObjects","catalogDependencies","inheritance",
                   "sequenceOwnership","rowSecurity","catalogCoverage")


def restore_differences(source: dict, clone: dict) -> list[str]:
    """Сравнить именованные метаданные, а не локальные OID/attnum."""
    def normalized(section, value):
        if section not in ("catalogDependencies","sequenceOwnership"):
            return value
        result=[]
        for row in value:
            item=dict(row)
            if section=="catalogDependencies":
                for ordinal,name in (("sourceColumn","sourceColumnName"),("targetColumn","targetColumnName")):
                    # Не скрывать неразрешённый атрибут при нормализации.
                    if item[ordinal]==0 or item[name] is not None:
                        item.pop(ordinal)
            elif item["columnName"] is not None:
                item.pop("columnOrdinal")
            result.append(item)
        return sorted(result,key=lambda x:json.dumps(x,sort_keys=True))
    return [section for section in _RESTORE_SECTIONS
            if section not in source or section not in clone or
            normalized(section,source[section])!=normalized(section,clone[section])]


def _restore_parameters(raw, synthetic_flag, ci_flag):
    """Только фиксированная пара disposable PG17 баз CI; не выбирать произвольный clone."""
    values=connection_parameters(raw,synthetic_flag)
    if ci_flag!="1" or values is None:
        return None
    dsn,hostaddr=values
    options=conninfo_to_dict(dsn)
    if (set(options)!={"host","port","user","password","dbname"}
        or options.get("host")!="127.0.0.1" or options.get("port")!="5432"
        or options.get("user")!="roboport" or options.get("dbname")!="roboport"):
        return None
    return dsn,hostaddr


def _read_report(dsn,hostaddr,database=None):
    kwargs={"hostaddr":hostaddr,"connect_timeout":5,"options":"-c search_path=pg_catalog"}
    if database is not None:
        kwargs["dbname"]=database
    with psycopg.connect(dsn,**kwargs) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if database is not None:
            actual,version=conn.execute("""
                SELECT current_database(),current_setting('server_version_num')::integer
            """).fetchone()
            if actual!=database or not 170000<=version<180000:
                raise ValueError("unexpected restore database")
        return inspect_schema(conn)


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-restored",action="store_true")
    args=parser.parse_args(argv)
    if args.compare_restored:
        values=_restore_parameters(os.environ.get("ROBOport_DATABASE_URL",""),
            os.environ.get("ROBOport_SYNTHETIC_AUTO_API",""),
            os.environ.get("ROBOport_CATALOG_RESTORE_CI",""))
        if values is None:
            print("BLOCKED: disposable PG17 restore comparison required",file=sys.stderr)
            return 2
        try:
            source=_read_report(*values,"roboport")
            clone=_read_report(*values,"roboport_restored")
            differences=restore_differences(source,clone)
        except (psycopg.Error,ValueError,KeyError,TypeError):
            print("BLOCKED: PostgreSQL restore catalog comparison unavailable",file=sys.stderr)
            return 2
        if differences:
            # Только фиксированные имена разделов, никаких объектов/DDL/DSN.
            print("BLOCKED: restored catalog mismatch: "+", ".join(differences),file=sys.stderr)
            return 2
        print("CATALOG RESTORE PASS: metadata objects, dependencies, ownership and RLS; releaseAllowed=false")
        return 0
    values = connection_parameters(
        os.environ.get("ROBOport_DATABASE_URL", ""),
        os.environ.get("ROBOport_SYNTHETIC_AUTO_API", ""),
    )
    if values is None:
        print("BLOCKED: synthetic explicit loopback PostgreSQL required", file=sys.stderr)
        return 2
    dsn, hostaddr = values
    try:
        result = _read_report(dsn,hostaddr)
    except psycopg.Error:
        # Never log DSN, DB errors, or connection credentials.
        print("BLOCKED: PostgreSQL schema inventory unavailable", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
