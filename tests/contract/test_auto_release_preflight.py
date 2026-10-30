"""Release preflight remains BLOCKED even when technical parts are green."""
from scripts.check_auto_release_ci import REQUIRED_JOBS
from scripts.check_auto_release_evidence import REQUIRED_GATES
from scripts.check_auto_release_preflight import BLOCKING_ISSUES, evaluate, main

SHA = "a" * 40
RUN_ID = 123


def fixtures():
    evidence = {"approved_by": "external-reviewer", "approved_at": "2026-10-30T10:00:00Z",
                "gates": {k: {"approved": True, "evidence_url": f"https://github.com/example/review/{k}"}
                          for k in REQUIRED_GATES}}
    run = {"id": RUN_ID, "run_attempt": 1, "head_sha": SHA,
           "head_branch": "main", "event": "push", "status": "completed",
           "conclusion": "success", "path": ".github/workflows/ci.yml",
           "repository": {"full_name": "askarahodov/roboport"}}
    jobs = {"total_count": 5,
            "jobs": [{"name": name, "run_id": RUN_ID, "run_attempt": 1,
                      "head_sha": SHA, "status": "completed", "conclusion": "success"}
                     for name in sorted(REQUIRED_JOBS)]}
    issues = {i: {"state": "closed"} for i in BLOCKING_ISSUES}
    return evidence, run, jobs, issues


def test_full_technical_preflight_is_never_production_approval():
    result = evaluate(*fixtures(), SHA, RUN_ID)
    assert result == {"verdict": "BLOCKED", "technicalChecksPassed": True,
                      "reasons": [], "productionApproved": False}


def test_each_external_issue_blocks():
    for number in BLOCKING_ISSUES:
        evidence, run, jobs, issues = fixtures()
        issues[number] = {"state": "open"}
        result = evaluate(evidence, run, jobs, issues, SHA, RUN_ID)
        assert f"ISSUE_{number}_NOT_CLOSED" in result["reasons"]
        assert result["technicalChecksPassed"] is False


def test_evidence_ci_and_missing_issues_fail_closed():
    evidence, run, jobs, issues = fixtures()
    evidence["gates"]["privacy_consent"]["approved"] = False
    jobs["jobs"][0]["conclusion"] = "skipped"
    issues.pop(113)
    result = evaluate(evidence, run, jobs, issues, SHA, RUN_ID)
    assert "privacy_consent" in result["reasons"]
    assert any(x.startswith("CI_UNSUCCESSFUL_") for x in result["reasons"])
    assert "ISSUE_113_NOT_CLOSED" in result["reasons"]


def test_cli_rejects_missing_inputs_before_network(tmp_path):
    assert main(["--sha", "bad", "--run-id", "1",
                 "--evidence", str(tmp_path / "missing.json")]) == 2
    assert main(["--sha", SHA, "--run-id", "1",
                 "--evidence", str(tmp_path / "missing.json")]) == 2
