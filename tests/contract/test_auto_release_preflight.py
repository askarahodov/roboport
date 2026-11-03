"""Release preflight remains BLOCKED even when technical parts are green."""
import json

from scripts.check_auto_release_ci import REQUIRED_JOBS
from scripts.check_auto_release_evidence import REQUIRED_GATES
from scripts.check_auto_release_preflight import (
    BLOCKING_ISSUES, evaluate, main, validate_current_main,
)

SHA = "a" * 40
RUN_ID = 123


def ref(sha=SHA):
    return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}}


def evaluate_fixture(*args):
    return evaluate(*args, ref(), ref())


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
    result = evaluate_fixture(*fixtures(), SHA, RUN_ID)
    assert result == {"verdict": "BLOCKED", "technicalChecksPassed": True,
                      "reasons": [], "productionApproved": False}


def test_each_external_issue_blocks():
    for number in BLOCKING_ISSUES:
        evidence, run, jobs, issues = fixtures()
        issues[number] = {"state": "open"}
        result = evaluate_fixture(evidence, run, jobs, issues, SHA, RUN_ID)
        assert f"ISSUE_{number}_NOT_CLOSED" in result["reasons"]
        assert result["technicalChecksPassed"] is False


def test_evidence_ci_and_missing_issues_fail_closed():
    evidence, run, jobs, issues = fixtures()
    evidence["gates"]["privacy_consent"]["approved"] = False
    jobs["jobs"][0]["conclusion"] = "skipped"
    issues.pop(113)
    result = evaluate_fixture(evidence, run, jobs, issues, SHA, RUN_ID)
    assert "privacy_consent" in result["reasons"]
    assert any(x.startswith("CI_UNSUCCESSFUL_") for x in result["reasons"])
    assert "ISSUE_113_NOT_CLOSED" in result["reasons"]


def test_cli_rejects_missing_inputs_before_network(tmp_path):
    assert main(["--sha", "bad", "--run-id", "1",
                 "--evidence", str(tmp_path / "missing.json")]) == 2
    assert main(["--sha", SHA, "--run-id", "1",
                 "--evidence", str(tmp_path / "missing.json")]) == 2



def test_current_main_ref_accepts_only_exact_commit_sha_and_stable_ref():
    assert validate_current_main(ref(), ref(), SHA) == ()
    assert "STALE_MAIN_SHA" in validate_current_main(ref("b"*40), ref("b"*40), SHA)
    assert "MAIN_MOVED_DURING_PREFLIGHT" in validate_current_main(
        ref(), ref("b"*40), SHA,
    )
    invalid=[
        None, {}, {"ref":"refs/heads/other","object":{"type":"commit","sha":SHA}},
        {"ref":"refs/heads/main","object":{"type":"tag","sha":SHA}},
        {"ref":"refs/heads/main","object":{"type":"commit","sha":"not-a-sha"}},
        {"ref":"refs/heads/main","object":{"type":"commit","sha":123}},
    ]
    for wrong in invalid:
        assert "MAIN_REF_NOT_VERIFIED" in validate_current_main(wrong, ref(), SHA)
        assert "MAIN_REF_NOT_VERIFIED" in validate_current_main(ref(), wrong, SHA)


def test_stale_green_ci_and_concurrent_new_main_push_fail_closed():
    for before,after,reason in (
        (ref("b"*40),ref("b"*40),"STALE_MAIN_SHA"),
        (ref(),ref("b"*40),"MAIN_MOVED_DURING_PREFLIGHT"),
        ({"ref":"refs/heads/main"},ref(),"MAIN_REF_NOT_VERIFIED"),
    ):
        result=evaluate(*fixtures(),SHA,RUN_ID,before,after)
        assert result["verdict"]=="BLOCKED"
        assert result["productionApproved"] is False
        assert result["technicalChecksPassed"] is False
        assert reason in result["reasons"]


def test_blocker_catalog_covers_all_current_full_auto_release_domains():
    assert set(BLOCKING_ISSUES)>={
        110,111,113,114,115,116,117,119,121,123,124,165,166,170,172,
    }
    assert len(set(BLOCKING_ISSUES))==len(BLOCKING_ISSUES)


def test_cli_rechecks_live_main_ref_before_and_after_remote_data(
    tmp_path,monkeypatch,capsys,
):
    evidence,run,jobs,issues=fixtures()
    path=tmp_path/"synthetic-evidence.json"
    path.write_text(json.dumps(evidence),encoding="utf-8")
    seen=[]
    final_main={"sha":SHA}

    def stub(url, token):
        assert token==""
        seen.append(url)
        if url=="/git/ref/heads/main":
            return ref(final_main["sha"])
        if url==f"/actions/runs/{RUN_ID}":
            return run
        if url==f"/actions/runs/{RUN_ID}/jobs?per_page=100":
            return jobs
        for number in BLOCKING_ISSUES:
            if url==f"/issues/{number}":
                return issues[number]
        raise AssertionError("unexpected network endpoint")

    monkeypatch.delenv("GITHUB_TOKEN",raising=False)
    monkeypatch.setattr("scripts.check_auto_release_preflight._json_api",stub)
    args=["--sha",SHA,"--run-id",str(RUN_ID),"--evidence",str(path)]
    assert main(args)==1
    result=json.loads(capsys.readouterr().out)
    assert result["technicalChecksPassed"] is True
    assert result["productionApproved"] is False
    assert seen[0]=="/git/ref/heads/main"
    assert seen[-1]=="/git/ref/heads/main"
    assert seen.count("/git/ref/heads/main")==2

    seen.clear()
    final_main["sha"]="b"*40
    assert main(args)==1
    result=json.loads(capsys.readouterr().out)
    assert result["technicalChecksPassed"] is False
    assert "STALE_MAIN_SHA" in result["reasons"]


def test_cli_catches_main_moving_during_checks(tmp_path,monkeypatch,capsys):
    evidence,run,jobs,issues=fixtures()
    path=tmp_path/"synthetic-evidence.json"
    path.write_text(json.dumps(evidence),encoding="utf-8")
    count=0

    def advancing(url,token):
        nonlocal count
        if url=="/git/ref/heads/main":
            count+=1
            return ref(SHA if count==1 else "b"*40)
        if url==f"/actions/runs/{RUN_ID}":
            return run
        if url==f"/actions/runs/{RUN_ID}/jobs?per_page=100":
            return jobs
        for number in BLOCKING_ISSUES:
            if url==f"/issues/{number}":
                return issues[number]
        raise AssertionError(url)

    monkeypatch.delenv("GITHUB_TOKEN",raising=False)
    monkeypatch.setattr("scripts.check_auto_release_preflight._json_api",advancing)
    assert main(["--sha",SHA,"--run-id",str(RUN_ID),
                 "--evidence",str(path)])==1
    result=json.loads(capsys.readouterr().out)
    assert result["technicalChecksPassed"] is False
    assert "MAIN_MOVED_DURING_PREFLIGHT" in result["reasons"]
    assert result["productionApproved"] is False
