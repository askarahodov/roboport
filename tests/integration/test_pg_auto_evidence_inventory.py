"""PG17: complete receipt/claim/audit inventory, no row data and no writes."""
import json
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_evidence_inventory import EVIDENCE_TABLES, evidence_inventory
from roboport_api.auto_vehicle_claim import (
    issue_auto_proof, reissue_auto_proof, redeem_auto_proof, review_auto_claim,
)
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def test_counts_track_receipt_and_audit_without_exposing_secret_or_identity():
    with psycopg.connect(DSN) as conn:
        before = evidence_inventory(conn)
    data = _scenario()
    with psycopg.connect(DSN) as conn:
        claim, original = issue_auto_proof(
            conn, data["tokens"][data["intake"]], data["link"],
        )
    with psycopg.connect(DSN) as conn:
        _, replacement = reissue_auto_proof(
            conn, data["tokens"][data["intake"]], data["link"],
        )
    with psycopg.connect(DSN) as conn:
        assert redeem_auto_proof(
            conn, data["tokens"][data["client_a"]], claim, replacement,
            idempotency_key=uuid4().hex,
        ) == "PENDING_REVIEW"
    with psycopg.connect(DSN) as conn:
        assert review_auto_claim(
            conn, data["tokens"][data["manager"]], claim, True,
            idempotency_key=uuid4().hex,
        ) == "VERIFIED"
    with psycopg.connect(DSN) as conn:
        after = evidence_inventory(conn)

    assert set(after) == set(EVIDENCE_TABLES)
    increments = {
        "auto_create_receipt": 0,
        "auto_claim_retry_receipt": 2,
        "auto_vehicle_claim": 1,
        "auto_vehicle_claim_reissue": 1,
        "auto_vehicle_claim_audit": 4,
        "auto_vehicle_link_intake_audit": 0,
        "auto_vehicle_staff_assignment_audit": 0,
    }
    for name, increment in increments.items():
        assert after[name]["total"] == before[name]["total"] + increment
        assert after[name]["last24h"] >= increment
        assert sum(after[name][bucket] for bucket in (
            "last24h", "days1to7", "days7to30", "older30d",
        )) == after[name]["total"]
    rendered = json.dumps(after)
    for secret in (original, replacement, str(claim),
                   str(data["client_a"]), str(data["intake"])):
        assert secret not in rendered
    assert all(set(counts) == {
        "total", "last24h", "days1to7", "days7to30", "older30d",
    } for counts in after.values())
    assert all(type(n) is int for counts in after.values() for n in counts.values())


def test_read_only_postgres_transaction_rejects_delete_even_without_rows():
    with psycopg.connect(DSN) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        report = evidence_inventory(conn)
        assert set(report) == set(EVIDENCE_TABLES)
        # Even a zero-row deletion must be forbidden by PostgreSQL itself.
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM auto_claim_retry_receipt WHERE false")
