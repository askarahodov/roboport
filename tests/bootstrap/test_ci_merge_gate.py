"""Проверка итогового merge gate на синтетических результатах GitHub Actions."""
import json
import os
import unittest
from unittest.mock import patch

from scripts.ci_merge_gate import JOB_FLAGS, main, validate


def fixture(*active: str, full_pg: bool = False) -> dict:
    flags = {flag: "true" if job in active else "false"
             for job, flag in JOB_FLAGS.items()}
    flags["pg_full"] = "true" if full_pg else "false"
    return {
        "impact": {"result": "success", "outputs": flags},
        **{job: {"result": "success" if job in active else "skipped"}
           for job in JOB_FLAGS},
    }


class MergeGateTests(unittest.TestCase):
    def test_docs_only_and_partial_pr(self):
        self.assertEqual(validate(fixture(), "pull_request"), ())
        self.assertEqual(validate(fixture("web", "browser-e2e"), "pull_request"), ())
        self.assertEqual(validate(fixture("api", "postgres", full_pg=True),
                                  "pull_request"), ())

    def test_full_main_and_manual(self):
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event):
                self.assertEqual(validate(fixture(*JOB_FLAGS, full_pg=True), event), ())
                self.assertIn("FULL_GATE_REQUIRED", validate(fixture(), event))
                self.assertIn("FULL_GATE_REQUIRED", validate(fixture(*JOB_FLAGS), event))

    def test_required_job_cannot_skip_fail_cancel_or_be_missing(self):
        for job in JOB_FLAGS:
            for result in ("skipped", "failure", "cancelled", None, "neutral"):
                with self.subTest(job=job, result=result):
                    needs = fixture(*JOB_FLAGS, full_pg=True)
                    needs[job]["result"] = result
                    self.assertIn(f"{job}:EXPECTED_SUCCESS",
                                  validate(needs, "pull_request"))
                    self.assertIn(f"{job}:EXPECTED_SUCCESS", validate(needs, "push"))
            needs = fixture(*JOB_FLAGS, full_pg=True)
            del needs[job]
            self.assertIn("INVALID_NEEDS", validate(needs, "push"))

    def test_nonapplicable_result_must_be_explicitly_skipped(self):
        for result in ("success", "failure", "cancelled", None):
            needs = fixture()
            needs["api"]["result"] = result
            self.assertIn("api:EXPECTED_SKIPPED", validate(needs, "pull_request"))

    def test_impact_failure_and_invalid_flags(self):
        for result in ("skipped", "cancelled", "failure", None):
            needs = fixture()
            needs["impact"]["result"] = result
            self.assertIn("IMPACT_NOT_SUCCESS", validate(needs, "pull_request"))
        for value in (None, "", "TRUE", True, 1, "unknown"):
            needs = fixture()
            needs["impact"]["outputs"]["api"] = value
            self.assertIn("INVALID_IMPACT_FLAGS", validate(needs, "pull_request"))
        needs = fixture()
        del needs["impact"]["outputs"]["browser"]
        self.assertIn("INVALID_IMPACT_FLAGS", validate(needs, "pull_request"))
        needs["impact"].pop("outputs")
        self.assertIn("INVALID_IMPACT_OUTPUTS", validate(needs, "pull_request"))
        self.assertIn("INVALID_PG_PLAN", validate(fixture(full_pg=True), "pull_request"))

    def test_malformed_needs_and_unknown_event(self):
        for needs in (None, [], {}, "not a dict"):
            self.assertIn("INVALID_NEEDS", validate(needs, "pull_request"))
        needs = fixture()
        needs["api"] = None
        self.assertIn("INVALID_JOB_RECORD", validate(needs, "pull_request"))
        self.assertIn("UNKNOWN_EVENT", validate(fixture(), "schedule"))

    def test_cli_exit_codes(self):
        for payload, event, expected in (
            (json.dumps(fixture()), "pull_request", 0),
            ("not json", "pull_request", 1),
            ("{}", "pull_request", 1),
            (json.dumps(fixture()), "push", 1),
            (json.dumps(fixture(*JOB_FLAGS, full_pg=True)), "push", 0),
        ):
            with self.subTest(event=event, expected=expected), patch.dict(
                os.environ, {"CI_NEEDS_JSON": payload, "GITHUB_EVENT_NAME": event},
                clear=True,
            ):
                self.assertEqual(main(), expected)


if __name__ == "__main__":
    unittest.main()
