"""Guardrails for draft customer API and opt-in dev-only handlers."""
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from roboport_api.main import app

SPEC=yaml.safe_load((Path(__file__).resolve().parents[2]/"contracts/openapi.yaml").read_text())
METHODS={"get","post","put","patch","delete"}
CUSTOMER_PREFIX="/api/v1/customer/"
DEV_PREFIX="/api/v1/dev/"
CLIENT=TestClient(app)


def test_customer_and_staff_operations_are_explicitly_session_secured():
    for path,operations in SPEC["paths"].items():
        if not path.startswith(("/api/v1/customer/","/api/v1/staff/")):
            continue
        for method in METHODS.intersection(operations):
            assert {"localSession":[]} in operations[method].get(
                "security",SPEC.get("security",[])
            ),(method,path)


def test_customer_ticket_contract_contains_list_and_owned_detail():
    for path in ("/api/v1/customer/tickets","/api/v1/customer/tickets/{ticketId}"):
        assert "get" in SPEC["paths"][path]
        assert {"localSession":[]} in SPEC["paths"][path]["get"].get(
            "security",SPEC.get("security",[])
        )


def test_dev_diagnostics_are_not_advertised_in_contract_or_runtime_schema():
    assert not any(path.startswith(DEV_PREFIX) for path in SPEC["paths"])
    runtime=CLIENT.get("/openapi.json").json()
    assert not any(path.startswith(DEV_PREFIX) for path in runtime["paths"])


@pytest.mark.parametrize("path",[
    "/api/v1/customer/me",
    "/api/v1/customer/tickets",
    "/api/v1/customer/tickets/11111111-1111-4111-8111-111111111111",
    "/api/v1/customer/devices",
])
def test_customer_routes_remain_closed_for_spoofed_identity(path):
    response=CLIENT.get(path,headers={
        "X-User-ID":"11111111-1111-4111-8111-111111111111",
        "X-Customer-ID":"11111111-1111-4111-8111-111111111111",
    })
    assert response.status_code in (401,403,404)
    assert response.headers.get("cache-control","no-store")=="no-store" or response.status_code==404
    assert "customerId" not in response.text


def test_dev_routes_are_disabled_without_opt_in(monkeypatch):
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API",raising=False)
    for path in (
        "/api/v1/dev/tickets",
        "/api/v1/dev/tickets/keyset",
        "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111",
        "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111/state",
    ):
        assert CLIENT.get(path).status_code==404
