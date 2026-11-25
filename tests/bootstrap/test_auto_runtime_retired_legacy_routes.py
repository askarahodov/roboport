"""Airmatik runtime must never mount historical robot HTTP endpoints (#165).

Legacy data tests exercise tests.legacy_app, which is outside the deployable
Python package. Even with all synthetic switches enabled the production ASGI
entrypoint must have no route to robot tickets, catalog, claims or stock.
"""
import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app

ROOT = Path(__file__).resolve().parents[2]
RETIRED = (
    "/api/v1/catalog",
    "/api/v1/dev/tickets",
    "/api/v1/dev/authorized/tickets",
    "/api/v1/dev/attachments",
    "/api/v1/dev/estimates",
    "/api/v1/dev/catalog",
    "/api/v1/dev/inventory",
    "/api/v1/dev/guest-intake",
    "/api/v1/dev/guest-claims",
    "/api/v1/dev/account/tickets",
    "/api/v1/dev/account/summary",
    "/api/v1/dev/staff/queue",
    "/api/v1/dev/staff/audit",
    "/api/v1/staff/tickets",
    "/api/v1/staff/estimates",
    "/api/v1/staff/stock/reservations",
    "/api/v1/customer/estimates",
)


def _retired(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in RETIRED)


def test_runtime_route_registration_contains_zero_retired_robot_paths():
    paths = [route.path for route in app.routes if hasattr(route, "path")]
    assert not [path for path in paths if _retired(path)]
    assert "/health/live" in paths
    assert "/api/v1/meta" in paths
    # This FastAPI/ASGI stack can retain lazy _IncludedRouter objects in
    # app.routes, so direct .path inspection cannot prove an imported router
    # is actually reachable. Exercise HTTP in an independent Python process.
    import json
    import subprocess
    import sys
    check = subprocess.run(
        [sys.executable, "-c",
         "import json, os; "
         "os.environ['ROBOport_SYNTHETIC_AUTO_API']='1'; "
         "os.environ.pop('ROBOport_DATABASE_URL',None); "
         "from fastapi.testclient import TestClient; "
         "import roboport_api.main as m; "
         "client=TestClient(m.app,base_url='https://127.0.0.1'); "
         "car=client.get('/api/v1/dev/auto/vehicles'); "
         "old=client.get('/api/v1/dev/tickets'); "
         "print(json.dumps({'auto_status':car.status_code, "
         "'auto_code':car.json().get('code'), "
         "'legacy_status':old.status_code, "
         "'vehicle_defined':any(r.path=='/api/v1/dev/auto/vehicles' "
         "for r in m.auto_vehicle_router.routes)}))"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    report = json.loads(check.stdout)
    assert report["vehicle_defined"], report
    assert report["auto_status"] == 403, report  # Real automotive route, no DSN.
    assert report["auto_code"] == "FORBIDDEN", report
    assert report["legacy_status"] == 404, report


def test_live_openapi_contains_no_legacy_routes():
    schema = TestClient(app).get("/openapi.json").json()
    assert not [path for path in schema["paths"] if _retired(path)]


@pytest.mark.parametrize("path", [
    "/api/v1/dev/tickets",
    "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111",
    "/api/v1/dev/attachments/11111111-1111-4111-8111-111111111111/content",
    "/api/v1/dev/guest-intake",
    "/api/v1/dev/guest-claims/test/review",
    "/api/v1/dev/catalog/models/exact",
    "/api/v1/dev/inventory/reservations",
    "/api/v1/dev/staff/queue",
    "/api/v1/dev/staff/audit",
    "/api/v1/staff/tickets/test/estimates",
    "/api/v1/customer/estimates/test/approve",
])
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
def test_retired_http_urls_always_unregistered_even_with_opt_in(monkeypatch, path, method):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    client = TestClient(app, base_url="https://127.0.0.1")
    response = client.request(method, path, headers={
        "X-User-ID": "11111111-1111-4111-8111-111111111111",
        "X-Forwarded-Proto": "https",
    })
    assert response.status_code == 404


def test_runtime_source_does_not_import_archival_test_application():
    source = (ROOT / "apps/api/roboport_api/main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.ImportFrom) and
                   (node.module or "").startswith("tests") for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Import) and any(
        alias.name.startswith("tests") for alias in node.names
    ) for node in ast.walk(tree))
    assert (ROOT / "tests/legacy_app.py").exists()


def test_every_archived_robot_operation_is_unreachable_in_current_runtime():
    """Inventory frozen HTTP source + historical draft, not a hand-picked sample."""
    import json
    import re
    import subprocess
    import sys
    from scripts.inventory_legacy_routes import scan_python, scan_openapi_contract

    archived = [
        *scan_python(ROOT / "tests/legacy_app.py", ROOT),
        *scan_python(ROOT / "apps/api/roboport_api/guest_claim_http.py", ROOT),
    ]
    assert archived and not any(row["unresolved"] for row in archived)
    # These exact paths are intentionally shared, not robot business operations.
    shared = {"/health/live", "/health/ready", "/api/v1/meta",
              "/api/v1/dev/account/preferences"}
    retired = {(row["method"], row["path"]) for row in archived
               if row["path"] not in shared}
    assert retired and all(_retired(path) for _, path in retired), retired
    draft = scan_openapi_contract(ROOT / "contracts/openapi.yaml", ROOT)
    assert draft and not any(row.get("unresolved") for row in draft)
    # Historical non-auth draft is not a published API. Do not treat the
    # versioned current auth namespace as robot business functionality.
    retired.update((row["method"], row["path"]) for row in draft
                   if row["kind"] == "historical-openapi-draft"
                   and not row["path"].startswith("/api/v1/auth/"))
    assert len(retired) >= 40
    assert ("POST", "/api/v1/dev/guest-claims/{claim_id}/assign-review") in retired
    assert ("GET", "/api/v1/dev/tickets/{ticket_id}/state") in retired
    # Exercise the actual method and concretize path variables with dummy UUIDs.
    # In a fresh process no historical test application is imported/mounted.
    operations = sorted({(method, re.sub(r"\{[^{}]+\}",
                         "11111111-1111-4111-8111-111111111111", path))
                         for method, path in retired})
    assert all(method in {"GET","POST","PUT","PATCH","DELETE","HEAD","OPTIONS"}
               for method,_ in operations)
    runner = """
import json, os, sys
from fastapi.testclient import TestClient
from roboport_api.main import app
for flag in ("ACCESS","AUTH","AUTO"):
    os.environ["ROBOport_SYNTHETIC_"+flag+"_API"]="1"
os.environ.pop("ROBOport_DATABASE_URL",None)
operations=json.load(sys.stdin)
with TestClient(app,base_url="https://127.0.0.1") as client:
    failures=[]
    for method,path in operations:
        response=client.request(method,path,follow_redirects=False)
        if response.status_code!=404:
            failures.append([method,path,response.status_code])
    # No spoofed proxy header in the principal probe: a transport guard
    # must not be mistaken for proof that the business route is retired.
    schema=client.get("/openapi.json").json()["paths"]
    print(json.dumps({"failures":failures,"schema":list(schema),
        "health":client.get("/health/live").status_code,
        "auto":client.get("/api/v1/dev/auto/vehicles").status_code}))
"""
    check=subprocess.run([sys.executable,"-c",runner],
        input=json.dumps(operations),capture_output=True,text=True,check=True,timeout=60)
    result=json.loads(check.stdout)
    assert result["failures"]==[],result["failures"]
    assert result["health"]==200 and result["auto"]==403,result
    historical_paths={path for _,path in retired}
    assert not historical_paths.intersection(result["schema"])
