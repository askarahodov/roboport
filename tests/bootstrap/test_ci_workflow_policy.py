"""Проверка, что CI не плодит дублирующиеся browser jobs на feature push."""
import json
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


def test_browser_uses_lockfile_scoped_cache_without_weakening_quality_gates():
    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = config["jobs"]["browser-e2e"]["steps"]
    node = next(step for step in steps if step.get("uses", "").startswith("actions/setup-node@"))
    assert node["with"]["cache"] == "npm"
    assert node["with"]["cache-dependency-path"] == "apps/web/package-lock.json"

    cache = next(step for step in steps if step.get("name") == "Cache Playwright Chromium binaries")
    assert cache["uses"] == "actions/cache@v4"
    assert cache["with"]["path"] == "~/.cache/ms-playwright"
    assert "runner.os" in cache["with"]["key"]
    assert "hashFiles('apps/web/package-lock.json')" in cache["with"]["key"]
    assert steps.index(cache) < next(i for i, step in enumerate(steps) if step.get("name") == "Install browser test runner and Chromium")

    install = next(step for step in steps if step.get("name") == "Install browser test runner and Chromium")
    assert "npx playwright install --with-deps chromium" in install["run"]
    assert any(step.get("name") == "Browser E2E against synthetic FastAPI" for step in steps)
    assert any(step.get("name") == "Browser navigation and search smoke for local Docsify portal" for step in steps)
    assert all(step.get("continue-on-error") is not True for step in steps)
    upload = next(step for step in steps if step.get("name") == "Publish browser acceptance screenshots and traces")
    assert upload["if"] == "always()"

    package = json.loads((root / "apps/web/package.json").read_text(encoding="utf-8"))
    # Установка browser привязана к pinned Playwright, а не к floating dependency.
    playwright_version = package["devDependencies"]["@playwright/test"]
    assert playwright_version[0].isdigit() and not any(x in playwright_version for x in ("^", "~", "*"))
