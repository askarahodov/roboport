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
