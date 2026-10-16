"""Staff-only synthetic access gate: no default access or role spoofing."""
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api import auth_http


def test_staff_access_off_by_default(monkeypatch):
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTH_API", raising=False)
    client = TestClient(app, base_url="https://127.0.0.1")
    assert client.get("/api/v1/dev/staff/access").status_code == 403


def test_staff_access_rejects_http_and_remote_hosts(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", "postgresql://127.0.0.1:5432/demo")
    assert TestClient(app,base_url="http://127.0.0.1").get(
        "/api/v1/dev/staff/access").status_code == 404
    assert TestClient(app,base_url="https://example.com").get(
        "/api/v1/dev/staff/access").status_code == 404


def test_staff_access_denies_anonymous_and_unassigned(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", "postgresql://127.0.0.1:5432/demo")
    class Conn:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def execute(self,sql,params):
            assert "revoked_at IS NULL" in sql
            assert "expires_at IS NULL" in sql
            return self
        def fetchall(self): return []
    monkeypatch.setattr(auth_http.psycopg,"connect",lambda *args,**kwargs:Conn())
    monkeypatch.setattr(auth_http,"session_subject",lambda conn,token: None if not token else "test-user")
    client=TestClient(app,base_url="https://127.0.0.1")
    assert client.get("/api/v1/dev/staff/access").status_code==401
    response=client.get("/api/v1/dev/staff/access",cookies={"rp_session":"synthetic"})
    assert response.status_code==403
    assert response.headers["Cache-Control"]=="no-store"


def test_staff_access_returns_only_live_roles(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", "postgresql://127.0.0.1:5432/demo")
    class Conn:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def execute(self,sql,params):
            assert "role_code IN" in sql
            assert "CLIENT" not in sql
            return self
        def fetchall(self): return [("STOCK",)]
    monkeypatch.setattr(auth_http.psycopg,"connect",lambda *args,**kwargs:Conn())
    monkeypatch.setattr(auth_http,"session_subject",lambda conn,token:"test-user")
    response=TestClient(app,base_url="https://127.0.0.1").get(
        "/api/v1/dev/staff/access",cookies={"rp_session":"synthetic"})
    assert response.status_code==200
    assert response.json()=={"roles":["STOCK"]}
    assert response.headers["Cache-Control"]=="no-store"
