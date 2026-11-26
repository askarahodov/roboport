"""PG17/HTTPS: complete PII-free CSV work-order register export."""
import csv
from io import StringIO
from uuid import uuid4

import psycopg
from test_pg_auto_work_order_register_http import setup


def rows(response):
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    assert response.headers["x-content-type-options"]=="nosniff"
    assert response.headers["content-type"].startswith("text/csv")
    assert 'attachment;' in response.headers["content-disposition"]
    assert response.content.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(StringIO(response.text.removeprefix("\ufeff")),delimiter=";"))


def test_export_26_orders_not_only_first_25_and_exact_filter(monkeypatch):
    d,org,loc,foreign,foreign_loc,orders,http,url=setup(monkeypatch)
    path=url+"/export"
    assert http.get(path).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    result=rows(http.get(path))
    assert len(result)==27
    assert result[0]==["Заказ-наряд","Запись","Пост","Создан",
                       "Начало записи","Окончание записи","Технический статус"]
    assert len({row[0] for row in result[1:]})==26
    assert {row[0] for row in result[1:]}=={str(x) for x in orders}
    assert all(len(row)==7 and row[6]=="DRAFT" for row in result[1:])
    assert all(not cell.startswith(("=","+","-","@","\t","\r","\n"))
               for row in result for cell in row)
    assert str(d["vehicle"]) not in http.get(path).text
    assert str(d["link"]) not in http.get(path).text
    assert "customerId" not in http.get(path).text
    exact=rows(http.get(path,params={"orderId":str(orders[-1])}))
    assert len(exact)==2 and exact[1][0]==str(orders[-1])
    assert len(rows(http.get(path,params={"stage":"READY"})))==1
    assert len(rows(http.get(path,params={"stage":"DRAFT"})))==27
    assert http.get(path,params={"stage":"INVALID"}).status_code==422
    assert http.get(path,params={"orderId":"bad"}).status_code==422
    assert http.get(path,params={"stage":["ALL","DRAFT"]}).status_code==422
    assert http.get(path,params={"cursor":"forged"}).status_code==422
    wrong=path.replace(str(org),str(foreign)).replace(str(loc),str(foreign_loc))
    assert http.get(wrong).status_code==403
    assert http.get(path.replace(str(loc),str(foreign_loc))).status_code==403


def test_export_limit_is_http_409_never_partial_csv(monkeypatch):
    import roboport_api.auto_work_order_register as register
    d,org,loc,foreign,foreign_loc,orders,http,url=setup(monkeypatch,2)
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    # Pure internal bound override. The real production/dev limit remains 1000.
    monkeypatch.setattr(register,"EXPORT_LIMIT",1)
    result=http.get(url+"/export")
    assert result.status_code==409,result.text
    assert result.json()["code"]=="REGISTER_LIMIT"
    assert "content-disposition" not in result.headers
    assert not result.headers["content-type"].startswith("text/csv")
    exact=rows(http.get(url+"/export",params={"orderId":str(orders[0])}))
    assert len(exact)==2


