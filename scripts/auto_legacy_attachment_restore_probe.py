"""Disposable PG17 legacy attachment metadata seed + dump/restore reconciliation (#165).

No MinIO access, real uploads, production credentials or data deletion.
Manifest is held outside the dump. Exit is fail-closed on mismatch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg

from scripts.auto_restore_lifecycle_probe import _dsn

FIELDS = ("actor", "customer", "ticket", "ready", "failed")
VERSION = 1


def digest(data: object) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def rows(conn: psycopg.Connection, query: str, params: tuple) -> list[list[str]]:
    return [[str(value) if value is not None else "NULL" for value in record]
            for record in conn.execute(query, params).fetchall()]


def graph(conn: psycopg.Connection, ids: dict[str, str]) -> dict[str, object]:
    actor, customer, ticket, ready, failed = (UUID(ids[key]) for key in FIELDS)
    data = {
        "owner": rows(conn, """
            SELECT t.id,t.customer_id,c.id,a.id,a.created_by
            FROM service_ticket t
            JOIN customer c ON c.id=t.customer_id
            JOIN attachment a ON a.ticket_id=t.id
            WHERE t.id=%s AND c.id=%s AND a.id=%s
        """, (ticket, customer, ready)),
        "attachments": rows(conn, """
            SELECT id,ticket_id,object_key,mime,byte_size,sha256,
                   upload_state,quarantine_state,created_by,created_at,updated_at
            FROM attachment WHERE id IN (%s,%s)
            ORDER BY id
        """, (ready, failed)),
        "replays": rows(conn, """
            SELECT actor_id,ticket_id,key_hash,payload_hash,attachment_id
            FROM attachment_upload_dedupe
            WHERE attachment_id IN (%s,%s) ORDER BY attachment_id
        """, (ready, failed)),
    }
    attachments = data["attachments"]
    if (len(data["owner"]) != 1 or len(attachments) != 2
            or len(data["replays"]) != 1):
        raise ValueError("missing legacy attachment lineage")
    by_id = {row[0]: row for row in attachments}
    if set(by_id) != {str(ready), str(failed)}:
        raise ValueError("incomplete legacy attachment snapshot")
    good, bad = by_id[str(ready)], by_id[str(failed)]
    if (data["owner"][0] != [str(ticket), str(customer), str(customer),
                              str(ready), str(actor)]
            or any(row[1] != str(ticket) or row[8] != str(actor)
                   for row in attachments)
            or good[6:8] != ["READY", "CLEAR"]
            or bad[6:8] != ["FAILED", "QUARANTINED"]
            or data["replays"][0][0:2] != [str(actor), str(ticket)]
            or data["replays"][0][4] != str(ready)
            or any(len(row[2]) != 32 or len(row[5]) != 64 for row in attachments)):
        raise ValueError("legacy attachment states/ownership/replay mismatch")
    return data


def seed_graph(conn: psycopg.Connection) -> dict[str, str]:
    ids = {field: str(uuid4()) for field in FIELDS}
    actor, customer, ticket, ready, failed = (UUID(ids[key]) for key in FIELDS)
    conn.execute(
        "INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)",
        (actor, "restore-attachment-" + actor.hex),
    )
    conn.execute("INSERT INTO customer(id,label) VALUES (%s,'synthetic-restore')",
                 (customer,))
    conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",
                 (ticket, customer))
    for attachment_id, suffix in ((ready, b"ready"), (failed, b"failed")):
        conn.execute("""
            INSERT INTO attachment(id,ticket_id,object_key,mime,byte_size,sha256,created_by)
            VALUES (%s,%s,%s,'text/plain',%s,%s,%s)
        """, (attachment_id, ticket, uuid4().hex, len(suffix),
              hashlib.sha256(suffix).hexdigest(), actor))
    conn.execute("UPDATE attachment SET upload_state='READY' WHERE id=%s", (ready,))
    conn.execute("""
        UPDATE attachment SET upload_state='FAILED',quarantine_state='QUARANTINED'
        WHERE id=%s
    """, (failed,))
    conn.execute("""
        INSERT INTO attachment_upload_dedupe
            (actor_id,ticket_id,key_hash,payload_hash,attachment_id)
        VALUES (%s,%s,%s,%s,%s)
    """, (actor, ticket, "a" * 64, "b" * 64, ready))
    return ids


def load_manifest(path: Path) -> dict:
    item = json.loads(path.read_text(encoding="utf-8"))
    if (type(item) is not dict or set(item) != {"version", "ids", "sha256"}
            or item["version"] != VERSION or type(item["ids"]) is not dict
            or set(item["ids"]) != set(FIELDS)
            or any(type(item["ids"][key]) is not str for key in FIELDS)
            or type(item["sha256"]) is not str or len(item["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in item["sha256"])):
        raise ValueError("invalid restore manifest")
    for field in FIELDS:
        UUID(item["ids"][field])
    return item


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "verify-source", "verify-clone"))
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        database = "roboport_restored" if args.action == "verify-clone" else "roboport"
        dsn = _dsn(database)  # strict loopback CI-only DSN and ambient-PG checks
        if args.action == "seed":
            if args.manifest.exists():
                raise ValueError("manifest already exists")
            with psycopg.connect(dsn) as conn:
                ids = seed_graph(conn)
                proof = digest(graph(conn, ids))
            # Write manifest only after successful DB commit, never print IDs/keys.
            with args.manifest.open("x", encoding="utf-8") as handle:
                json.dump({"version": VERSION, "ids": ids, "sha256": proof}, handle)
        else:
            manifest = load_manifest(args.manifest)
            with psycopg.connect(dsn) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                actual = digest(graph(conn, manifest["ids"]))
            if actual != manifest["sha256"]:
                raise ValueError("legacy metadata restore mismatch")
    except (ValueError, OSError, psycopg.Error, json.JSONDecodeError):
        print("BLOCKED: legacy attachment metadata reconciliation failed")
        return 1
    print("PASS: synthetic legacy attachment metadata " + args.action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
