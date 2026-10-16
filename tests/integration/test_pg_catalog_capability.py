"""Real PostgreSQL approval and exact-match catalog safety checks."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
import pytest
from roboport_api.catalog_approval import lookup_model,approve_capability,CatalogConflict,CatalogForbidden
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN

def test_catalog_exact_variant_dock_region_revision_and_two_reviewers():
    model,cap,engineer,manager,dual=(uuid4() for _ in range(5))
    now=datetime.now(timezone.utc)
    tokens=[new_token() for _ in range(3)]
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO catalog_model
         (id,brand,model,variant,dock,region,revision)
         VALUES (%s,'Brand','Robot','VARIANT-1','DOCK-A','RU','R2')""",(model,))
        db.execute("""INSERT INTO service_capability(id,model_id,capability,evidence)
                      VALUES(%s,%s,'REPAIR','Synthetic evidence pointer')""",(cap,model))
        for actor,role,token in zip((engineer,manager,dual),("ENGINEER","MANAGER","ENGINEER"),tokens):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)",(actor,actor.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                       (uuid4(),actor,role))
            db.execute("""INSERT INTO user_session
             (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
             VALUES(%s,%s,%s,%s,%s,%s,%s)""",
             (uuid4(),actor,token_digest(token),token_digest(new_token()),now,
              now+timedelta(minutes=30),now+timedelta(hours=4)))
        assert lookup_model(db,brand="Brand",model="Robot",variant="VARIANT-1",
                            dock="DOCK-A",region="RU",revision="R2")["status"]=="CANDIDATE"
        for changed in ("dock","region","revision","variant"):
            opts=dict(brand="Brand",model="Robot",variant="VARIANT-1",dock="DOCK-A",
                      region="RU",revision="R2")
            opts[changed]="OTHER"
            assert lookup_model(db,**opts) is None
        with pytest.raises(ValueError):
            lookup_model(db,brand="",model="Robot",variant="v",dock="d",region="RU",revision="R2")
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE service_capability SET status='SUPPORTED' WHERE id=%s",(cap,))
        with pytest.raises(CatalogConflict):
            approve_capability(db,tokens[0],cap,True)
        db.execute("UPDATE catalog_model SET status='SUPPORTED' WHERE id=%s",(model,))
        assert approve_capability(db,tokens[0],cap,True)["status"]=="CANDIDATE"
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE service_capability SET status='SUPPORTED' WHERE id=%s",(cap,))
        with pytest.raises(CatalogConflict):
            approve_capability(db,tokens[0],cap,True)
        assert approve_capability(db,tokens[1],cap,True)["status"]=="SUPPORTED"
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM capability_approval WHERE capability_id=%s",(cap,))
        assert db.execute("SELECT count(*) FROM catalog_audit WHERE capability_id=%s",(cap,)).fetchone()[0]==2

def test_rejected_capability_remains_unsupported():
    model,cap,user=uuid4(),uuid4(),uuid4()
    tok=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO catalog_model(id,brand,model,variant,dock,region,revision,status)
          VALUES (%s,'B','M','V','D','RU','R1','SUPPORTED')""",(model,))
        db.execute("""INSERT INTO service_capability(id,model_id,capability,evidence)
          VALUES (%s,%s,'DIAGNOSTIC','synthetic')""",(cap,model))
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(user,user.hex))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES (%s,%s,'ENGINEER')",(uuid4(),user))
        db.execute("""INSERT INTO user_session
          (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s)""",
          (uuid4(),user,token_digest(tok),token_digest(new_token()),now,
           now+timedelta(minutes=30),now+timedelta(hours=4)))
        assert approve_capability(db,tok,cap,False)["status"]=="UNSUPPORTED"
        with pytest.raises(CatalogConflict):
            approve_capability(db,tok,cap,True)


def test_catalog_http_exact_and_live_review(monkeypatch):
    from fastapi.testclient import TestClient
    from roboport_api.main import app
    model,cap,engineer,manager=(uuid4() for _ in range(4))
    tokens=(new_token(),new_token())
    csrf=(new_token(),new_token())
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO catalog_model
         (id,brand,model,variant,dock,region,revision,status)
         VALUES (%s,'Brand2','Robot2','V1','DOCK1','RU','R1','SUPPORTED')""",(model,))
        db.execute("""INSERT INTO service_capability(id,model_id,capability,evidence)
          VALUES (%s,%s,'REPAIR','synthetic evidence')""",(cap,model))
        for actor,role,tok,secret in zip((engineer,manager),("ENGINEER","MANAGER"),tokens,csrf):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)",(actor,actor.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES (%s,%s,%s)",(uuid4(),actor,role))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
                         created_at,idle_expires_at,absolute_expires_at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                       (uuid4(),actor,token_digest(tok),token_digest(secret),now,
                        now+timedelta(minutes=30),now+timedelta(hours=4)))
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    http=TestClient(app,base_url="https://127.0.0.1")
    q={"brand":"Brand2","model":"Robot2","variant":"V1","dock":"DOCK1","region":"RU","revision":"R1"}
    assert http.get("/api/v1/dev/catalog/models/exact",params=q).status_code==200
    assert http.get("/api/v1/dev/catalog/models/exact",params={**q,"revision":"R2"}).status_code==404
    assert http.get("/api/v1/dev/catalog/models/exact",params={**q,"region":""}).status_code==422
    route=f"/api/v1/dev/catalog/capabilities/{cap}/review"
    assert http.post(route,json={"approved":True}).status_code==401
    http.cookies.set("rp_session",tokens[0])
    headers={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf[0]}
    assert http.post(route,json={"approved":True},headers={**headers,"X-CSRF-Token":"invalid"}).status_code==403
    first=http.post(route,json={"approved":True},headers=headers)
    assert first.status_code==200 and first.json()["status"]=="CANDIDATE",first.text
    http.cookies.clear()
    http.cookies.set("rp_session",tokens[1])
    second=http.post(route,json={"approved":True},headers={**headers,"X-CSRF-Token":csrf[1]})
    assert second.status_code==200 and second.json()["status"]=="SUPPORTED",second.text
