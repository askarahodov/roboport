"""Source-level contract: automotive role handbook never outruns implemented UI.

These are cheap, platform-independent checks (including Windows CMD) in
addition to real Docsify/Playwright and PostgreSQL acceptance tests.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
WEB = ROOT / "apps" / "web"
API = ROOT / "apps" / "api" / "roboport_api"

ROLE_IDS = (
    "administrator", "intake", "mechanic", "warehouse",
    "manager", "finance", "client", "mobile",
)

# Exact source files for the available browser paths, never legacy robots.
SOURCES = (
    ("AutoStaffAdmin.tsx", "auto-staff-console.spec.ts", "auto_org_staff_http.py"),
    ("AutoCalendar.tsx", "auto-calendar.spec.ts", "auto_appointment_http.py"),
    ("AutoOrderServices.tsx", "auto-order-services.spec.ts", "auto_order_service_http.py"),
    ("AutoWorkshopQc.tsx", "auto-workshop-qc.spec.ts", "auto_workshop_qc_http.py"),
    ("AutoEstimateConsole.tsx", "auto-estimates.spec.ts", "auto_work_order_estimate_http.py"),
    ("AutoWorkExecution.tsx", "auto-work-execution.spec.ts", "auto_work_order_execution_http.py"),
    ("AutoDemoClose.tsx", "auto-demo-close.spec.ts", "auto_demo_close_http.py"),
    ("WarehouseStocktake.tsx", "warehouse-stocktake.spec.ts", "auto_stocktake_http.py"),
    ("WarehouseReceiving.tsx", "warehouse-receiving.spec.ts", "auto_stock_receipt_document.py"),
    ("WarehouseWorkOrders.tsx", "warehouse-work-orders.spec.ts", "auto_stock_lot_work_order_issue.py"),
    ("WarehouseReclassification.tsx", "warehouse-reclassification.spec.ts", "auto_stock_reclassification.py"),
    ("WarehouseSupplierReturns.tsx", "warehouse-supplier-returns.spec.ts", "auto_stock_supplier_return_document.py"),
    ("WarehouseSerialMovements.tsx", "warehouse-serial-movements.spec.ts", "auto_stock_serial_movement.py"),
    ("CustomerVehicles.tsx", "auto-vehicles.spec.ts", "auto_vehicle_http.py"),
    ("CustomerAutoOrders.tsx", "customer-auto-orders.spec.ts", "auto_customer_orders_http.py"),
)


def _text(path):
    return path.read_text(encoding="utf-8")


def test_every_current_synthetic_role_has_independent_docs_and_matrix():
    sidebar = _text(DOCS / "_sidebar.md")
    matrix = _text(DOCS / "guides" / "ACCESS_AND_TEST_MATRIX.md")
    quick = _text(DOCS / "guides" / "QUICK_START.md")
    for name in ROLE_IDS:
        page = DOCS / "guides" / (name + ".md")
        assert page.is_file(), name
        assert f"(/guides/{name}.md)" in sidebar, name
        assert _text(page).startswith("# "), name
    for name in ("QUICK_START", "ACCESS_AND_TEST_MATRIX", "FAQ", "SCREENSHOT_EVIDENCE"):
        assert (DOCS / "guides" / (name + ".md")).is_file(), name
        assert f"(/guides/{name}.md)" in sidebar, name
    assert "[Руководитель](manager.md)" in matrix
    for phrase in ("401", "403", "409", "planned", "releaseAllowed"):
        assert phrase in quick
    assert "платёж" in _text(DOCS / "auto" / "CUSTOMER_HISTORY.md").lower()


def test_relevant_ui_e2e_api_sources_exist_and_matrix_does_not_claim_legacy_as_auto():
    matrix = _text(DOCS / "guides" / "ACCESS_AND_TEST_MATRIX.md")
    source_files = [p for p in (WEB / "src" / "pages").rglob("*.tsx")]
    for component, browser_test, api in SOURCES:
        assert any(p.name == component for p in source_files), component
        assert (WEB / "e2e" / browser_test).is_file(), browser_test
        assert (API / api).is_file(), api
        assert component in matrix, component
        assert browser_test in matrix, browser_test
    assert "не" in matrix.lower() and "current-dev" in matrix
    assert "releaseAllowed" in _text(DOCS / "auto" / "OPERATIONS_EVIDENCE.md")


def test_robot_legacy_in_separate_archived_nav_not_user_role_instructions():
    sidebar = _text(DOCS / "_sidebar.md")
    archive = _text(DOCS / "auto" / "LEGACY_ARCHIVE.md")
    assert "(/auto/LEGACY_ARCHIVE.md)" in sidebar
    current_nav = sidebar.split("- **Роли: частично доступно в synthetic dev**", 1)[1].split(
        "- **Разработчикам**", 1
    )[0]
    for old in ("TECHNICAL_SPEC", "TICKET_FSM", "WORK_BREAKDOWN"):
        assert old not in current_nav
        assert old in archive
    assert "#165" in archive and "cutover" in archive
    assert "legacy" in _text(DOCS / "README.md")


def test_32_real_viewport_screenshots_and_safe_evidence_procedure():
    test_source = _text(WEB / "e2e-docs" / "role-screenshots.spec.ts")
    evidence = _text(DOCS / "guides" / "SCREENSHOT_EVIDENCE.md")
    for role in ROLE_IDS:
        assert f"id:'{role}'" in test_source, role
    for token in ("390", "1280", "light", "dark", "page.screenshot", "outputPath"):
        assert token in test_source
    for phrase in ("14 дней", "roboport-browser-evidence", "SHA",
                   "независим", "ручной"):
        assert phrase in evidence
    assert "32 снимка" in evidence
