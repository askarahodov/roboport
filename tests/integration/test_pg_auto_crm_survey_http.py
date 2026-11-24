"""PG17 real closed-order survey, exact replay, race, privacy and reporting."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_crm_leads import create_lead,transition_lead
from test_pg_auto_crm_outcomes_http import _posted
from test_pg_auto_document_lifecycle_http import ready,session
from test_pg_baseline import DSN


def test_full_survey_closed_order_to_customer_response_and_manager_report(monkeypatch):
    d,org,loc,order,act,preview=ready(monkeypatch)
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    http=TestClient(app,base_url="https://127.0.0.1")
    manager=session(http,d,d["intake"])
    with psycopg.connect(DSN) as db:
        appointment=db.execute("SELECT appointment_id FROM auto_work_order WHERE id=%s",(order,)).fetchone()[0]
        lead=UUID(create_lead(db,d["tokens"][d["intake"]],org,loc,d["link"],"WEB",uuid4())["leadId"])
        transition_lead(db,d["tokens"][d["intake"]],org,loc,lead,uuid4(),"QUALIFIED",None,None)
        transition_lead(db,d["tokens"][d["intake"]],org,loc,lead,uuid4(),"BOOKED",None,appointment)
    staff=root+f"/crm/leads/{lead}/survey"
    customer=root+f"/orders/{order}/survey/customer"
    payload={"requestKey":str(uuid4()),"action":"INVITE"}
    assert http.get(staff).json()["eligible"] is False
    assert http.post(staff,json=payload,headers=manager).status_code==409
    with psycopg.connect(DSN) as db:
        _posted(db,d,org,loc,order,act,preview,"POSTED",d["intake"])
        _posted(db,d,org,loc,order,act,preview,"CLIENT_ACK",d["client_a"])
        _posted(db,d,org,loc,order,act,preview,"CLOSED_TECHNICAL",d["intake"])
    assert http.post(staff,json=payload).status_code==403
    made=http.post(staff,json=payload,headers=manager)
    assert made.status_code==201,made.text
    assert made.json()["survey"]["status"]=="OPEN"
    ident=UUID(made.json()["survey"]["id"])
    assert made.headers["cache-control"]=="no-store"
    assert http.post(staff,json=payload,headers=manager).json()["replayed"] is True
    assert http.post(staff,json={**payload,"requestKey":str(uuid4())},headers=manager).status_code==409
    session(http,d,d["client_b"])
    assert http.get(customer).status_code==403
    answer={"requestKey":str(uuid4()),"rating":4,"reasonCode":"TIMING"}
    assert http.post(customer,json=answer,headers=session(http,d,d["client_b"])).status_code==403
    client=session(http,d,d["client_a"])
    assert http.get(customer).json()["survey"]["id"]==str(ident)
    assert http.post(customer,json={**answer,"rating":True},headers=client).status_code==422
    assert http.post(customer,json={**answer,"comment":"NOT PERMITTED"},headers=client).status_code==422
    assert http.post(customer,json=answer).status_code==403
    barrier=Barrier(2)
    def send():
        with TestClient(app,base_url="https://127.0.0.1") as local:
            headers=session(local,d,d["client_a"])
            barrier.wait(timeout=10)
            response=local.post(customer,json=answer,headers=headers)
            return response.status_code,response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(lambda _:send(),range(2)))
    assert sorted(status for status,_ in result)==[200,201],result
    assert all(row["survey"]["rating"]==4 and row["survey"]["status"]=="ANSWERED" for _,row in result)
    assert http.post(customer,json={**answer,"rating":1},headers=client).status_code==409
    manager=session(http,d,d["intake"])
    assert http.post(staff,json={"requestKey":str(uuid4()),"action":"CANCEL"},headers=manager).status_code==409
    day=datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    report=root+"/crm/survey-report"
    params={"fromDay":day,"toDay":day}
    got=http.get(report,params=params)
    assert got.status_code==200,got.text
    assert got.json()["total"]==1 and got.json()["averageHundredths"]==400
    assert got.json()["ratings"]["4"]==1 and got.json()["reasons"]["TIMING"]==1
    assert got.json()["statuses"]=={"OPEN":0,"ANSWERED":1,"CANCELED":0,"EXPIRED":0}
    assert str(ident) not in got.text and str(order) not in got.text and str(d["client_a"]) not in got.text
    assert got.json()["sendAllowed"] is False
    assert http.get(report,params={**params,"fromDay":"2020-01-01"}).status_code==422
    session(http,d,d["client_a"])
    assert http.get(report,params=params).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_crm_survey_result WHERE survey_id=%s",(ident,)).fetchone()==(1,)
        with pytest.raises(psycopg.errors.RaiseException),db.transaction():
            db.execute("UPDATE auto_crm_survey_result SET rating=1 WHERE survey_id=%s",(ident,))
        with pytest.raises(psycopg.errors.RaiseException),db.transaction():
            db.execute("DELETE FROM auto_crm_survey WHERE id=%s",(ident,))
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert http.get(customer).status_code==403
    assert http.post(customer,json=answer,headers=client).status_code==403
    session(http,d,d["intake"])
    assert http.get(staff).status_code==403
    assert http.get(report,params=params).json()["total"]==0


def test_canceled_survey_never_accepts_response_and_exact_cancel_replay(monkeypatch):
    d,org,loc,order,act,preview=ready(monkeypatch)
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    with psycopg.connect(DSN) as db:
        appointment=db.execute("SELECT appointment_id FROM auto_work_order WHERE id=%s",(order,)).fetchone()[0]
        lead=UUID(create_lead(db,d["tokens"][d["intake"]],org,loc,d["link"],"PHONE",uuid4())["leadId"])
        transition_lead(db,d["tokens"][d["intake"]],org,loc,lead,uuid4(),"QUALIFIED",None,None)
        transition_lead(db,d["tokens"][d["intake"]],org,loc,lead,uuid4(),"BOOKED",None,appointment)
        _posted(db,d,org,loc,order,act,preview,"POSTED",d["intake"])
        _posted(db,d,org,loc,order,act,preview,"CLIENT_ACK",d["client_a"])
        _posted(db,d,org,loc,order,act,preview,"CLOSED_TECHNICAL",d["intake"])
    http=TestClient(app,base_url="https://127.0.0.1")
    staff=root+f"/crm/leads/{lead}/survey"
    customer=root+f"/orders/{order}/survey/customer"
    manager=session(http,d,d["intake"])
    assert http.post(staff,json={"requestKey":str(uuid4()),"action":"INVITE"},headers=manager).status_code==201
    cancel={"requestKey":str(uuid4()),"action":"CANCEL"}
    done=http.post(staff,json=cancel,headers=manager)
    assert done.status_code==201,done.text
    assert done.json()["survey"]["status"]=="CANCELED"
    assert http.post(staff,json=cancel,headers=manager).json()["replayed"] is True
    assert http.post(staff,json={**cancel,"requestKey":str(uuid4())},headers=manager).status_code==409
    client=session(http,d,d["client_a"])
    assert http.get(customer).json()["survey"]["status"]=="CANCELED"
    assert http.post(customer,json={"requestKey":str(uuid4()),"rating":5,"reasonCode":"NONE"},headers=client).status_code==409
    session(http,d,d["intake"])
    day=datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    report=http.get(root+"/crm/survey-report",params={"fromDay":day,"toDay":day}).json()
    assert report["statuses"]["CANCELED"]==1 and report["averageHundredths"] is None
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code IN ('MANAGER','INTAKE')""",(org,d["intake"]))
    assert http.post(staff,json=cancel,headers=manager).status_code==403
