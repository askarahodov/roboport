"""PG17/HTTPS: атомарная регистрация, scoped retry, правки до proof и независимость."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario,_http,_act
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_service import grant


def setup(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,_=_org_and_bay(db)
        other,other_loc,_=_org_and_bay(db)
        role=grant(db,d["intake"],org,loc,"INTAKE")
        grant(db,d["intake"],org,loc,"MANAGER")
        grant(db,d["other"],org,loc,"INTAKE")
        grant(db,d["manager"],org,loc,"MANAGER")
    path=f"/api/v1/dev/auto/organizations/{org}/vehicle-registrations"
    payload={"locationId":str(loc),"customerId":str(d["ca"]),
             "make":"Synthetic","model":"Test","vin":"SYN"+uuid4().hex[:14].upper(),
             "variant":None,"modelYear":2026}
    return d,org,loc,other,other_loc,role,path,payload


def post(http,d,user,path,body,key=None,csrf=True):
    http.cookies.set("rp_session",d["tokens"][user])
    headers={"Origin":"https://127.0.0.1","Idempotency-Key":str(key or uuid4())}
    if csrf:
        headers["X-CSRF-Token"]=d["csrf"][user]
    return http.post(path,json=body,headers=headers)


def edit_body(payload,revision,model):
    return {**{k:v for k,v in payload.items() if k not in ("customerId","vin")},
            "expectedRevision":revision,"model":model}


def test_registration_and_independent_claim(monkeypatch):
    d,org,loc,other,other_loc,role,path,payload=setup(monkeypatch)
    with _http() as http:
        assert post(http,d,d["client_a"],path,payload).status_code==403
        assert post(http,d,d["intake"],path,payload,csrf=False).status_code==403
        assert post(http,d,d["intake"],path,{**payload,"customerId":str(uuid4())}).status_code==403
        assert post(http,d,d["intake"],path,{**payload,"locationId":str(other_loc)}).status_code==403
        assert post(http,d,d["intake"],path,{**payload,"modelYear":True}).status_code==422
        key=uuid4()
        created=post(http,d,d["intake"],path,payload,key)
        assert created.status_code==201,created.text
        assert created.headers["cache-control"]=="no-store"
        result=created.json()
        vehicle,link=result["vehicleId"],result["linkId"]
        assert set(result)=={"vehicleId","linkId","revision","status"}
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator, FormatChecker
        spec=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-vehicle-registration-dev-openapi.json").read_text(encoding="utf-8"))
        def check(name,body):
            Draft202012Validator({**spec,"$ref":"#/components/schemas/"+name},
                                 format_checker=FormatChecker()).validate(body)
        check("Receipt",result)
        assert result["revision"]==1 and result["status"]=="PENDING"
        assert post(http,d,d["intake"],path,payload,key).json()==result
        assert post(http,d,d["intake"],path,{**payload,"model":"Changed"},key).status_code==409
        assert post(http,d,d["intake"],path,payload).status_code==409
        detail=path+"/"+vehicle
        loaded=http.get(detail,params={"locationId":str(loc)})
        assert loaded.status_code==200 and loaded.json()["vinLast4"]==payload["vin"][-4:]
        assert "vin" not in loaded.json() and loaded.json()["editable"] is True
        check("Details",loaded.json())
        assert http.get(detail,params={"locationId":str(other_loc)}).status_code==403
        revised=edit_body(payload,1,"Revised")
        edit_key=uuid4()
        updated=post(http,d,d["intake"],detail+"/revisions",revised,edit_key)
        assert updated.status_code==200,updated.text
        assert updated.json()["revision"]==2
        assert post(http,d,d["intake"],detail+"/revisions",revised,edit_key).json()==updated.json()
        assert post(http,d,d["intake"],detail+"/revisions",revised).status_code==409
        assert post(http,d,d["intake"],detail+"/revisions",{**revised,"vin":payload["vin"]}).status_code==422
        with psycopg.connect(DSN) as db:
            assert db.execute("SELECT registration_revision,model FROM auto_vehicle WHERE id=%s",(vehicle,)).fetchone()==(2,"Revised")
            assert db.execute("SELECT count(*) FROM auto_vehicle_registration_event WHERE vehicle_id=%s",(vehicle,)).fetchone()==(2,)
            assert db.execute("SELECT count(*) FROM auto_vehicle_customer_link WHERE vehicle_id=%s",(vehicle,)).fetchone()==(1,)
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("UPDATE auto_vehicle SET model='Bypass' WHERE id=%s",(vehicle,))
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                               (d["manager"],link))
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("DELETE FROM auto_vehicle_registration_event WHERE vehicle_id=%s",(vehicle,))
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("TRUNCATE auto_vehicle_registration_event")
        http.cookies.set("rp_session",d["tokens"][d["client_a"]])
        assert http.get("/api/v1/dev/auto/vehicles/"+vehicle).status_code==404
        issue=_act(http,d,d["other"],f"/api/v1/dev/auto/links/{link}/issue-proof")
        assert issue.status_code==200,issue.text
        claim,proof=issue.json()["claimId"],issue.json()["proof"]
        assert post(http,d,d["intake"],detail+"/revisions",edit_body(payload,2,"Later")).status_code==409
        assert http.get(detail,params={"locationId":str(loc)}).json()["editable"] is False
        redeem=_act(http,d,d["client_a"],f"/api/v1/dev/auto/claims/{claim}/redeem",json={"proof":proof})
        assert redeem.json()["status"]=="PENDING_REVIEW",redeem.text
        review=f"/api/v1/dev/auto/claims/{claim}/review"
        assert _act(http,d,d["intake"],review,json={"approve":True}).status_code==403
        assert _act(http,d,d["manager"],review,json={"approve":True}).json()["status"]=="VERIFIED"
        http.cookies.set("rp_session",d["tokens"][d["client_a"]])
        assert http.get("/api/v1/dev/auto/vehicles/"+vehicle).status_code==200
        http.cookies.set("rp_session",d["tokens"][d["client_b"]])
        assert http.get("/api/v1/dev/auto/vehicles/"+vehicle).status_code==404
        with psycopg.connect(DSN) as db:
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(role,))
        assert post(http,d,d["intake"],path,payload,key).status_code==403
        assert http.get(detail,params={"locationId":str(loc)}).status_code==403


def test_registration_concurrent_replay_and_revision(monkeypatch):
    d,org,loc,other,other_loc,role,path,payload=setup(monkeypatch)
    key=uuid4()
    barrier=Barrier(2)
    def create(_):
        with _http() as http:
            barrier.wait(timeout=10)
            return post(http,d,d["intake"],path,payload,key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(create,range(2)))
    assert [x.status_code for x in results]==[201,201],[x.text for x in results]
    assert results[0].json()==results[1].json()
    vehicle=results[0].json()["vehicleId"]
    barrier=Barrier(2)
    def edit(number):
        with _http() as http:
            barrier.wait(timeout=10)
            return post(http,d,d["intake"],path+"/"+vehicle+"/revisions",
                        edit_body(payload,1,"Concurrent "+str(number)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        edits=list(pool.map(edit,range(2)))
    assert sorted(x.status_code for x in edits)==[200,409],[x.text for x in edits]
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_vehicle WHERE vin=%s",(payload["vin"],)).fetchone()==(1,)
        assert db.execute("SELECT count(*) FROM auto_vehicle_registration_event WHERE vehicle_id=%s",(vehicle,)).fetchone()==(2,)
        assert db.execute("SELECT count(*) FROM auto_vehicle_location_binding WHERE vehicle_id=%s",(vehicle,)).fetchone()==(1,)
