"""Fail-closed production release evidence gate for automobile transition (#119).

This script never approves itself: independent evidence and explicit approval are
required. CI runs unit tests only; no production deployment is performed.
"""
from __future__ import annotations

import json
import sys
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
            or gate["approved"] is not True or type(gate["evidence_url"]) is not str
            or not gate["evidence_url"].startswith("https://")
            or len(gate["evidence_url"]) > 1000):
            errors.append(key)
    if type(raw["approved_by"]) is not str or not raw["approved_by"].strip():
        errors.append("INDEPENDENT_APPROVER")
    if type(raw["approved_at"]) is not str or not raw["approved_at"].strip():
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
