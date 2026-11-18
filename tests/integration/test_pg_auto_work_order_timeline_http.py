"""PG17/HTTPS immutable evidence chronology of one staff WorkOrder."""
from uuid import uuid4

import psycopg
from test_pg_baseline import DSN
from test_pg_auto_work_order_register_http import setup


def test_timeline_primary_events_keyset_history_and_filters(monkeypatch):
    d,org,loc,other,other_loc,orders,client,_=setup(monkeypatch,count=1)
    order=orders[0]
    url=(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
         f"/orders/{order}/timeline")
    assert client.get(url).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    initial=client.get(url)
    assert initial.status_code==200,initial.text
    assert initial.headers["cache-control"]=="no-store"
    assert initial.headers["x-content-type-options"]=="nosniff"
    assert initial.json()["items"][0]["action"]=="ORDER_CREATED"
    assert initial.json()["nextCursor"] is None
    with psycopg.connect(DSN) as db:
        inspected=uuid4()
        db.execute("""INSERT INTO auto_work_order_inspection
            (id,work_order_id,organization_id,location_id,actor_id,
             odometer_km,fuel_percent,tyres_ok,lights_ok,fluid_leak,exterior_code)
            VALUES(%s,%s,%s,%s,%s,100,40,TRUE,TRUE,FALSE,'CLEAR')""",
            (inspected,order,org,loc,d["intake"]))
    response=client.get(url)
    assert response.status_code==200,response.text
    assert [x["action"] for x in response.json()["items"]]==[
        "INSPECTION_RECORDED","ORDER_CREATED"]
    assert response.json()["items"][0]["evidenceId"]==str(inspected)
    for secret in (str(d["vehicle"]),str(d["link"]),str(d["manager"]),
                   "customerId","vehicleId","price","amountMinor","paid"):
        assert secret not in response.text
    first=client.get(url,params={"limit":"1"})
    assert first.status_code==200,first.text
    assert [x["action"] for x in first.json()["items"]]==["INSPECTION_RECORDED"]
    cursor=first.json()["nextCursor"]
    assert cursor
    second=client.get(url,params={"limit":"1","cursor":cursor})
    assert second.status_code==200,second.text
    assert [x["action"] for x in second.json()["items"]]==["ORDER_CREATED"]
    assert second.json()["nextCursor"] is None
    assert client.get(url,params={"cursor":cursor}).status_code==422
    assert client.get(url,params={"limit":"0"}).status_code==422
    assert client.get(url,params={"limit":"26"}).status_code==422
    assert client.get(url,params={"limit":"1","cursor":"broken"}).status_code==422
    assert client.get(url,params={"limit":["1","2"]}).status_code==422
    assert client.get(url,params={"cursor":["a","b"]}).status_code==422
    assert client.get(url.replace(str(order),str(uuid4()))).status_code==403
    assert client.get(url.replace(str(loc),str(other_loc))).status_code==403
    assert client.get(url.replace(str(org),str(other))).status_code==403


def test_timeline_reauthorization_role_and_customer_proof(monkeypatch):
    d,org,loc,_,_,orders,client,_=setup(monkeypatch,count=1)
    order=orders[0]
    url=(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
         f"/orders/{order}/timeline")
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert client.get(url).status_code==200
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                      WHERE organization_id=%s AND location_id=%s
                        AND user_id=%s AND role_code='INTAKE'
                        AND revoked_at IS NULL""",(org,loc,d["intake"]))
    assert client.get(url).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization_role_grant(
          id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert client.get(url).status_code==403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.get(url).status_code==404


def test_timeline_document_policy_deny_and_cutoff_exclude_sources(monkeypatch):
    """Real PG17 ADMIN policies must filter entire families before LIMIT."""
    from roboport_api.auto_order_timeline import read_order_timeline
    from roboport_api.auto_document_access import publish_policy
    from test_pg_auto_document_access_http import admin

    d,org,loc,_,_,orders,client,_=setup(monkeypatch,count=1)
    order=orders[0]
    intake_token=d["tokens"][d["intake"]]
    manager_token=d["tokens"][d["manager"]]

    class Recorder:
        def __init__(self, db):
            self.db=db
            self.sql=[]
        def transaction(self):
            return self.db.transaction()
        def execute(self, query, params=None):
            self.sql.append(query)
            return self.db.execute(query,params)

    def sourced_sql():
        with psycopg.connect(DSN) as db:
            recorder=Recorder(db)
            value=read_order_timeline(recorder,intake_token,org,loc,order)
            assert value["items"][0]["action"]=="ORDER_CREATED"
            assert "customerLinkId" not in str(value)
            return next(q for q in recorder.sql if "WITH evidence AS" in q)

    # An INTAKE actor ordinarily can read their intake-act documents, but
    # cannot read ESTIMATE/HANDOVER history without a MANAGER grant.
    initial=sourced_sql()
    assert "FROM auto_work_order_intake_act item" in initial
    assert "FROM auto_work_order_estimate_revision item" not in initial
    assert "FROM auto_order_demo_document_event item" not in initial

    with psycopg.connect(DSN) as db:
        admin(db,org,d["manager"])
        def rule(*,allowed,not_before=None):
            publish_policy(db,manager_token,org,request_key=uuid4(),
                role="INTAKE",loc=loc,user=None,document="INTAKE_ACT",
                cap="READ",allowed=allowed,not_before=not_before)
        rule(allowed=False)
    denied=sourced_sql()
    assert "FROM auto_work_order_intake_act item" not in denied
    assert "FROM auto_work_order_intake_act_review item" not in denied
    with psycopg.connect(DSN) as db:
        publish_policy(db,manager_token,org,request_key=uuid4(),
            role="INTAKE",loc=loc,user=None,document="INTAKE_ACT",cap="READ",
            allowed=True,not_before=__import__("datetime").datetime(
                2099,1,1,tzinfo=__import__("datetime").timezone.utc))
    cutoff=sourced_sql()
    assert "FROM auto_work_order_intake_act item" not in cutoff
    with psycopg.connect(DSN) as db:
        publish_policy(db,manager_token,org,request_key=uuid4(),
            role="INTAKE",loc=loc,user=None,document="INTAKE_ACT",cap="READ",
            allowed=True,not_before=None)
    allowed=sourced_sql()
    assert "FROM auto_work_order_intake_act item" in allowed
