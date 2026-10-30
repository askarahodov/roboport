"""Verify actual GitHub CI run provenance for a specified release commit (#119).

This is a technical CI gate only. It cannot issue legal/security approval.
Network failures, missing jobs, skipped checks and incorrect refs fail closed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REQUIRED_JOBS = frozenset({"postgres", "api", "web", "browser-e2e", "windows-cmd"})
REPO = "askarahodov/roboport"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def validate_run(run: object, jobs: object, expected_sha: str,
                 expected_run_id: int | None = None) -> tuple[str, ...]:
    if type(expected_sha) is not str or not SHA_RE.fullmatch(expected_sha):
        return ("INVALID_SHA",)
    if type(run) is not dict or type(jobs) is not dict:
        return ("INVALID_RESPONSE",)
    problems = []
    if (run.get("head_sha") != expected_sha or run.get("head_branch") != "main"
            or run.get("event") != "push" or run.get("status") != "completed"
            or run.get("conclusion") != "success"):
        problems.append("RUN_NOT_VERIFIED")
    if (expected_run_id is not None and
            (type(expected_run_id) is not int or run.get("id") != expected_run_id)):
        problems.append("RUN_ID_MISMATCH")
    repository = run.get("repository")
    if type(repository) is not dict or repository.get("full_name") != REPO:
        problems.append("REPOSITORY_MISMATCH")
    workflow = run.get("path")
    if (type(workflow) is not str or not
            workflow.split("@", 1)[0].endswith("/.github/workflows/ci.yml")):
        problems.append("UNEXPECTED_WORKFLOW")
    pages = jobs.get("total_count")
    data = jobs.get("jobs")
    if type(pages) is not int or type(data) is not list or pages != len(data):
        problems.append("INCOMPLETE_JOB_LIST")
        return tuple(problems)
    seen = {}
    for job in data:
        if type(job) is not dict or type(job.get("name")) is not str:
            problems.append("INVALID_JOB")
            continue
        name = job["name"]
        if name in seen:
            problems.append("DUPLICATE_JOB")
        seen[name] = job
    if not REQUIRED_JOBS.issubset(seen):
        problems.append("MISSING_REQUIRED_JOBS")
    for name in REQUIRED_JOBS & seen.keys():
        job = seen[name]
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            problems.append("UNSUCCESSFUL_" + name)
        if job.get("head_sha") not in (None, expected_sha):
            problems.append("JOB_SHA_MISMATCH")
    return tuple(problems)


def _json_api(path: str, token: str) -> object:
    request = Request(
        "https://api.github.com/repos/" + REPO + path,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "roboport-release-ci-check",
                 **({"Authorization": "Bearer " + token} if token else {})},
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True, help="full 40-char main commit SHA")
    parser.add_argument("--run-id", required=True, type=int, help="GitHub Actions run ID")
    args = parser.parse_args(argv)
    if not SHA_RE.fullmatch(args.sha) or args.run_id <= 0:
        print("BLOCKED: invalid SHA or run ID", file=sys.stderr)
        return 2
    try:
        run = _json_api(f"/actions/runs/{args.run_id}", os.getenv("GITHUB_TOKEN", ""))
        jobs = _json_api(f"/actions/runs/{args.run_id}/jobs?per_page=100",
                         os.getenv("GITHUB_TOKEN", ""))
    except (HTTPError, URLError, OSError, ValueError) as exc:
        print("BLOCKED: cannot verify remote CI evidence: " + type(exc).__name__,
              file=sys.stderr)
        return 2
    errors = validate_run(run, jobs, args.sha, args.run_id)
    if errors:
        print("BLOCKED: " + ", ".join(errors), file=sys.stderr)
        return 1
    print("CI SHA and all five jobs verified. Independent release approvals still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
