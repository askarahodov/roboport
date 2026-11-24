"""Conservative PR impact selection. Main/manual CI remains an unconditional full gate.

Unknown/infra/CI paths fail open to *more* tests, never silently exclude them.
Only explicitly changed PG integration tests may be selected for scoped runs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

JOBS = ("postgres", "api", "web", "browser-e2e", "windows-cmd")
FULL = frozenset(JOBS)
CRITICAL = (
    "apps/api/alembic/", "infra/", ".github/workflows/",
    "apps/api/requirements", "apps/api/constraints",
    "apps/api/roboport_api/main.py", "apps/api/roboport_api/auto_org_authority.py",
    "apps/api/roboport_api/auto_document_access.py",
    "apps/api/roboport_api/auto_vehicle_claim_http.py",
)


def impact(paths: list[str], *, full: bool = False) -> tuple[set[str], bool]:
    if full or not paths:
        return set(FULL), True
    required: set[str] = set()
    full_pg = False
    for path in paths:
        if path.startswith(CRITICAL) or path in ("AGENTS.md",):
            return set(FULL), True
        if path.startswith(("scripts/", ".github/", "db/", "contracts/")):
            return set(FULL), True
        if path.startswith("apps/api/roboport_api/"):
            required.update(("postgres", "api"))
            if not path.split("/")[-1].startswith("auto_"):
                full_pg = True
        elif path.startswith("apps/api/"):
            required.update(("postgres", "api"))
            full_pg = True
        elif path.startswith("tests/integration/"):
            required.add("postgres")
        elif path.startswith(("tests/bootstrap/", "tests/contract/")):
            required.add("api")
        elif path.startswith("apps/web/e2e/"):
            required.add("browser-e2e")
        elif path.startswith("apps/web/src/"):
            required.add("web")
            if not path.endswith((".css", ".scss", ".svg", ".png", ".webp")):
                required.add("browser-e2e")
        elif path.startswith("apps/web/"):
            required.update(("web", "browser-e2e", "windows-cmd"))
        elif path.startswith(("docs/", ".github/ISSUE_TEMPLATE/")) or path.endswith(".md"):
            pass  # Docs gate always runs; the five release jobs stay skipped on docs PR.
        else:
            return set(FULL), True
    return required, full_pg


def changed(base: str) -> list[str]:
    # -z preserves unusual filenames; '--' terminates revision arguments.
    # Treat a rename as deletion + addition: a critical old path must not
    # disappear from impact selection when moved to a lightweight path.
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", "--diff-filter=ACMRTD",
         f"{base}...HEAD", "--"],
        check=True, capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def run_integration(paths: list[str], *, full_pg: bool) -> int:
    tests = sorted(set(
        p for p in paths if p.startswith("tests/integration/")
        and p.endswith(".py") and Path(p).is_file()
    ))
    if full_pg or not tests:
        tests = ["tests/integration"]
    print("PostgreSQL tests:", *tests, flush=True)
    return subprocess.call([sys.executable, "-m", "pytest", "-q", "-ra", *tests])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--run-integration", action="store_true")
    args = parser.parse_args()
    paths = [] if args.full else changed(args.base) if args.base else None
    if paths is None:
        parser.error("either --base or --full must be supplied")
    selected, full_pg = impact(paths, full=args.full)
    if args.run_integration:
        if "postgres" not in selected:
            raise RuntimeError("PostgreSQL scope is not active")
        return run_integration(paths, full_pg=full_pg)
    lines = [f"{job.replace('-', '_')}={'true' if job in selected else 'false'}"
             for job in JOBS]
    lines.append(f"pg_full={'true' if full_pg else 'false'}")
    # Browser container does not mount .git. Transfer a bounded, canonical
    # list computed by the trusted host impact job. Oversized/unknown = FULL.
    browser_paths = "FULL"
    if not args.full and paths and len(paths) <= 250:
        blob = json.dumps(paths, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        encoded = base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")
        if len(encoded) <= 12000:
            browser_paths = encoded
    lines.append(f"browser_paths_b64={browser_paths}")
    output = "\n".join(lines) + "\n"
    print(output, end="")
    if target := os.environ.get("GITHUB_OUTPUT"):
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
