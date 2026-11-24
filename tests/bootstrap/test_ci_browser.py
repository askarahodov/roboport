"""Offline regression for PR-only browser test selection and release safety."""
import unittest
import subprocess
import base64
import json
from unittest.mock import patch

from scripts.ci_browser import PAGE_SCENARIOS, E2E_ROOT, plan


SPECS={
    "auto-order-register.spec.ts",
    "auto-order-workspace.spec.ts",
    "auto-order-services.spec.ts",
    "auto-estimates.spec.ts",
    "auto-workshop-qc.spec.ts",
}

REPORT_SPECS = {
    "auto-assignments.spec.ts",
    "auto-branch-cashflow.spec.ts",
    "auto-closed-contribution.spec.ts",
    "auto-closed-order-ledger-reconciliation.spec.ts",
    "auto-crm-funnel.spec.ts",
    "auto-crm-kanban.spec.ts",
    "auto-crm-leads.spec.ts",
    "auto-crm-outcomes.spec.ts",
    "auto-demo-finance-report.spec.ts",
    "auto-material-cost-evidence.spec.ts",
    "auto-mechanic-productivity.spec.ts",
    "auto-mechanic-shift-comparison.spec.ts",
    "auto-order-register.spec.ts",
    "auto-order-workspace.spec.ts",
    "auto-stock-abc-xyz.spec.ts",
    "auto-treasury-cashbook.spec.ts",
    "auto-treasury.spec.ts",
    "warehouse-catalog.spec.ts",
    "warehouse-receiving.spec.ts",
    "warehouse-work-orders.spec.ts",
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

    def test_reporting_domain_includes_related_scenarios(self):
        page = "apps/web/src/pages/staff/AutoBranchCashflow.tsx"
        expected = [
            "e2e/auto-branch-cashflow.spec.ts",
            "e2e/auto-demo-finance-report.spec.ts",
            "e2e/auto-treasury-cashbook.spec.ts",
            "e2e/auto-treasury.spec.ts",
        ]
        self.assertEqual(plan([page], available=REPORT_SPECS), expected)
        self.assertIsNone(plan([page], available=REPORT_SPECS -
                               {"auto-treasury-cashbook.spec.ts"}))

    def test_related_reporting_changes_union_and_full_fallback(self):
        pages = [
            "apps/web/src/pages/staff/AutoMechanicProductivity.tsx",
            "apps/web/src/pages/staff/AutoMechanicShiftComparison.tsx",
            "apps/web/src/pages/staff/AutoStockAbcXyz.tsx",
            "apps/web/e2e/auto-stock-abc-xyz.spec.ts",
            "docs/auto/REPORTING.md",
        ]
        expected = sorted("e2e/" + x for x in (
            "auto-mechanic-productivity.spec.ts",
            "auto-mechanic-shift-comparison.spec.ts",
            "auto-assignments.spec.ts",
            "auto-stock-abc-xyz.spec.ts",
            "warehouse-catalog.spec.ts",
            "warehouse-receiving.spec.ts",
            "warehouse-work-orders.spec.ts",
        ))
        self.assertEqual(plan(pages, available=REPORT_SPECS), expected)
        self.assertIsNone(plan(pages, full=True, available=REPORT_SPECS))
        for shared in ("apps/web/src/main.tsx",
                       "apps/api/roboport_api/auto_document_access.py",
                       "apps/web/e2e/helpers/setup.ts",
                       "apps/web/src/pages/staff/UnknownPage.tsx"):
            with self.subTest(shared=shared):
                self.assertIsNone(plan(pages+[shared], available=REPORT_SPECS))

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

    def test_explicit_page_mappings_exist_in_repository(self):
        for source, specs in PAGE_SCENARIOS.items():
            with self.subTest(source=source):
                self.assertTrue(specs)
                self.assertTrue((E2E_ROOT.parent.parent.parent / source).is_file(),
                                f"missing source {source}")
                for spec in specs:
                    self.assertTrue((E2E_ROOT / spec).is_file(),
                                    f"{source} references missing {spec}")

    def test_explicit_page_mappings_are_nonempty_and_deduplicated(self):
        for key,value in PAGE_SCENARIOS.items():
            self.assertTrue(key.startswith("apps/web/src/pages/"))
            self.assertEqual(len(value),len(set(value)))
            self.assertTrue(value)
            self.assertTrue(all(x.endswith(".spec.ts") for x in value))


if __name__ == "__main__":
    unittest.main()
