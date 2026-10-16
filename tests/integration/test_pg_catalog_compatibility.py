"""Real PostgreSQL catalog, exact variant and two-person review acceptance."""
from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.catalog import CatalogConflict,review_capability
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def catalog_fixture():
    brand,model,dock,variant,part,evidence=(uuid4() for _ in range(6))
    engineer,manager=uuid4(),uuid4()
    tokens,csrf=[new_token(),new_token()],[new_token(),new_token()]
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO catalog_brand(id,name) VALUES (%s,%s)",
                   (brand,"Synthetic-"+brand.hex))
        db.execute("""INSERT INTO catalog_model(id,brand_id,name,category)
                       VALUES (%s,%s,'Synthetic test vacuum','vacuum')""",(model,brand))
        db.execute("""INSERT INTO catalog_dock(id,dock_sku,region_code,hardware_revision)
                       VALUES (%s,%s,'RU','DOCK-R1')""",(dock,"DOCK-"+dock.hex))
        db.execute("""INSERT INTO catalog_variant
                       (id,model_id,manufacturer_sku,region_code,hardware_revision,dock_id)
                       VALUES (%s,%s,%s,'RU','BOT-R2',%s)""",
                   (variant,model,"BOT-"+variant.hex,dock))
        db.execute("INSERT INTO catalog_part(id,part_number,name) VALUES (%s,%s,'Test part')",
                   (part,"PN-"+part.hex))
        for uid,role,token,secret in zip((engineer,manager),("ENGINEER","MANAGER"),tokens,csrf):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)",
                       (uid,"catalog-"+uid.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES (%s,%s,%s)",
                       (uuid4(),uid,role))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
                  created_at,idle_expires_at,absolute_expires_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                  (uuid4(),uid,token_digest(token),token_digest(secret),now,
                   now+timedelta(minutes=30),now+timedelta(hours=4)))
            for action in ("capability.review","compatibility.review"):
                db.execute("""INSERT INTO catalog_grant(id,user_id,model_id,action)
                               VALUES (%s,%s,%s,%s)""",(uuid4(),uid,model,action))
        db.execute("INSERT INTO catalog_evidence(id,source_uri,sha256,created_by) VALUES (%s,%s,%s,%s)",
                   (evidence,"https://example.invalid/synthetic/"+evidence.hex,"a"*64,engineer))
    return brand,model,dock,variant,part,evidence,(engineer,manager),tokens,csrf


def test_catalog_public_reads_and_two_person_capability(monkeypatch):
    brand,model,dock,variant,part,evidence,users,tokens,csrf=catalog_fixture()
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    http=TestClient(app,base_url="https://127.0.0.1")
    unknown=http.get("/api/v1/catalog/models/"+str(uuid4()))
    assert unknown.status_code==404
    bad=http.get("/api/v1/catalog/models/not-uuid")
    assert bad.status_code==422 and bad.json()["code"]=="INVALID_INPUT"
    assert http.get("/api/v1/catalog/models?cursor=invalid").status_code==422
    before=http.get("/api/v1/catalog/models/"+str(model))
    assert before.status_code==200,before.text
    assert before.json()["supportStatus"]=="candidate"
    brands=http.get("/api/v1/catalog/brands")
    assert brands.status_code==200
    assert any(x["id"]==str(brand) for x in brands.json()["items"])
    listing=http.get("/api/v1/catalog/models?limit=1")
    assert listing.status_code==200
    assert any(x["id"]==str(model) for x in listing.json()["items"])
    path=f"/api/v1/staff/variants/{variant}/capabilities/repair"
    payload={"status":"approved","evidenceId":str(evidence)}
    def header(i,key):
        return {"Origin":"https://127.0.0.1","X-CSRF-Token":csrf[i],"Idempotency-Key":key}
    http.cookies.set("rp_session",tokens[0])
    first=http.patch(path,json=payload,headers=header(0,"cap-engineer-"+variant.hex))
    assert first.status_code==200,first.text
    assert first.json()["status"]=="candidate"
    assert http.patch(path,json=payload,headers=header(0,"cap-engineer-"+variant.hex)).json()==first.json()
    assert http.patch(path,json=payload,headers=header(0,"different-"+variant.hex)).status_code==409
    with psycopg.connect(DSN) as db:
        cap=UUID(first.json()["id"])
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE service_capability SET status='approved' WHERE id=%s",(cap,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO service_capability
                  (id,variant_id,operation_code,evidence_id,status)
                  VALUES (%s,%s,'fabricated',%s,'approved')""",(uuid4(),variant,evidence))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO part_compatibility
                  (id,part_id,variant_id,evidence_id,status)
                  VALUES (%s,%s,%s,%s,'manufacturer_documented')""",
                           (uuid4(),part,variant,evidence))
    http.cookies.clear()
    http.cookies.set("rp_session",tokens[1])
    second=http.patch(path,json=payload,headers=header(1,"cap-manager-"+variant.hex))
    assert second.status_code==200,second.text
    assert second.json()["status"]=="approved"
    assert http.get("/api/v1/catalog/models/"+str(model)).json()["supportStatus"]=="supported"
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM catalog_capability_review WHERE capability_id=%s",
                          (cap,)).fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM audit_event WHERE action='catalog.capability.review'").fetchone()[0]>=2
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM catalog_capability_review WHERE capability_id=%s",(cap,))


