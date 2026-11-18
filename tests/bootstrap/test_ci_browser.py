"""Offline regression for PR-only browser test selection and release safety."""
import unittest
import subprocess
import base64
import json
from unittest.mock import patch

from scripts.ci_browser import PAGE_SCENARIOS, plan


SPECS={
    "auto-order-register.spec.ts",
    "auto-order-workspace.spec.ts",
    "auto-order-services.spec.ts",
    "auto-estimates.spec.ts",
    "auto-workshop-qc.spec.ts",
}


class BrowserImpactTests(unittest.TestCase):
    def test_single_spec_only_runs_that_spec(self):
        self.assertEqual(
            plan(["apps/web/e2e/auto-order-register.spec.ts"],
                 available=SPECS),
            ["e2e/auto-order-register.spec.ts"],
        )

    def test_related_workspace_and_register_stay_together(self):
        result=plan(
            ["apps/web/src/pages/staff/AutoWorkOrderRegister.tsx",
             "docs/guides/intake.md"],
            available=SPECS,
        )
        self.assertEqual(result, [
            "e2e/auto-order-register.spec.ts",
            "e2e/auto-order-workspace.spec.ts",
        ])
        self.assertEqual(
            plan(["apps/web/src/pages/staff/AutoOrderServices.tsx"],
                 available=SPECS),
            ["e2e/auto-order-register.spec.ts",
             "e2e/auto-order-services.spec.ts",
             "e2e/auto-order-workspace.spec.ts"],
        )

    def test_unsafe_mixes_and_shared_modules_force_full(self):
        for path in (
            "apps/web/src/main.tsx",
            "apps/web/src/style.css",
            "apps/web/src/pages/staff/AutoCalendar.tsx",
            "apps/web/e2e/helpers/setup.ts",
            "apps/web/package-lock.json",
            ".github/workflows/ci.yml",
            "scripts/ci_browser.py",
            "apps/api/roboport_api/auto_work_order_execution.py",
            "tests/integration/test_pg_auto_appointment.py",
        ):
            with self.subTest(path=path):
                self.assertIsNone(
                    plan(["apps/web/e2e/auto-order-register.spec.ts",path],
                         available=SPECS),
                )

    def test_missing_or_deleted_spec_never_skips_regression(self):
        self.assertIsNone(plan(
            ["apps/web/e2e/auto-order-register.spec.ts"],available=set(),
        ))
        self.assertIsNone(plan(
            ["apps/web/src/pages/staff/AutoWorkOrderRegister.tsx"],
            available={"auto-order-register.spec.ts"},
        ))
        self.assertIsNone(plan(
            ["apps/web/e2e/missing.spec.ts"],available=SPECS,
        ))

    def test_full_release_and_ambiguous_changes(self):
        for paths in ([],["docs/auto/REPORTING.md"],
                      ["README.md","docs/guides/intake.md"]):
            self.assertIsNone(plan(paths,available=SPECS))
        self.assertIsNone(plan(
            ["apps/web/e2e/auto-order-register.spec.ts"],
            full=True,available=SPECS,
        ))

    def test_host_provided_paths_are_canonical_and_fail_full_on_corruption(self):
        from scripts.ci_browser import decode_paths
        changed=["apps/web/e2e/auto-order-register.spec.ts"]
        encoded=base64.urlsafe_b64encode(
            json.dumps(changed,separators=(",",":")).encode()
        ).decode().rstrip("=")
        self.assertEqual(decode_paths(encoded),changed)
        self.assertEqual(plan(decode_paths(encoded),available=SPECS),
                         ["e2e/auto-order-register.spec.ts"])
        for invalid in ("", "FULL", "@@@", encoded+"!", "A"*12001):
            self.assertIsNone(decode_paths(invalid))
        for invalid_path in ("../outside.spec.ts", "/tmp/out.spec.ts",
                             "apps/web/e2e/bad\\\\path.spec.ts",
                             "apps/web/e2e/bad\\n.spec.ts"):
            value=base64.urlsafe_b64encode(
                json.dumps([invalid_path]).encode()
            ).decode().rstrip("=")
            self.assertIsNone(decode_paths(value))

    def test_git_failure_runs_full_suite_not_zero_tests(self):
        from scripts.ci_browser import main
        failure=subprocess.CalledProcessError(
            129,["git","diff"],stderr=b"usage: git diff",
        )
        with patch("sys.argv",["ci_browser","--base","abcdef","--run"]), \
             patch("scripts.ci_browser.changed",side_effect=failure), \
             patch("scripts.ci_browser.subprocess.call",return_value=0) as launch:
            self.assertEqual(main(),0)
            self.assertEqual(launch.call_args.args[0],
                             ["npm","run","test:e2e"])

    def test_explicit_page_mappings_are_nonempty_and_deduplicated(self):
        for key,value in PAGE_SCENARIOS.items():
            self.assertTrue(key.startswith("apps/web/src/pages/"))
            self.assertEqual(len(value),len(set(value)))
            self.assertTrue(value)
            self.assertTrue(all(x.endswith(".spec.ts") for x in value))


if __name__ == "__main__":
    unittest.main()
