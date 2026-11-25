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
    due=today+timedelta(days=14)
    queue=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/maintenance-queue"
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
    queued=http.get(queue)
    assert queued.status_code==200,queued.text
    assert [r["recommendationId"] for r in queued.json()["items"]]==[item["id"]]
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
    assert http.get(queue).json()["items"]==[]
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
    assert http.get(queue).status_code==403
    token(http,d,d["intake"])
    assert http.get(queue).json()["items"]==[]
    customer=token(http,d,d["client_a"])
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
    token(http,d,d["intake"])
    assert [r["recommendationId"] for r in http.get(queue).json()["items"]]==[active["id"]]
    customer=token(http,d,d["client_a"])
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


def test_branch_maintenance_queue_pagination_local_date_and_live_document_policy(monkeypatch):
    from test_pg_auto_document_access_http import admin,set_rule,url as policy_url
    from roboport_api import auto_maintenance_queue
    from zoneinfo import ZoneInfo
    d,org,loc,order,service,foreign,_=scenario()
    http=api(monkeypatch)
    path=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/maintenance-queue"
    assert http.get(path).status_code==403
    with psycopg.connect(DSN) as db:
        admin(db,org,d["manager"])
        submit_work_order_inspection(db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=41700,fuel_percent=65,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="CLEAR")
        tz=db.execute("SELECT timezone_name FROM auto_service_location WHERE id=%s",(loc,)).fetchone()[0]
        local_day=datetime.now(ZoneInfo(tz)).date()
        due=datetime.now(timezone.utc).date()+timedelta(days=14)
        for n in range(27):
            service_id=uuid4()
            db.execute("""INSERT INTO auto_service_catalog
              (id,organization_id,location_id,code,title,duration_minutes)
              VALUES(%s,%s,%s,%s,%s,30)""",(service_id,org,loc,f"QUEUE-{n:02}","SYNTHETIC QUEUE"))
            create_recommendation(db,d["tokens"][d["intake"]],org,loc,order,key=uuid4(),
                                  service=service_id,reason="PERIODIC",due=due,km=50000)
    token(http,d,d["intake"])
    first=http.get(path,params={"mode":"UPCOMING","horizon":"30"})
    assert first.status_code==200,first.text
    page=first.json()
    assert first.headers["cache-control"]=="no-store"
    assert page["localDay"]==local_day.isoformat() and page["timeZone"]==tz
    assert page["sendAllowed"] is False and len(page["items"])==25
    assert all(x["workOrderId"]==str(order) and x["status"]=="UPCOMING" for x in page["items"])
    assert str(d["link"]) not in first.text and str(d["client_a"]) not in first.text
    cursor=page["nextCursor"]
    second=http.get(path,params={"mode":"UPCOMING","horizon":"30","cursor":cursor})
    assert second.status_code==200,second.text
    assert len(second.json()["items"])==2 and second.json()["nextCursor"] is None
    assert len({x["recommendationId"] for x in page["items"]+second.json()["items"]})==27
    assert http.get(path,params={"mode":"ALL","cursor":cursor}).status_code==422
    assert http.get(path,params={"horizon":"7"}).json()["items"]==[]
    assert http.get(path,params={"reasonCode":"DEFERRED"}).json()["items"]==[]
    assert len(http.get(path,params={"reasonCode":"PERIODIC"}).json()["items"])==25
    assert http.get(path,params={"mode":"UPCOMING","horizon":"30","reasonCode":"PERIODIC","cursor":cursor}).status_code==422
    assert http.get(path+"?mode=ALL&mode=OVERDUE").status_code==422
    assert http.get(path.replace(str(loc),str(uuid4()))).status_code==403
    class FutureClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime.combine(due+timedelta(days=1),datetime.min.time(),tzinfo=tz)
    monkeypatch.setattr(auto_maintenance_queue,"datetime",FutureClock)
    overdue=http.get(path,params={"mode":"OVERDUE"})
    assert overdue.status_code==200,overdue.text
    assert len(overdue.json()["items"])==25 and all(x["status"]=="OVERDUE" for x in overdue.json()["items"])
    assert http.get(path,params={"mode":"UPCOMING","horizon":"30","cursor":cursor}).status_code==422
    assert http.get(path,params={"mode":"UPCOMING"}).json()["items"]==[]
    owner=token(http,d,d["manager"])
    deny=set_rule(http,policy_url(org),owner,role="MANAGER",loc=loc,user=d["intake"],
                  document="INTAKE_ACT",cap="READ",allowed=False)
    assert deny.status_code==201,deny.text
    token(http,d,d["intake"])
    assert http.get(path).status_code==403
    owner=token(http,d,d["manager"])
    cutoff=set_rule(http,policy_url(org),owner,role="MANAGER",loc=loc,user=d["intake"],
                    document="INTAKE_ACT",cap="READ",allowed=True,not_before="2099-01-01T00:00:00Z")
    assert cutoff.status_code==201,cutoff.text
    token(http,d,d["intake"])
    assert http.get(path).json()["items"]==[]
    owner=token(http,d,d["manager"])
    restored=set_rule(http,policy_url(org),owner,role="MANAGER",loc=loc,user=d["intake"],
                      document="INTAKE_ACT",cap="READ",allowed=True)
    assert restored.status_code==201,restored.text
    token(http,d,d["intake"])
    assert len(http.get(path).json()["items"])==25
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert http.get(path).json()["items"]==[]


