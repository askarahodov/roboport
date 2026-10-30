"""Consolidated fail-closed automobile release preflight (#119).

Only a technical readiness report. A successful preflight does not authorize
production data, payments, or deployment; independent approvals remain required.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from scripts.check_auto_release_ci import _json_api, validate_run
from scripts.check_auto_release_evidence import validate_evidence

BLOCKING_ISSUES = (113, 114, 165, 172, 121, 123, 124)


def evaluate(evidence: object, run: object, jobs: object,
             issues: dict[int, object], sha: str, run_id: int) -> dict:
    """Stable, non-sensitive reasons; no evidence contents or tokens logged."""
    failed = list(validate_evidence(evidence))
    failed.extend("CI_" + reason for reason in validate_run(run, jobs, sha, run_id))
    for number in BLOCKING_ISSUES:
        issue = issues.get(number)
        if type(issue) is not dict or issue.get("state") != "closed":
            failed.append(f"ISSUE_{number}_NOT_CLOSED")
    return {"verdict": "BLOCKED", "technicalChecksPassed": not failed,
            "reasons": sorted(set(failed)),
            "productionApproved": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)
    # Validate inputs before touching evidence files or the network.
    from scripts.check_auto_release_ci import SHA_RE
    if not SHA_RE.fullmatch(args.sha) or args.run_id <= 0:
        print(json.dumps({"verdict": "BLOCKED", "reason": "INVALID_INPUT"}))
        return 2
    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(json.dumps({"verdict": "BLOCKED", "reason": "EVIDENCE_UNAVAILABLE"}))
        return 2
    # Do not use GitHub credentials in log messages or exception traces.
    token = os.getenv("GITHUB_TOKEN", "")
    try:
        run = _json_api(f"/actions/runs/{args.run_id}", token)
        jobs = _json_api(f"/actions/runs/{args.run_id}/jobs?per_page=100", token)
        issues = {number: _json_api(f"/issues/{number}", token)
                  for number in BLOCKING_ISSUES}
    except Exception:
        print(json.dumps({"verdict": "BLOCKED", "reason": "REMOTE_VERIFICATION_FAILED"}))
        return 2
    result = evaluate(evidence, run, jobs, issues, args.sha, args.run_id)
    print(json.dumps(result, sort_keys=True))
    # Even technical PASS is not an authorized production release.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
