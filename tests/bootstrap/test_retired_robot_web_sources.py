"""Prevent returning deleted robot-facing browser modules after auto UI cutover (#165).

This is a source/build boundary, not proof that legacy API/SQL has been removed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps/web/src"

RETIRED = (
    "pages/public/PublicCatalog.tsx",
    "pages/customer/GuestIntake.tsx",
    "pages/customer/CustomerTickets.tsx",
    "pages/staff/StaffGuestClaims.tsx",
    "pages/staff/TicketWorkflow.tsx",
    "shared/api/catalogClient.ts",
)

FORBIDDEN_BROWSER_API = (
    "/api/v1/catalog/",
    "/api/v1/dev/tickets",
    "/api/v1/dev/guest-intake",
    "/api/v1/dev/guest-claims",
    "/api/v1/dev/account/tickets",
)


def test_retired_robot_ui_and_client_sdk_absent_from_web_sources():
    for path in RETIRED:
        assert not (WEB / path).exists(), f"Legacy browser module restored: {path}"


def test_retired_robot_api_not_called_by_current_handwritten_browser_sources():
    # Generated OpenAPI type snapshots are archived draft evidence, not API calls.
    for path in sorted(WEB.rglob("*")):
        if path.suffix not in {".ts", ".tsx"} or path.name.endswith(".d.ts"):
            continue
        source = path.read_text(encoding="utf-8")
        for endpoint in FORBIDDEN_BROWSER_API:
            assert endpoint not in source, f"Legacy browser API {endpoint}: {path.relative_to(ROOT)}"


def test_current_browser_entrypoint_only_registers_automotive_workspaces():
    main = (WEB / "main.tsx").read_text(encoding="utf-8")
    for required in ("/account/vehicles", "/account/auto-booking", "/staff/auto-calendar"):
        assert required in main
    for obsolete in ('path="/robots"', 'path="/catalog/', 'path="/request-demo"',
                     'path="/account/claim"'):
        assert obsolete not in main