def test_part_compatibility_evidence_independent_roles_and_revoke(monkeypatch):
    brand,model,dock,variant,part,evidence,users,tokens,csrf=catalog_fixture()
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    http=TestClient(app,base_url="https://127.0.0.1")
    route=f"/api/v1/staff/parts/{part}/compatibilities"
    body={"modelVariantId":str(variant),"status":"manufacturer_documented","evidenceId":str(evidence)}
    headers=lambda i,key:{"Origin":"https://127.0.0.1","X-CSRF-Token":csrf[i],"Idempotency-Key":key}
    http.cookies.set("rp_session",tokens[0])
    assert http.post(route,json=body,headers=headers(0,"comp-"+part.hex)).status_code==200
    assert http.post(route,json={**body,"status":"incompatible"},headers=headers(0,"bad-status")).status_code==422
    assert http.post(route,json={**body,"evidenceId":str(uuid4())},
                     headers=headers(0,"comp-"+part.hex)).status_code==409
    http.cookies.clear()
    http.cookies.set("rp_session",tokens[1])
    accepted=http.post(route,json=body,headers=headers(1,"comp-manager-"+part.hex))
    assert accepted.status_code==200,accepted.text
    assert accepted.json()["status"]=="manufacturer_documented"
    with psycopg.connect(DSN) as db:
        cid=UUID(accepted.json()["id"])
        assert db.execute("SELECT count(*) FROM catalog_compatibility_review WHERE compatibility_id=%s",
                          (cid,)).fetchone()[0]==2
        db.execute("UPDATE catalog_grant SET revoked_at=now() WHERE user_id=%s",(users[1],))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE catalog_grant SET revoked_at=NULL WHERE user_id=%s",(users[1],))
    assert http.post(route,json=body,headers=headers(1,"comp-manager-"+part.hex)).status_code==404


def test_exact_variant_region_revision_dock_do_not_inherit_approval():
    brand,model,dock,variant,part,evidence,users,tokens,csrf=catalog_fixture()
    another_dock,variant_other=uuid4(),uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO catalog_dock(id,dock_sku,region_code,hardware_revision)
                      VALUES (%s,%s,'EU','DOCK-R2')""",(another_dock,"DOCK-"+another_dock.hex))
        db.execute("""INSERT INTO catalog_variant
                      (id,model_id,manufacturer_sku,region_code,hardware_revision,dock_id)
                      VALUES (%s,%s,%s,'EU','BOT-R3',%s)""",
                   (variant_other,model,"BOT-"+variant_other.hex,another_dock))
        a=review_capability(db,tokens[0],variant,"repair",evidence,True,"engineer-"+variant.hex)
        b=review_capability(db,tokens[1],variant,"repair",evidence,True,"manager-"+variant.hex)
        assert a["status"]=="candidate" and b["status"]=="approved"
        assert db.execute("SELECT count(*) FROM service_capability WHERE variant_id=%s",
                          (variant_other,)).fetchone()[0]==0
        assert db.execute("""SELECT count(*) FROM part_compatibility
                              WHERE variant_id=%s""",(variant_other,)).fetchone()[0]==0
