"""Fail-closed policy for read-only legacy database inventory (#165)."""
from scripts.inspect_legacy_db_surface import classify, connection_parameters


def test_only_explicit_synthetic_loopback_postgres_is_permitted():
    assert connection_parameters(
        "postgresql+psycopg://dev:secret@127.0.0.1:5432/roboport", "1"
    ) == ("postgresql://dev:secret@127.0.0.1:5432/roboport", "127.0.0.1")
    assert connection_parameters(
        "postgresql://dev:secret@localhost:5432/roboport", "1"
    )[1] == "127.0.0.1"
    assert connection_parameters(
        "postgresql://dev:secret@[::1]:5432/roboport", "1"
    )[1] == "::1"
    for dsn, flag in (
        ("postgresql://dev:secret@127.0.0.1/roboport", "0"),
        ("postgresql://dev:secret@db.internal/roboport", "1"),
        ("postgresql://dev:secret@127.0.0.1/postgres", "1"),
        ("postgresql://dev:secret@127.0.0.1/roboport?hostaddr=8.8.8.8", "1"),
        ("postgresql://dev:secret@127.0.0.1/roboport?service=prod", "1"),
        ("dbname=roboport user=dev", "1"),
        ("service=prod", "1"),
        ("", "1"),
    ):
        assert connection_parameters(dsn, flag) is None


def test_unclassified_is_not_automatically_safe_to_drop():
    assert classify("auto_vehicle") == "automotive"
    assert classify("service_ticket") == "legacy-or-unclassified"
    assert classify("app_user") == "shared-review"
    assert classify("audit_event") == "legacy-or-unclassified"


def test_automotive_identity_and_authority_relations_are_shared_review_not_legacy_drop_candidates():
    for name in (
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
    ):
        assert classify(name) == "shared-review"

    for name in (
        "robot_device",
        "service_ticket",
        "ticket_state_event",
        "acl_entry",
        "authorization_audit",
        "attachment",
        "catalog_model",
    ):
        assert classify(name) == "legacy-or-unclassified"


def test_catalog_graph_does_not_deparse_sensitive_definitions():
    from scripts.inspect_legacy_db_surface import _CATALOG_GRAPH_SQL
    query=_CATALOG_GRAPH_SQL.lower()
    for forbidden in ("prosrc","probin","pg_get_functiondef","pg_get_expr",
                      "pg_describe_object","polqual","polwithcheck","adbin",
                      "relacl","proacl","rolname"):
        assert forbidden not in query
    for expected in ("pg_depend","pg_proc","pg_type","pg_attrdef","pg_policy"):
        assert expected in query


def test_restore_gate_fixed_pair_and_column_names():
    from copy import deepcopy
    from scripts.inspect_legacy_db_surface import _RESTORE_SECTIONS,_restore_parameters,restore_differences

    dsn="postgresql+psycopg://roboport:synthetic@127.0.0.1:5432/roboport"
    assert _restore_parameters(dsn,"1","1")[1]=="127.0.0.1"
    for source,synthetic,ci in ((dsn,"0","1"),(dsn,"1","0"),
        (dsn.replace("/roboport","/other"),"1","1"),
        (dsn.replace("127.0.0.1","localhost"),"1","1"),
        (dsn+"?options=-c%20search_path%3Dpublic","1","1")):
        assert _restore_parameters(source,synthetic,ci) is None
    source={name:[] for name in _RESTORE_SECTIONS}
    source["catalogDependencies"]=[{"sourceColumn":0,"sourceColumnName":None,
        "targetColumn":4,"targetColumnName":"code","dependencyType":"n"}]
    source["sequenceOwnership"]=[{"columnOrdinal":4,"columnName":"code"}]
    clone=deepcopy(source)
    clone["catalogDependencies"][0]["targetColumn"]=2
    clone["sequenceOwnership"][0]["columnOrdinal"]=2
    assert restore_differences(source,clone)==[]
    clone["catalogDependencies"][0]["targetColumnName"]="different"
    clone["sequenceOwnership"][0]["columnName"]="different"
    assert restore_differences(source,clone)==["catalogDependencies","sequenceOwnership"]
    del clone["rowSecurity"]
    assert "rowSecurity" in restore_differences(source,clone)