def test_export_fails_closed_when_staff_or_customer_proof_revoked(monkeypatch):
    d,org,loc,foreign,foreign_loc,orders,http,url=setup(monkeypatch,2)
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    path=url+"/export"
    assert len(rows(http.get(path)))==3
    with psycopg.connect(__import__("test_pg_baseline").DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
            WHERE organization_id=%s AND location_id=%s AND user_id=%s
              AND role_code='INTAKE' AND revoked_at IS NULL""",
            (org,loc,d["intake"]))
    assert http.get(path).status_code==403
    with psycopg.connect(__import__("test_pg_baseline").DSN) as db:
        db.execute("""INSERT INTO auto_organization_role_grant
              (id,organization_id,location_id,user_id,role_code)
              VALUES(%s,%s,%s,%s,'INTAKE')""",
              (uuid4(),org,loc,d["intake"]))
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    value=rows(http.get(path))
    assert len(value)==1


def test_export_and_register_hide_handover_status_before_filtering(monkeypatch):
    from datetime import timedelta
    from uuid import UUID
    from fastapi.testclient import TestClient
    from roboport_api.main import app
    from roboport_api.auto_document_access import publish_policy
    from roboport_api.auto_document_lifecycle import record_document_event
    from test_pg_auto_document_lifecycle_http import ready
    from test_pg_auto_document_access_http import admin
    from test_pg_baseline import DSN

    d,org,loc,order,act,preview=ready(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders"
    path=url+"/export"
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["other"]))
        cutoff=db.execute("SELECT created_at FROM auto_work_order WHERE id=%s",
                          (order,)).fetchone()[0]+timedelta(seconds=1)
        for action,actor in (("POSTED",d["intake"]),("CLIENT_ACK",d["client_a"]),
                             ("CLOSED_TECHNICAL",d["intake"])):
            record_document_event(db,d["tokens"][actor],org,loc,order,
                request_key=uuid4(),action=action,preview_id=UUID(preview["id"]),
                preview_digest=preview["digest"],intake_act_id=UUID(act["id"]),
                intake_digest=act["sha256"])

    def assert_visible(expected):
        result=http.get(url,params={"orderId":str(order)})
        assert result.status_code==200,result.text
        assert [(item["workOrderId"],item["stage"])
                for item in result.json()["items"]]==[(str(order),expected)]
        exported=rows(http.get(path,params={"orderId":str(order)}))
        assert len(exported)==2 and exported[1][0]==str(order)
        assert exported[1][6]==expected
        workflow={"view":"workflow","repairStage":"READY","orderId":str(order)}
        separate=http.get(url,params=workflow)
        assert separate.status_code==200,separate.text
        item=separate.json()["items"][0]
        assert item["repairStage"]=="READY"
        assert item["documentStage"]==("CLOSED_TECHNICAL" if expected=="CLOSED_TECHNICAL" else "RESTRICTED")
        assert item["bookingStatus"]=="SCHEDULED"
        expanded=rows(http.get(path,params=workflow))
        assert len(expanded)==2 and len(expanded[1])==11
        assert expanded[1][7:10]==[item["repairStage"],item["documentStage"],item["bookingStatus"]]
        hidden=http.get(url,params={**workflow,"documentStage":"CLOSED_TECHNICAL"})
        assert len(hidden.json()["items"])==(1 if expected=="CLOSED_TECHNICAL" else 0)
        assert len(rows(http.get(path,params={**workflow,"documentStage":"CLOSED_TECHNICAL"})))==(2 if expected=="CLOSED_TECHNICAL" else 1)
        for stage in ("POSTED","CLIENT_ACKNOWLEDGED","CLIENT_DISPUTED","CLOSED_TECHNICAL","READY"):
            filtered=http.get(url,params={"stage":stage})
            assert filtered.status_code==200,filtered.text
            assert [item["workOrderId"] for item in filtered.json()["items"]]==(
                [str(order)] if stage==expected else [])
            assert filtered.json()["nextCursor"] is None
            assert len(rows(http.get(path,params={"stage":stage})))==(
                2 if stage==expected else 1)

    def policy(allowed,not_before=None):
        with psycopg.connect(DSN) as db:
            publish_policy(db,d["tokens"][d["other"]],org,
                request_key=uuid4(),role="MANAGER",loc=loc,user=None,
                document="HANDOVER",cap="READ",allowed=allowed,
                not_before=not_before)

    assert_visible("CLOSED_TECHNICAL")
    policy(False)
    assert_visible("READY")
    policy(True,cutoff)
    assert_visible("READY")
    policy(True)
    assert_visible("CLOSED_TECHNICAL")
    http.cookies.set("rp_session",d["tokens"][d["other"]])
    assert_visible("READY")
