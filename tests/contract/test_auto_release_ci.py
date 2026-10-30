"""Release CI provenance checks are strict, never approval substitutes."""
from copy import deepcopy

from scripts.check_auto_release_ci import REQUIRED_JOBS, validate_run, main

SHA = "a" * 40


def sample():
    run = {"head_sha": SHA, "head_branch": "main", "event": "push",
           "status": "completed", "conclusion": "success",
           "path": ".github/workflows/ci.yml",
           "id": 41045503073, "run_attempt": 1, "repository": {"full_name": "askarahodov/roboport"}}
    jobs = [{"name": name, "status": "completed", "conclusion": "success",\n             "head_sha": SHA, "run_id": 41045503073, "run_attempt": 1}
            for name in sorted(REQUIRED_JOBS)]
    return run, {"total_count": len(jobs), "jobs": jobs}


def test_verified_main_push_requires_all_five_jobs():
    run, jobs = sample()
    assert validate_run(run, jobs, SHA) == ()
    for name in REQUIRED_JOBS:
        altered = deepcopy(jobs)
        altered["jobs"] = [j for j in altered["jobs"] if j["name"] != name]
        altered["total_count"] -= 1
        assert "MISSING_REQUIRED_JOBS" in validate_run(run, altered, SHA)


def test_fail_closed_on_wrong_run_or_untrusted_job_status():
    for field, bad in (("head_sha", "b" * 40), ("head_branch", "feature"),
                       ("event", "pull_request"), ("status", "in_progress"),
                       ("conclusion", "failure"), ("path", "other.yml")):
        run, jobs = sample()
        run[field] = bad
        assert validate_run(run, jobs, SHA), field
    for bad in ("skipped", "cancelled", "failure", None):
        run, jobs = sample()
        jobs["jobs"][0]["conclusion"] = bad
        assert validate_run(run, jobs, SHA)
    run, jobs = sample()
    jobs["total_count"] += 1
    assert "INCOMPLETE_JOB_LIST" in validate_run(run, jobs, SHA)
    run, jobs = sample()
    jobs["jobs"].append(jobs["jobs"][0])
    jobs["total_count"] += 1
    assert "DUPLICATE_JOB" in validate_run(run, jobs, SHA)


def test_cli_rejects_invalid_args_before_remote_access():
    assert main(["--sha", "bad", "--run-id", "7"]) == 2
    assert main(["--sha", SHA, "--run-id", "0"]) == 2


def test_real_github_run_identity_and_workflow_path():
    run, jobs = sample()
    assert validate_run(run, jobs, SHA, 41045503073) == ()
    for field, value, reason in (
        ("id", 41045503074, "RUN_ID_MISMATCH"),
        ("repository", {"full_name": "someone/other"}, "REPOSITORY_MISMATCH"),
        ("path", "fork/.github/workflows/ci.yml", "UNEXPECTED_WORKFLOW"),
    ):
        altered = deepcopy(run)
        altered[field] = value
        assert reason in validate_run(altered, jobs, SHA, 41045503073)


def test_job_identity_must_match_run_and_attempt():
    for field, wrong, expected in (
        ("head_sha", "b" * 40, "JOB_SHA_MISMATCH"),
        ("run_id", 42, "JOB_RUN_MISMATCH"),
        ("run_attempt", 2, "JOB_RUN_MISMATCH"),
    ):
        run, jobs = sample()
        jobs["jobs"][0][field] = wrong
        assert expected in validate_run(run, jobs, SHA, 41045503073)
    run, jobs = sample()
    del jobs["jobs"][0]["head_sha"]
    assert "JOB_SHA_MISMATCH" in validate_run(run, jobs, SHA)
