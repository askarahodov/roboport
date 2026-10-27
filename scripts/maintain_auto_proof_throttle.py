#!/usr/bin/env python3
"""Read-only by default, localhost-only manual cleanup of synthetic counters.

Usage (from repo root):
  python scripts/maintain_auto_proof_throttle.py
  python scripts/maintain_auto_proof_throttle.py --apply --confirm-synthetic
No actor IDs, database URLs, credentials or proof secrets are printed.
"""
import argparse
import json
import os

import psycopg
from psycopg.conninfo import conninfo_to_dict

from roboport_api.auto_throttle_maintenance import (
    MAX_BATCH, RETENTION_HOURS, expired_counts, prune_expired,
)


def local_dev_dsn(dsn: str) -> bool:
    """Fail closed: no remote DB, implicit service/socket or hostaddr bypass."""
    try:
        parsed = conninfo_to_dict(dsn.replace("postgresql+psycopg://", "postgresql://", 1))
        return parsed.get("host") in ("localhost", "127.0.0.1", "::1") and (
            "hostaddr" not in parsed and "service" not in parsed
        )
    except (ValueError, psycopg.Error):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="one bounded DELETE batch")
    parser.add_argument("--confirm-synthetic", action="store_true",
                        help="acknowledge synthetic/dev database only")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    if type(args.batch_size) is not int or not 1 <= args.batch_size <= MAX_BATCH:
        parser.error("batch size must be in [1,1000]")
    if args.confirm_synthetic and not args.apply:
        parser.error("--confirm-synthetic requires --apply")
    dsn = os.getenv("ROBOport_DATABASE_URL", "")
    if (os.getenv("ROBOport_SYNTHETIC_AUTO_API") != "1"
            or not local_dev_dsn(dsn)):
        parser.error("only explicit synthetic API with loopback PostgreSQL is allowed")
    if args.apply and not args.confirm_synthetic:
        parser.error("--apply requires --confirm-synthetic")
    with psycopg.connect(
        dsn.replace("postgresql+psycopg://", "postgresql://", 1),
        connect_timeout=5,
    ) as conn:
        if args.apply:
            result = prune_expired(conn, batch_size=args.batch_size)
        else:
            result = expired_counts(conn)
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "retentionHours": RETENTION_HOURS,
        "countsByOperation": result,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
