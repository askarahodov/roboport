"""Не принимаем пустые legacy SQL за готовые Alembic миграции (#17)."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / "db" / "legacy_drafts"
EXPECTED = [
    "001_initial.sql",
    "002_integrity_hardening.sql",
    "003_estimate_proof_and_review.sql",
    "004_customer_portal.sql",
    "005_customer_access_integrity.sql",
    "006_idempotency_ledger.sql",
]


def test_legacy_sql_is_incomplete_and_not_migration_source():
    files = sorted(path.name for path in LEGACY.glob("*.sql"))
    assert files == EXPECTED
    assert all((LEGACY / name).stat().st_size == 0 for name in EXPECTED), (
        "Legacy SQL changed: revisit schema reconciliation before #11"
    )


def test_ticket_fsm_and_openapi_state_mismatch_is_explicitly_documented():
    contract = yaml.safe_load((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))
    state = contract["components"]["schemas"]["Ticket"]["properties"]["state"]
    audit = (ROOT / "docs/data/SCHEMA_RECONCILIATION.md").read_text(encoding="utf-8")
    assert state["type"] == "string"
    assert "enum" not in state, "Ticket state enum was added: reconcile FSM and SQL"
    assert "Критический GAP" in audit and "Ticket.state" in audit
