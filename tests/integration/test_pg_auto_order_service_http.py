"""PG17/HTTP: manager selects catalog services on an actual synthetic WorkOrder."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_appointment_service import create_appointment, reschedule_appointment
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_service import grant


def scenario():
    d = _scenario()
    with psycopg.connect(DSN) as conn:
        org, loc, bay = _org_and_bay(conn)
        other, other_loc, _ = _org_and_bay(conn)
        conn.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                     (d["manager"], d["link"]))
        conn.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                     (d["vehicle"],loc))
        grant(conn,d["intake"],org,loc,"INTAKE")
        manager_grant = grant(conn,d["intake"],org,loc,"MANAGER")
        service, foreign_service = uuid4(), uuid4()
        conn.execute("""INSERT INTO auto_service_catalog
          (id,organization_id,location_id,code,title,duration_minutes)
          VALUES(%s,%s,%s,'TEST-ORDER-SVC','SYNTHETIC INSPECTION',45)""",(service,org,loc))
        conn.execute("""INSERT INTO auto_service_catalog
          (id,organization_id,location_id,code,title,duration_minutes)
          VALUES(%s,%s,%s,'TEST-FOREIGN-SVC','SYNTHETIC',30)""",
          (foreign_service,other,other_loc))
    start=datetime.now(timezone.utc)+timedelta(days=4)
    with psycopg.connect(DSN) as conn:
        appointment,booking,order=create_appointment(
            conn,d["tokens"][d["intake"]],org,loc,bay,d["link"],start,
            start+timedelta(hours=1))
    return d,org,loc,bay,order,appointment,start,service,foreign_service,manager_grant


def test_order_services_http_manager_flow_replay_and_tenant_separation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,bay,order,appointment,start,service,foreign_service,grant_id=scenario()
    client=TestClient(app,base_url="https://127.0.0.1")
    base=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    catalog=f"{base}/service-catalog"
    selected=f"{base}/orders/{order}/services"
    assert client.get(catalog).status_code==403
    assert client.get(selected).status_code==403
    assert client.post(selected,json={"serviceId":str(service)}).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    own=client.get(catalog)
    assert own.status_code==200,own.text
    assert own.headers["cache-control"]=="no-store"
    assert own.json()=={"items":[{"id":str(service),"code":"TEST-ORDER-SVC",
                                 "title":"SYNTHETIC INSPECTION","durationMinutes":45}]}
    assert client.get(selected).json()=={"items":[]}
    assert client.post(selected,json={"serviceId":str(service)}).status_code==403
    headers={"Origin":"https://127.0.0.1",
             "X-CSRF-Token":d["csrf"][d["intake"]]}
    assert client.post(selected,json={"serviceId":str(foreign_service)},
                       headers=headers).status_code==403
    assert client.post(selected,json={"serviceId":"bad"},
                       headers=headers).status_code==422
    added=client.post(selected,json={"serviceId":str(service)},headers=headers)
    assert added.status_code==201,added.text
    assert added.json()["writes"]==1
    repeated=client.post(selected,json={"serviceId":str(service)},headers=headers)
    assert repeated.status_code==200 and repeated.json()["replayed"] is True
    assert repeated.json()["writes"]==0
    rows=client.get(selected)
    assert rows.status_code==200 and rows.json()["items"]==own.json()["items"]
    assert client.get(selected.replace(str(loc),str(uuid4()))).status_code==403
    assert client.get(selected.replace(str(order),str(uuid4()))).status_code==403
    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s",
                            (order,)).fetchone()[0]==1
        conn.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                     (d["link"],))
    assert client.get(selected).status_code==403
    assert client.post(selected,json={"serviceId":str(service)},headers=headers).status_code==403
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                     (grant_id,))
    assert client.get(catalog).status_code==403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.get(catalog).status_code==404


def test_order_service_after_reschedule_uses_current_booking(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,bay,order,appointment,start,service,foreign_service,_=scenario()
    with psycopg.connect(DSN) as conn:
        reschedule_appointment(conn,d["tokens"][d["intake"]],org,appointment,bay,
                               start+timedelta(hours=2),start+timedelta(hours=3))
    client=TestClient(app,base_url="https://127.0.0.1")
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    path=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/services"
    headers={"Origin":"https://127.0.0.1",
             "X-CSRF-Token":d["csrf"][d["intake"]]}
    assert client.get(path).status_code==200
    response=client.post(path,json={"serviceId":str(service)},headers=headers)
    assert response.status_code==201,response.text
    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s",
                            (order,)).fetchone()[0]==1
