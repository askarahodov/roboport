#!/usr/bin/env python3
"""Inventory of synthetic auto receipts/proofs/audit. Strictly read-only.

From project root (with installed API dependencies):
  PYTHONPATH=apps/api python scripts/inspect_auto_evidence.py
Requires ROBOport_SYNTHETIC_AUTO_API=1 and loopback PostgreSQL.
Never prints identifiers, hashes, proof secrets, connection strings, or rows.
"""
import argparse
import json
import os

import psycopg
from psycopg.conninfo import conninfo_to_dict

from roboport_api.auto_evidence_inventory import evidence_inventory


def local_dev_dsn(raw: str) -> bool:
    """Reject remote, Unix socket, service files and explicit hostaddr."""
    try:
        info = conninfo_to_dict(raw.replace("postgresql+psycopg://", "postgresql://", 1))
    except (ValueError, psycopg.Error):
        return False
    return (info.get("host") in ("localhost", "127.0.0.1", "::1")
            and "hostaddr" not in info and "service" not in info)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args(argv)
    dsn = os.getenv("ROBOport_DATABASE_URL", "")
    if os.getenv("ROBOport_SYNTHETIC_AUTO_API") != "1" or not local_dev_dsn(dsn):
        parser.error("synthetic dev and explicit loopback PostgreSQL required")
    parsed = conninfo_to_dict(dsn.replace("postgresql+psycopg://", "postgresql://", 1))
    # Hostaddr explicitly overrides environment PGHOSTADDR. No remote fallback.
    loopback = "::1" if parsed["host"] == "::1" else "127.0.0.1"
    with psycopg.connect(
        dsn.replace("postgresql+psycopg://", "postgresql://", 1),
        hostaddr=loopback, connect_timeout=5,
    ) as conn:
        # PostgreSQL itself enforces read-only, not just code convention.
        conn.execute("SET TRANSACTION READ ONLY")
        inventory = evidence_inventory(conn)
    print(json.dumps({
        "mode": "read-only",
        "retentionDecision": "BLOCKED_PENDING_LEGAL_AND_RELEASE_GATES",
        "evidence": inventory,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
