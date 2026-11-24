"""PG17: план следующего ТО, корректировка, собственная история клиента."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_workshop_qc import submit_work_order_inspection
from roboport_api.auto_service_recommendations import create_recommendation,RecommendationConflict
from test_pg_auto_work_order_estimate_http import scenario,api,token,path as estimate_path,publish_payload
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def test_plan_from_inspection_to_customer_and_corrected_recommendation(monkeypatch):
    d,org,loc,order,service,foreign,manager_grant=scenario()
    http=api(monkeypatch)
    path=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/recommendations"
    today=datetime.now(timezone.utc).date()
    due=today+timedelta(days=180)
    payload={"requestKey":str(uuid4()),"serviceId":str(service),
             "reasonCode":"PERIODIC","dueDate":due.isoformat(),"dueKm":50000}
    assert http.get(path).status_code==403
    manager=token(http,d,d["intake"])
    assert http.get(path).json()["baselineKm"] is None
    assert http.post(path,headers=manager,json=payload).status_code==409
    assert http.post(path,json=payload).status_code==403
    with psycopg.connect(DSN) as db:
        submit_work_order_inspection(db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=41700,fuel_percent=65,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="CLEAR")
        grant(db,d["manager"],org,loc,"MECHANIC")
    mechanic=token(http,d,d["manager"])
    readonly=http.get(path)
    assert readonly.status_code==200 and readonly.json()["canManage"] is False
    assert readonly.json()["services"]==[]
    assert http.post(path,headers=mechanic,json=payload).status_code==403
    manager=token(http,d,d["intake"])
    assert http.post(path,headers=manager,json={**payload,"dueDate":today.isoformat()}).status_code==422
    assert http.post(path,headers=manager,json={**payload,"dueKm":41700}).status_code==422
    assert http.post(path,headers=manager,json={**payload,"serviceId":str(foreign)}).status_code==403
    quote=http.post(estimate_path(org,loc,order),headers=manager,json=publish_payload(service))
    assert quote.status_code==201,quote.text
    original_quote=quote.json()["items"]
    created=http.post(path,headers=manager,json=payload)
    assert created.status_code==201,created.text
    assert created.headers["cache-control"]=="no-store"
    state=created.json()
    assert state["canManage"] and state["sendAllowed"] is False and state["releaseAllowed"] is False
    item=state["items"][0]
    assert item["baselineKm"]==41700 and item["dueKm"]==50000
    assert item["dueDate"]==due.isoformat() and item["status"]=="ACTIVE"
    assert item["actorId"]==str(d["intake"])
    assert http.post(path,headers=manager,json=payload).json()["replayed"] is True
    assert http.post(path,headers=manager,json={**payload,"dueKm":51000}).status_code==409
    assert http.post(path,headers=manager,json={**payload,"requestKey":str(uuid4())}).status_code==409
    # Рекомендация не меняет уже опубликованную смету, исполнение или бронь.
    assert http.get(estimate_path(org,loc,order)).json()["items"]==original_quote
    customer=token(http,d,d["client_a"])
    visible=http.get(path+"/customer")
    assert visible.status_code==200,visible.text
    assert visible.headers["cache-control"]=="no-store"
    assert not visible.json()["canManage"] and visible.json()["services"]==[]
    row=visible.json()["items"][0]
    assert row["id"]==item["id"] and "actorId" not in row and "withdrawnBy" not in row
    assert http.get(path).status_code==403
    assert http.post(path,headers=customer,json=payload).status_code==403
    token(http,d,d["client_b"])
    assert http.get(path+"/customer").status_code==403
    assert http.get(path.replace(str(loc),str(uuid4()))+"/customer").status_code==403
    manager=token(http,d,d["intake"])
    # Большой неизменяемый каталог не блокирует чтение, отзыв или выбор.
    with psycopg.connect(DSN) as db:
        for i in range(101):
            db.execute("""INSERT INTO auto_service_catalog
              (id,organization_id,location_id,code,title,duration_minutes)
              VALUES(%s,%s,%s,%s,%s,30)""",
              (uuid4(),org,loc,f"AAA-{i:03}","SYNTHETIC EXTRA"))
    large=http.get(path)
    assert large.status_code==200 and large.json()["servicesTruncated"] is True
    assert len(large.json()["services"])==100 and large.json()["items"][0]["id"]==item["id"]
    found=http.get(path+"/services",params={"q":"test-order-svc"})
    assert found.status_code==200,found.text
    assert found.json()["items"]==[{"id":str(service),"code":"TEST-ORDER-SVC","title":"SYNTHETIC INSPECTION"}]
    assert found.json()["truncated"] is False
    assert http.get(path+"/services?q=a&q=b").status_code==422
    withdraw=path+"/"+item["id"]+"/withdraw"
    cancel={"requestKey":str(uuid4())}
    assert http.post(withdraw,json=cancel).status_code==403
    result=http.post(withdraw,headers=manager,json=cancel)
    assert result.status_code==201,result.text
    assert result.json()["items"][0]["status"]=="WITHDRAWN"
    assert http.post(withdraw,headers=manager,json=cancel).json()["replayed"] is True
    assert http.post(withdraw,headers=manager,json={"requestKey":str(uuid4())}).status_code==409
    corrected={**payload,"requestKey":str(uuid4()),"dueKm":52000}
    result=http.post(path,headers=manager,json=corrected)
    assert result.status_code==201,result.text
    assert {r["status"] for r in result.json()["items"]}=={"ACTIVE","WITHDRAWN"}
    customer=token(http,d,d["client_a"])
    assert len(http.get(path+"/customer").json()["items"])==2
    active=next(r for r in result.json()["items"] if r["status"]=="ACTIVE")
    visit_path=path+"/"+active["id"]+"/visit"
    assert http.get(path+"/"+item["id"]+"/visit").status_code==409
    assert http.get(path+"/services",params={"q":"SVC"}).status_code==403
    options=http.get(visit_path)
    assert options.status_code==200,options.text
    bay=options.json()["bays"][0]["id"]
    windows=http.get(visit_path,params={"bayId":bay,"localDay":due.isoformat()})
    assert windows.status_code==200,windows.text
    slot=windows.json()["items"][0]
    booking={"requestKey":str(uuid4()),"bayId":bay,**slot}
    assert http.post(visit_path,json=booking).status_code==403
    token(http,d,d["client_b"])
    assert http.get(visit_path).status_code==403
    assert http.post(visit_path,headers=token(http,d,d["client_b"]),json=booking).status_code==403
    customer=token(http,d,d["client_a"])
    made=http.post(visit_path,headers=customer,json=booking)
    assert made.status_code==201,made.text
    visit=made.json()["visit"]
    assert visit["status"]=="SCHEDULED" and visit["workOrderId"]!=str(order)
    replay=http.post(visit_path,headers=customer,json=booking)
    assert replay.status_code==200 and replay.json()["visit"]==visit
    assert http.post(visit_path,headers=customer,json={**booking,"requestKey":str(uuid4())}).status_code==409
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT service_id,selection_origin FROM auto_work_order_service
          WHERE work_order_id=%s""",(visit["workOrderId"],)).fetchall()==[(service,"BOOKING")]
        assert db.execute("SELECT count(*) FROM auto_recommendation_visit WHERE recommendation_id=%s",
                          (active["id"],)).fetchone()==(1,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_recommendation_visit WHERE recommendation_id=%s",(active["id"],))
    # Отмена через существующий кабинет сохраняет происхождение и разрешает новый визит.
    canceled=http.post("/api/v1/dev/auto/customer/booking/appointments/"+visit["appointmentId"]+"/cancel",
        headers={**customer,"Idempotency-Key":str(uuid4())},json={"organizationId":str(org)})
    assert canceled.status_code==200,canceled.text
    current=next(r for r in http.get(path+"/customer").json()["items"] if r["id"]==active["id"])
    assert current["visit"]["status"]=="CANCELED"
    assert http.post(visit_path,headers=customer,json=booking).status_code==409
    new_booking={**booking,"requestKey":str(uuid4())}
    made_again=http.post(visit_path,headers=customer,json=new_booking)
    assert made_again.status_code==201,made_again.text
    assert made_again.json()["visit"]["appointmentId"]!=visit["appointmentId"]
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_recommendation_visit WHERE recommendation_id=%s",
                          (active["id"],)).fetchone()==(2,)
        assert db.execute("SELECT count(*) FROM auto_service_recommendation WHERE work_order_id=%s",
                          (order,)).fetchone()==(2,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_service_recommendation SET due_km=99999 WHERE id=%s",(item["id"],))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_service_recommendation_withdrawal WHERE recommendation_id=%s",(item["id"],))
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert http.get(path+"/customer").status_code==403
    assert http.get(visit_path).status_code==403
    assert http.post(visit_path,headers=customer,json=new_booking).status_code==403
    manager=token(http,d,d["intake"])
    assert http.get(path).status_code==403
    assert http.post(path,headers=manager,json=corrected).status_code==403


def test_two_managers_cannot_publish_duplicate_active_plan(monkeypatch):
    d,org,loc,order,service,_,_=scenario()
    with psycopg.connect(DSN) as db:
        submit_work_order_inspection(db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=41700,fuel_percent=65,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="CLEAR")
        grant(db,d["manager"],org,loc,"MANAGER")
    barrier=Barrier(2)
    due=datetime.now(timezone.utc).date()+timedelta(days=180)
    def publish(actor):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                create_recommendation(db,d["tokens"][actor],org,loc,order,key=uuid4(),
                    service=service,reason="PERIODIC",due=due,km=50000)
            return "CREATED"
        except RecommendationConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish,(d["intake"],d["manager"])))==["CONFLICT","CREATED"]
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_service_recommendation WHERE work_order_id=%s",
                          (order,)).fetchone()==(1,)
