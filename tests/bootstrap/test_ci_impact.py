"""Impact strategy is fail-safe and release-grade main is always full."""
import unittest
import subprocess
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory

from unittest.mock import patch

from scripts.ci_impact import (
    FULL, OFFLINE_GROUPS, changed, impact, run_integration, verified_offline_plan,
)


class ImpactTests(unittest.TestCase):
    def test_docs_only_is_lightweight(self):
        self.assertEqual(impact(["docs/auto/REPORTING.md"]), (set(), False))

    def test_css_does_not_launch_browser(self):
        self.assertEqual(impact(["apps/web/src/styles.css"]), ({"web"}, False))

    def test_frontend_behavior_needs_browser(self):
        self.assertEqual(impact(["apps/web/src/pages/staff/Panel.tsx"]),
                         ({"web", "browser-e2e"}, False))

    def test_runtime_backend_requires_full_pg_even_with_changed_test(self):
        self.assertEqual(impact(["apps/api/roboport_api/auto_mechanic_productivity.py",
                                 "tests/integration/test_pg_auto_mechanic_productivity_http.py"]),
                         ({"postgres", "api"}, True))
        for source in ("auto_order_customer_read.py", "auto_workshop_qc.py",
                       "auto_stock_lot_stocktake.py", "auto_purchase_document_policy.py"):
            self.assertEqual(impact(["apps/api/roboport_api/" + source,
                                     "tests/integration/test_pg_auto_new.py"]),
                             ({"postgres", "api"}, True))

    def test_security_ddl_and_ci_are_full(self):
        for path in ("apps/api/alembic/versions/0140_test.py",
                     "apps/api/roboport_api/auto_document_access.py",
                     ".github/workflows/ci.yml", "infra/dev/compose.yml"):
            self.assertEqual(impact([path]), (set(FULL), True))

    def test_unknown_and_no_diff_fail_closed(self):
        self.assertEqual(impact(["new_unknown_extension.xyz"]), (set(FULL), True))
        self.assertEqual(impact([]), (set(FULL), True))

    def test_rename_of_critical_path_to_docs_keeps_full_impact(self):
        with TemporaryDirectory() as directory, chdir(directory):
            def git(*args):
                return subprocess.check_output(
                    ["git", *args], text=True, stderr=subprocess.STDOUT,
                ).strip()

            git("init")
            old = Path("apps/api/roboport_api/main.py")
            old.parent.mkdir(parents=True)
            old.write_text("# synthetic marker\n", encoding="utf-8")
            git("add", ".")
            git("-c", "user.name=CI", "-c", "user.email=ci@example.invalid",
                "commit", "-m", "base")
            base = git("rev-parse", "HEAD")
            new = Path("docs/archive.md")
            new.parent.mkdir()
            git("mv", str(old), str(new))
            git("-c", "user.name=CI", "-c", "user.email=ci@example.invalid",
                "commit", "-m", "rename")
            paths = changed(base)
            self.assertEqual(set(paths), {old.as_posix(), new.as_posix()})
            self.assertEqual(impact(paths), (set(FULL), True))

    def test_offline_sources_require_verified_plan_and_never_override_full(self):
        for sources, contracts, integration in OFFLINE_GROUPS:
            paths = [sources[0], "docs/auto/DATA_MIGRATION.md"]
            self.assertEqual(impact(paths), (set(FULL), True))
            self.assertEqual(
                impact(paths, offline_plan=integration),
                ({"api", "postgres"} if integration else {"api"}, False),
            )
            self.assertEqual(impact(paths, full=True, offline_plan=integration),
                             (set(FULL), True))
            # Any unknown script or critical path keeps all gates.
            for extra in ("scripts/unknown.py", "apps/api/alembic/versions/new.py",
                          "infra/dev/README.md", ".github/workflows/ci.yml"):
                self.assertEqual(impact(paths + [extra], offline_plan=integration),
                                 (set(FULL), True))

    def test_mixed_backend_or_wrong_mandatory_plan_never_skips_pg(self):
        backend = OFFLINE_GROUPS[-1][0][1]
        self.assertEqual(impact([backend], offline_plan=()), (set(FULL), True))
        self.assertEqual(impact([backend, "apps/api/roboport_api/auto_other.py"],
                                offline_plan=OFFLINE_GROUPS[-1][2]),
                         (set(FULL), True))
        paths = [OFFLINE_GROUPS[0][0][0], OFFLINE_GROUPS[-1][0][0]]
        self.assertEqual(impact(paths, offline_plan=OFFLINE_GROUPS[-1][2]),
                         ({"api", "postgres"}, False))

    def test_verified_offline_scope_checks_base_head_and_required_files(self):
        for sources, contracts, integration in OFFLINE_GROUPS:
            with self.subTest(sources=sources), TemporaryDirectory() as directory, chdir(directory):
                def git(*args):
                    return subprocess.check_output(
                        ["git", *args], text=True, stderr=subprocess.STDOUT,
                    ).strip()

                def write(path, content):
                    p = Path(path)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")

                def commit(message):
                    git("add", ".")
                    git("-c", "user.name=CI", "-c", "user.email=ci@example.invalid",
                        "commit", "-m", message)
                    return git("rev-parse", "HEAD")

                git("init")
                for source in sources:
                    write(source, "# isolated source\n")
                for test in contracts + integration:
                    write(test, "# " + " ".join(Path(s).stem for s in sources) + "\n")
                base = commit("isolated tools")
                write(sources[0], "# changed implementation\n")
                commit("change")
                paths = changed(base)
                self.assertEqual(paths, [sources[0]])
                self.assertEqual(verified_offline_plan(paths, base), integration)
                self.assertIsNone(verified_offline_plan(paths, "missing-ref"))
                # Missing tests disable the shortcut rather than silently skip.
                Path(contracts[0]).unlink()
                self.assertIsNone(verified_offline_plan(paths, base))
                git("checkout", "HEAD", "--", contracts[0])
                # New executable consumer at HEAD, including one missed by an
                # artificially narrow path list, invalidates the allowlist.
                write("apps/api/roboport_api/unexpected.py",
                      "# " + Path(sources[0]).stem + "\n")
                unsafe = commit("new consumer")
                self.assertIsNone(verified_offline_plan(paths, base))
                Path("apps/api/roboport_api/unexpected.py").unlink()
                commit("remove consumer")
                # A consumer in the base tree also invalidates the shortcut.
                self.assertIsNone(verified_offline_plan(paths, unsafe))
                self.assertEqual(verified_offline_plan(paths, base), integration)

    def test_scoped_cutover_cli_always_runs_pg_integration_without_changed_test(self):
        source = OFFLINE_GROUPS[-1][0][0]
        mandatory = OFFLINE_GROUPS[-1][2]
        with TemporaryDirectory() as directory, chdir(directory):
            test = Path(mandatory[0])
            test.parent.mkdir(parents=True)
            test.write_text("# integration test\n", encoding="utf-8")
            with patch("scripts.ci_impact.subprocess.call", return_value=0) as call:
                self.assertEqual(run_integration([source], full_pg=False,
                                                 mandatory_tests=mandatory), 0)
                self.assertEqual(call.call_args.args[0][-1], mandatory[0])
                test.unlink()
                run_integration([source], full_pg=False, mandatory_tests=mandatory)
                self.assertEqual(call.call_args.args[0][-1], "tests/integration")

    def test_linux_only_frontend_config_and_full_matrix(self):
        self.assertEqual(set(FULL), {"postgres", "api", "web", "browser-e2e"})
        for path in ("apps/web/package-lock.json", "apps/web/package.json",
                     "apps/web/vite.config.ts", "apps/web/playwright.config.ts"):
            self.assertEqual(impact([path]), ({"web", "browser-e2e"}, False))

    def test_main_and_manual_are_full_even_for_docs(self):
        self.assertEqual(impact(["docs/README.md"], full=True), (set(FULL), True))


if __name__ == "__main__":
    unittest.main()
