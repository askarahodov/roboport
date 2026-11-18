"""Консервативный выбор браузерных тестов для маленьких PR.

Все ветки main, ручной запуск, изменения общего UI/CI/API и неизвестные
зависимости всегда запускают полную Playwright-приёмку. При небольшом
изменении отдельного .spec.ts можно выполнять только этот сценарий;
для явно известных изолированных экранов — их сценарии и соседние
переходы. Ни одного пропуска релизных проверок.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from scripts.ci_impact import changed

ROOT = Path(__file__).resolve().parent.parent
E2E_ROOT = ROOT / "apps/web/e2e"

# Явные связи экрана и сценариев. При сомнении — весь набор.
# Сохраняем тест перехода из списка к рабочему месту и обратно.
PAGE_SCENARIOS = {
    "apps/web/src/pages/staff/AutoWorkOrderRegister.tsx": (
        "auto-order-register.spec.ts", "auto-order-workspace.spec.ts",
    ),
    "apps/web/src/pages/staff/AutoWorkOrderWorkspace.tsx": (
        "auto-order-register.spec.ts", "auto-order-workspace.spec.ts",
    ),
    "apps/web/src/pages/staff/AutoOrderServices.tsx": (
        "auto-order-services.spec.ts", "auto-order-register.spec.ts",
        "auto-order-workspace.spec.ts",
    ),
}

def plan(paths: list[str], *, full: bool = False,
         available: set[str] | None = None) -> list[str] | None:
    """Список Playwright spec относительно apps/web либо None=полный набор.

    Не доверяем удалению теста или отсутствию ожидаемого сценария:
    тогда надо выполнять полную регрессию, а не молча пропускать.
    """
    if full or not paths:
        return None
    known = (available if available is not None else
             {p.name for p in E2E_ROOT.glob("*.spec.ts")})
    selected: set[str] = set()
    effective = 0
    for path in paths:
        if path.startswith("docs/") or path.endswith(".md"):
            continue
        effective += 1
        if (path.startswith("apps/web/e2e/") and
            path.count("/") == 3 and path.endswith(".spec.ts")):
            candidate = path.rsplit("/", 1)[-1]
            if candidate not in known:
                return None
            selected.add(candidate)
        elif path in PAGE_SCENARIOS:
            required = PAGE_SCENARIOS[path]
            if any(spec not in known for spec in required):
                return None
            selected.update(required)
        else:
            # shared components, lockfile, root router, API, DB, config,
            # scripts, tests infrastructure => unknown impact/full E2E.
            return None
    if not effective or not selected:
        return None
    return ["e2e/" + name for name in sorted(selected)]


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--base")
    group.add_argument("--full", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    paths = [] if args.full else changed(args.base)
    specs = plan(paths, full=args.full)
    print("Browser selection:", "FULL" if specs is None else " ".join(specs), flush=True)
    if not args.run:
        return 0
    command = ["npm", "run", "test:e2e"]
    if specs is not None:
        command.extend(["--", *specs])
    return subprocess.call(command, cwd=ROOT / "apps/web")


if __name__ == "__main__":
    raise SystemExit(main())
