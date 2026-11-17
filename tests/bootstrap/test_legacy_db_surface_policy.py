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
