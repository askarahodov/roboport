"""Real PG17 synthetic legacy attachment lineage and restore digest proof."""
import psycopg
import pytest

from scripts.auto_legacy_attachment_restore_probe import (
    digest, graph, seed_graph,
)
from test_pg_baseline import DSN


def test_legacy_attachment_metadata_lineage_and_replay_digest():
    with psycopg.connect(DSN) as conn:
        ids = seed_graph(conn)
        snapshot = graph(conn, ids)
        assert snapshot == graph(conn, ids)
        assert digest(snapshot) == digest(graph(conn, ids))
        assert len(snapshot["attachments"]) == 2
        assert len(snapshot["replays"]) == 1
        # A real distinct record must change the checksum; never rely on row counts.
        changed = {**snapshot, "attachments": [row[:] for row in snapshot["attachments"]]}
        changed["attachments"][0][3] = "image/png"
        assert digest(snapshot) != digest(changed)
        # Legacy ownership of the ticket and metadata is immutable.
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE attachment SET ticket_id=%s WHERE id=%s",
                             (ids["customer"], ids["ready"]))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE attachment SET quarantine_state='CLEAR' WHERE id=%s",
                             (ids["failed"],))
