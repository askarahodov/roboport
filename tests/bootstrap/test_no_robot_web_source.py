"""CI gate: deployable React source must remain automotive-only (#165)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/web/src"
RETIRED = (
    "/api/v1/catalog/",
    "/api/v1/dev/tickets",
    "/api/v1/dev/authorized/tickets",
    "/api/v1/dev/guest-intake",
    "/api/v1/dev/guest-claims",
    "/api/v1/dev/account/tickets",
    "/api/v1/dev/account/summary",
    "/api/v1/dev/account/preferences",
    "/api/v1/dev/staff/queue",
    "/api/v1/dev/staff/audit",
    "/account/robots",
    "/account/tickets",
    "/request-demo",
    "робот",
    "robot_device",
    "service_ticket",
)


def test_live_frontend_has_no_retired_robot_imports_routes_or_ui_words():
    sources = sorted(list(FRONTEND.rglob("*.ts")) + list(FRONTEND.rglob("*.tsx")))
    assert sources
    for path in sources:
        content = path.read_text(encoding="utf-8").lower()
        for marker in RETIRED:
            assert marker not in content, (str(path.relative_to(ROOT)), marker)


def test_old_catalog_sdk_and_screens_only_exist_in_archived_test_fixtures():
    assert not (FRONTEND / "shared/api/catalogClient.ts").exists()
    assert not (FRONTEND / "shared/api/schema.d.ts").exists()
    assert (ROOT / "tests/legacy_assets/catalogClient.ts").is_file()
    assert (ROOT / "tests/legacy_assets/schema.d.ts").is_file()
    for old in (
        "customer/CustomerSummary.tsx",
        "customer/CustomerTickets.tsx",
        "customer/EstimateActions.tsx",
        "customer/GuestIntake.tsx",
        "public/PublicCatalog.tsx",
        "staff/StaffAudit.tsx",
        "staff/StaffGuestClaims.tsx",
        "staff/StaffOperations.tsx",
        "staff/TicketWorkflow.tsx",
    ):
        assert not (FRONTEND / "pages" / old).exists(), old
