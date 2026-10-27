"""No raw auto-evidence exfiltration or accidental production inventory."""
import pytest

from scripts.inspect_auto_evidence import local_dev_dsn, main
from roboport_api.auto_evidence_inventory import EVIDENCE_TABLES


@pytest.mark.parametrize("dsn", [
    "postgresql://dev:secret@localhost:5432/roboport",
    "postgresql+psycopg://dev:secret@127.0.0.1:5432/roboport",
    "postgresql://dev:secret@[::1]:5432/roboport",
])
def test_explicit_local_dsn(dsn):
    assert local_dev_dsn(dsn)


@pytest.mark.parametrize("dsn", [
    "", "dbname=roboport", "postgresql://dev@db.prod/roboport",
    "postgresql://dev@10.1.2.3/roboport",
    "postgresql://dev@localhost/roboport?hostaddr=203.0.113.11",
    "postgresql://dev@localhost/roboport?service=prod",
])
def test_remote_and_implicit_db_refused(dsn):
    assert not local_dev_dsn(dsn)


def test_requires_synthetic_optin_without_leaking_connection(monkeypatch, capsys):
    monkeypatch.setenv("ROBOport_DATABASE_URL",
                       "postgresql://dev:sensitive-test-password@localhost/roboport")
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API", raising=False)
    with pytest.raises(SystemExit) as e:
        main([])
    assert e.value.code == 2
    assert "sensitive-test-password" not in capsys.readouterr().err


def test_refuses_mutating_flags_before_touching_db(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", "postgresql://dev@localhost/roboport")
    for arg in ("--apply", "--delete", "--archive", "--table", "--confirm-synthetic"):
        with pytest.raises(SystemExit) as exc:
            main([arg])
        assert exc.value.code == 2


def test_inventory_allowlist_includes_complete_receipt_claim_audit_chain():
    assert set(EVIDENCE_TABLES) == {
        "auto_create_receipt", "auto_claim_retry_receipt",
        "auto_vehicle_claim", "auto_vehicle_claim_reissue",
        "auto_vehicle_claim_audit", "auto_vehicle_link_intake_audit",
        "auto_vehicle_staff_assignment_audit",
    }
