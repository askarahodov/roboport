"""Regression gate for ticket/capability public boundary (synthetic data only)."""
from uuid import uuid4
from fastapi.testclient import TestClient
from roboport_api.main import app

client=TestClient(app)

def test_ticket_state_cannot_be_overwritten_by_generic_patch():
    ticket=str(uuid4())
    # Never expose free-form state mutations even when an ID is guessed.
    for path in (f"/api/v1/dev/tickets/{ticket}",f"/api/v1/dev/tickets/{ticket}/transition"):
        response=client.patch(path,json={"state":"COMPLETED","version":1})
        assert response.status_code in (404,405)
    assert client.put(f"/api/v1/dev/tickets/{ticket}",json={"state":"READY_FOR_HANDOVER"}).status_code in (404,405)

def test_capability_reviews_cannot_be_issued_without_session():
    ident=str(uuid4())
    for target in ("capabilities","parts"):
        response=client.post(f"/api/v1/dev/catalog/{target}/{ident}/review",json={"approved":True})
        assert response.status_code in (401,403,404)
        assert "SUPPORTED" not in response.text

def test_catalog_exact_identity_requires_complete_variant():
    response=client.get("/api/v1/dev/catalog/models/exact",
        params={"brand":"Demo","model":"Robot","variant":"V1","dock":"D1","region":"","revision":"R1"})
    assert response.status_code in (404,422)