def test_recommendation_direct_routes_follow_intake_read_cutoff_and_write(monkeypatch):
    from test_pg_auto_document_access_http import admin, set_rule, url
    d,org,loc,order,service,_,_=scenario()
    http=api(monkeypatch)
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    path=root+f"/orders/{order}/recommendations"
    queue=root+"/crm/maintenance-queue"
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
        submit_work_order_inspection(db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=41700,fuel_percent=65,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="CLEAR")
    def policy(cap,allowed=False,cutoff=None):
        owner=token(http,d,d["other"])
        result=set_rule(http,url(org),owner,loc=loc,user=d["intake"],
            document="INTAKE_ACT",cap=cap,allowed=allowed,not_before=cutoff)
        assert result.status_code==201,result.text
    body={"requestKey":str(uuid4()),"serviceId":str(service),
          "reasonCode":"PERIODIC","dueDate":(datetime.now(timezone.utc).date()+
          timedelta(days=14)).isoformat(),"dueKm":50000}
    manager=token(http,d,d["intake"])
    created=http.post(path,headers=manager,json=body)
    assert created.status_code==201,created.text
    item=created.json()["items"][0]
    assert item["baselineKm"]==41700
    withdraw=path+"/"+item["id"]+"/withdraw"
    cancel={"requestKey":str(uuid4())}
    policy("READ")
    manager=token(http,d,d["intake"])
    for response in (http.get(path),http.get(path+"/services"),http.get(queue),
                     http.post(path,headers=manager,json=body),
                     http.post(withdraw,headers=manager,json=cancel)):
        assert response.status_code==403,response.text
        assert response.headers["cache-control"]=="no-store"
        assert "41700" not in response.text
    policy("READ",True,"2099-01-01T00:00:00Z")
    manager=token(http,d,d["intake"])
    assert http.get(path).status_code==403
    assert http.get(queue).json()["items"]==[]
    assert http.post(path,headers=manager,json=body).status_code==403
    policy("READ",True)
    policy("WRITE")
    manager=token(http,d,d["intake"])
    snapshot=http.get(path)
    assert snapshot.status_code==200,snapshot.text
    assert snapshot.json()["canManage"] is False
    assert snapshot.json()["services"]==[]
    assert snapshot.json()["items"]==created.json()["items"]
    assert http.get(path+"/services").status_code==403
    assert http.post(path,headers=manager,json=body).status_code==403
    assert http.post(withdraw,headers=manager,json=cancel).status_code==403
    token(http,d,d["client_a"])
    assert http.get(path+"/customer").json()["items"][0]["id"]==item["id"]
    token(http,d,d["client_b"])
    assert http.get(path+"/customer").status_code==403
    policy("WRITE",True)
    manager=token(http,d,d["intake"])
    assert http.get(path).json()["canManage"] is True
    assert http.post(path,headers=manager,json=body).json()["replayed"] is True
    withdrawn=http.post(withdraw,headers=manager,json=cancel)
    assert withdrawn.status_code==201,withdrawn.text
    policy("WRITE")
    manager=token(http,d,d["intake"])
    assert http.post(withdraw,headers=manager,json=cancel).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_service_recommendation WHERE work_order_id=%s",
                          (order,)).fetchone()==(1,)
        assert db.execute("SELECT count(*) FROM auto_service_recommendation_withdrawal WHERE recommendation_id=%s",
                          (item["id"],)).fetchone()==(1,)
