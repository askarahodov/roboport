"""Проверка, что CI не плодит дублирующиеся browser jobs на feature push."""
from pathlib import Path

import yaml


def test_ci_checks_current_pr_and_main_without_duplicate_feature_push():
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
    config = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    # PyYAML YAML 1.1 трактует ключ 'on' как True; оба представления допустимы.
    events = config.get("on", config.get(True))
    assert events["push"]["branches"] == ["main"]
    assert "pull_request" in events
    assert "workflow_dispatch" in events
    concurrency = config["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert "github.event.pull_request.number" in concurrency["group"]
    assert "github.ref" in concurrency["group"]
    jobs = config["jobs"]
    assert {"api", "web", "postgres", "browser-e2e"} <= set(jobs)
