"""Conservative PR impact selection. Main/manual CI remains an unconditional full gate.

Unknown/infra/CI paths fail open to *more* tests, never silently exclude them.
Only explicitly changed PG integration tests, or the mandatory tests of a
verified isolated offline package, may be selected for scoped runs.
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


# Closed groups of offline tools. Each group lists implementation paths,
# mandatory contract tests (the API job runs the entire contract suite), and
# mandatory real PostgreSQL tests. No shared preflight, HTTP, DB authority,
# restore probe, migration or CI script is included in this allowlist.
OFFLINE_GROUPS = (
    (("scripts/auto_migration_provenance_bundle.py",),
     ("tests/contract/test_auto_migration_provenance_bundle.py",), ()),
    (("scripts/auto_vehicle_column_mapping.py",),
     ("tests/contract/test_auto_vehicle_column_mapping.py",), ()),
    (("scripts/auto_synthetic_xlsx.py", "scripts/auto_synthetic_batch.py"),
     ("tests/contract/test_auto_synthetic_xlsx.py",
      "tests/contract/test_auto_synthetic_batch.py"), ()),
    (("scripts/auto_cutover_export_verify.py",
      "apps/api/roboport_api/auto_synthetic_cutover_export.py"),
     ("tests/contract/test_auto_cutover_export_verify.py",),
     ("tests/integration/test_pg_auto_synthetic_cutover_export.py",)),
)


def _doc_only(path: str) -> bool:
    # A Markdown file inside a workflow/infra path must not bypass a gate.
    return path.startswith("docs/") and path.endswith(".md")


def _offline_candidate(paths: list[str]):
    selected = [group for group in OFFLINE_GROUPS
                if set(paths).intersection(group[0])]
    if not selected:
        return None
    allowed = {path for group in selected for items in group for path in items}
    if any(path not in allowed and not _doc_only(path) for path in paths):
        return None
    return selected


def verified_offline_plan(paths: list[str], base: str | None) -> tuple[str, ...] | None:
    """Return mandatory PG tests for an independently checked offline-only PR.

    Empty tuple means API/contract only; None means no shortcut. Check literal
    references to every group source stem in BOTH trees (base and HEAD), not
    only files modified by the PR. Unknown executable consumers, missing
    tests, unreadable trees and all mixed/shared changes fall back to full.
    This is an explicit reviewed allowlist, not a general dependency solver.
    """
    groups = _offline_candidate(paths)
    if not groups or not base:
        return None
    try:
        for sources, contracts, integration in groups:
            allowed = set(sources + contracts + integration)
            if any(not Path(path).is_file() or Path(path).is_symlink()
                   for path in allowed):
                return None
            for source in sources:
                stem = Path(source).stem
                for ref in (base, "HEAD"):
                    result = subprocess.run(
                        ["git", "grep", "-z", "-l", "-F", "-e", stem, ref, "--"],
                        capture_output=True, check=False,
                    )
                    if result.returncode not in (0, 1):
                        return None
                    for record in result.stdout.split(b"\0"):
                        if not record:
                            continue
                        prefix, separator, raw_path = record.partition(b":")
                        if not separator or prefix.decode("utf-8") != ref:
                            return None
                        path = raw_path.decode("utf-8")
                        if (path not in allowed and not path.endswith(".md")
                                and path != "scripts/ci_impact.py"
                                and path != "tests/bootstrap/test_ci_impact.py"):
                            return None
        return tuple(sorted({test for group in groups for test in group[2]}))
    except (OSError, UnicodeError, ValueError):
        return None


def impact(paths: list[str], *, full: bool = False,
           offline_plan: tuple[str, ...] | None = None) -> tuple[set[str], bool]:
    if full or not paths:
        return set(FULL), True
    groups = _offline_candidate(paths)
    if groups and offline_plan is not None:
        mandatory = tuple(sorted({test for group in groups for test in group[2]}))
        if offline_plan == mandatory:
            return ({"api", "postgres"} if mandatory else {"api"}), False
    # Any known offline implementation with an unverified or mixed scope
    # stays full, including the backend module under auto_*.py.
    if any(set(paths).intersection(group[0]) for group in OFFLINE_GROUPS):
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
            # Runtime modules can have unchanged transitive consumers.
            # A newly added test cannot replace the full integration suite.
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


def run_integration(paths: list[str], *, full_pg: bool,
                    mandatory_tests: tuple[str, ...] = ()) -> int:
    tests = sorted(set(mandatory_tests) | set(
        p for p in paths if p.startswith("tests/integration/")
        and p.endswith(".py") and Path(p).is_file()
    ))
    if full_pg or not tests or any(not Path(test).is_file() for test in tests):
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
    plan = None if args.full else verified_offline_plan(paths, args.base)
    selected, full_pg = impact(paths, full=args.full, offline_plan=plan)
    if args.run_integration:
        if "postgres" not in selected:
            raise RuntimeError("PostgreSQL scope is not active")
        return run_integration(paths, full_pg=full_pg, mandatory_tests=plan or ())
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
