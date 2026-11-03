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

# Full automotive production scope: brand/licensing, vehicle booking and repair,
# stock/supply, client consent, payments/fiscalization, tenant bootstrap,
# cutover and role-specific documentation. Closed issue state alone is never
# proof of valid evidence or an independent release approval.
BLOCKING_ISSUES = (
    110, 111, 113, 114, 115, 116, 117, 119, 121, 123, 124,
    165, 166, 170, 172,
)


def validate_current_main(before: object, after: object,
                          expected_sha: str) -> tuple[str, ...]:
    """Verify current Git refs/heads/main twice to detect stale green CI.

    Main may advance during the remote checks. A snapshot is only evidence for
    this preflight instant and MUST be checked again before any deployment.
    """
    from scripts.check_auto_release_ci import SHA_RE

    def ref_sha(value: object) -> str | None:
        if type(value) is not dict or value.get("ref") != "refs/heads/main":
            return None
        obj = value.get("object")
        if (type(obj) is not dict or obj.get("type") != "commit"
            or type(obj.get("sha")) is not str
            or SHA_RE.fullmatch(obj["sha"]) is None):
            return None
        return obj["sha"]

    first = ref_sha(before)
    last = ref_sha(after)
    if first is None or last is None:
        return ("MAIN_REF_NOT_VERIFIED",)
    if first != last:
        return ("MAIN_MOVED_DURING_PREFLIGHT",)
    if first != expected_sha:
        return ("STALE_MAIN_SHA",)
    return ()


def evaluate(evidence: object, run: object, jobs: object,
             issues: dict[int, object], sha: str, run_id: int,
             main_ref_before: object, main_ref_after: object) -> dict:
    """Stable, non-sensitive reasons; no evidence contents or tokens logged."""
    failed = list(validate_evidence(evidence))
    failed.extend("CI_" + reason for reason in validate_run(run, jobs, sha, run_id))
    failed.extend(validate_current_main(main_ref_before, main_ref_after, sha))
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
        # Ref fetched before and after all other GitHub API requests to
        # detect a new push during verification and reject stale green runs.
        ref_before = _json_api("/git/ref/heads/main", token)
        run = _json_api(f"/actions/runs/{args.run_id}", token)
        jobs = _json_api(f"/actions/runs/{args.run_id}/jobs?per_page=100", token)
        issues = {number: _json_api(f"/issues/{number}", token)
                  for number in BLOCKING_ISSUES}
        ref_after = _json_api("/git/ref/heads/main", token)
    except Exception:
        print(json.dumps({"verdict": "BLOCKED", "reason": "REMOTE_VERIFICATION_FAILED"}))
        return 2
    result = evaluate(
        evidence, run, jobs, issues, args.sha, args.run_id,
        ref_before, ref_after,
    )
    print(json.dumps(result, sort_keys=True))
    # Even technical PASS is not an authorized production release.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
