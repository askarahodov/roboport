"""Итоговая проверка CI: skip допустим только по явному impact-плану."""
from __future__ import annotations

import json
import os
import sys

JOB_FLAGS = {
    "postgres": "postgres",
    "api": "api",
    "web": "web",
    "browser-e2e": "browser",
}
EVENTS = frozenset({"pull_request", "push", "workflow_dispatch"})


def validate(needs: object, event: str) -> tuple[str, ...]:
    """Не доверять отсутствующим/неожиданным результатам и пустым outputs."""
    if event not in EVENTS:
        return ("UNKNOWN_EVENT",)
    if type(needs) is not dict or set(needs) != {"impact", *JOB_FLAGS}:
        return ("INVALID_NEEDS",)
    if any(type(value) is not dict for value in needs.values()):
        return ("INVALID_JOB_RECORD",)
    if needs["impact"].get("result") != "success":
        return ("IMPACT_NOT_SUCCESS",)
    outputs = needs["impact"].get("outputs")
    if type(outputs) is not dict:
        return ("INVALID_IMPACT_OUTPUTS",)
    flags = (*JOB_FLAGS.values(), "pg_full")
    if any(outputs.get(flag) not in ("true", "false") for flag in flags):
        return ("INVALID_IMPACT_FLAGS",)
    if event != "pull_request" and any(outputs[flag] != "true" for flag in flags):
        return ("FULL_GATE_REQUIRED",)
    if outputs["pg_full"] == "true" and outputs["postgres"] != "true":
        return ("INVALID_PG_PLAN",)
    problems = []
    for job, flag in JOB_FLAGS.items():
        expected = "success" if outputs[flag] == "true" else "skipped"
        if needs[job].get("result") != expected:
            problems.append(f"{job}:EXPECTED_{expected.upper()}")
    return tuple(problems)


def main() -> int:
    try:
        needs = json.loads(os.environ.get("CI_NEEDS_JSON", ""))
    except (TypeError, ValueError):
        print("CI merge gate FAIL: INVALID_NEEDS_JSON", file=sys.stderr)
        return 1
    problems = validate(needs, os.environ.get("GITHUB_EVENT_NAME", ""))
    if problems:
        # Не выводить произвольные данные из outputs в CI-лог.
        print("CI merge gate FAIL: " + ", ".join(problems), file=sys.stderr)
        return 1
    print("CI merge gate PASS: impact and applicable job results match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
