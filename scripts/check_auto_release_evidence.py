"""Fail-closed production release evidence gate for automobile transition (#119).

This script never approves itself: independent evidence and explicit approval are
required. CI runs unit tests only; no production deployment is performed.
"""
from __future__ import annotations

import json
import sys
import ipaddress
from datetime import datetime, timezone
from urllib.parse import urlsplit
from pathlib import Path

REQUIRED_GATES = (
    "auto_e2e",
    "tenant_admin_acl",
    "legacy_cutover_restore",
    "privacy_consent",
    "parts_licensing",
    "payments_compliance",
    "monitoring_rollback",
    "ci_main",
)

def _safe_evidence_url(raw: object) -> bool:
    """Public HTTPS reference syntax only; not proof that the link is genuine."""
    if type(raw) is not str or not 1 <= len(raw) <= 1000:
        return False
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in raw):
        return False
    try:
        url = urlsplit(raw)
        host = url.hostname
        if (url.scheme != "https" or not host or url.username is not None
                or url.password is not None or url.port not in (None, 443)
                or not url.path or url.path == "/"):
            return False
        if host.lower() in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return "." in host and all(part for part in host.split("."))
    except ValueError:
        return False


def _valid_approval_time(raw: object) -> bool:
    if type(raw) is not str or not raw.endswith("Z"):
        return False
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return stamp.tzinfo is not None and stamp.utcoffset() == timezone.utc.utcoffset(stamp)


def validate_evidence(raw: object) -> tuple[str, ...]:
    if type(raw) is not dict or set(raw) != {"gates", "approved_by", "approved_at"}:
        return ("INVALID_EVIDENCE_SCHEMA",)
    gates = raw["gates"]
    if type(gates) is not dict or set(gates) != set(REQUIRED_GATES):
        return ("MISSING_OR_EXTRA_GATES",)
    errors = []
    for key in REQUIRED_GATES:
        gate = gates[key]
        if (type(gate) is not dict or set(gate) != {"approved", "evidence_url"}
            or gate["approved"] is not True or not _safe_evidence_url(gate["evidence_url"])):
            errors.append(key)
    if type(raw["approved_by"]) is not str or not raw["approved_by"].strip():
        errors.append("INDEPENDENT_APPROVER")
    if not _valid_approval_time(raw["approved_at"]):
        errors.append("APPROVAL_TIMESTAMP")
    return tuple(errors)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("BLOCKED: explicit release evidence JSON required", file=sys.stderr)
        return 2
    try:
        content = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("BLOCKED: evidence unavailable or invalid", file=sys.stderr)
        return 2
    failed = validate_evidence(content)
    if failed:
        print("BLOCKED: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("Evidence gate structure passed. Independent verification still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
