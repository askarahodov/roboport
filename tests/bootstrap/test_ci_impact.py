"""Impact strategy is fail-safe and release-grade main is always full."""
import unittest

from scripts.ci_impact import FULL, impact


class ImpactTests(unittest.TestCase):
    def test_docs_only_is_lightweight(self):
        self.assertEqual(impact(["docs/auto/REPORTING.md"]), (set(), False))

    def test_css_does_not_launch_browser(self):
        self.assertEqual(impact(["apps/web/src/styles.css"]), ({"web"}, False))

    def test_frontend_behavior_needs_browser(self):
        self.assertEqual(impact(["apps/web/src/pages/staff/Panel.tsx"]),
                         ({"web", "browser-e2e"}, False))

    def test_local_backend_and_scoped_pg(self):
        self.assertEqual(impact(["apps/api/roboport_api/auto_mechanic_productivity.py",
                                 "tests/integration/test_pg_auto_mechanic_productivity_http.py"]),
                         ({"postgres", "api"}, False))

    def test_security_ddl_and_ci_are_full(self):
        for path in ("apps/api/alembic/versions/0140_test.py",
                     "apps/api/roboport_api/auto_document_access.py",
                     ".github/workflows/ci.yml", "infra/dev/compose.yml"):
            self.assertEqual(impact([path]), (set(FULL), True))

    def test_unknown_and_no_diff_fail_closed(self):
        self.assertEqual(impact(["new_unknown_extension.xyz"]), (set(FULL), True))
        self.assertEqual(impact([]), (set(FULL), True))

    def test_main_and_manual_are_full_even_for_docs(self):
        self.assertEqual(impact(["docs/README.md"], full=True), (set(FULL), True))


if __name__ == "__main__":
    unittest.main()
