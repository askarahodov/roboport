"""PG17: complete manager warehouse ABC/XYZ over closed months, no money or PII."""
import csv
from io import StringIO
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest

from roboport_api.auto_stock_abc_xyz import _variation,_month_string,_csv_report
from test_pg_auto_bay_occupancy_http import _fixture
from test_pg_auto_stock_ledger import sku,record
from test_pg_auto_work_order_stock import work_order
from test_pg_baseline import DSN


def url(org,loc):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/stock-abc-xyz"


def last_months():
    now=datetime.now(ZoneInfo("Europe/Berlin"))
    current=now.year*12+now.month-1
    indices=[current-3,current-2,current-1]
    return [_month_string(i) for i in indices]


def seed(db,d,org,loc,bay):
    order,_=work_order(db,d,org,loc,bay)
    months=last_months()
    profiles=[
        ("A",[60,60,60],0),
        ("B",[25,25,25],0),
        ("C",[12,15,18],5),
        ("D",[0,0,15],0),
        ("E",[0,0,0],0),
    ]
    result={}
    for label,monthly,returned in profiles:
        item=sku(db,org,loc)
        result[label]=str(item)
        issued=sum(monthly)
        if issued:
            record(db,item,"RECEIPT",issued+10)
            record(db,item,"RESERVE",issued)
        for index,quantity in enumerate(monthly):
            if quantity==0:
                continue
            year,month=map(int,months[index].split("-"))
            instant=datetime(year,month,15,12,tzinfo=ZoneInfo("Europe/Berlin"))
            stock_id=uuid4()
            db.execute(
                """INSERT INTO auto_stock_event
                   (id,request_id,sku_id,organization_id,location_id,
                    action,quantity,created_at)
                   VALUES(%s,%s,%s,%s,%s,'ISSUE',%s,%s)""",
                (stock_id,uuid4(),item,org,loc,quantity,instant),
            )
            db.execute(
                """INSERT INTO auto_work_order_stock_event
                   (stock_event_id,work_order_id,sku_id,action,quantity,created_at)
                   VALUES(%s,%s,%s,'ISSUE',%s,%s)""",
                (stock_id,order,item,quantity,instant),
            )
        if returned:
            year,month=map(int,months[-1].split("-"))
            instant=datetime(year,month,20,12,tzinfo=ZoneInfo("Europe/Berlin"))
            stock_id=uuid4()
            db.execute(
                """INSERT INTO auto_stock_event
                   (id,request_id,sku_id,organization_id,location_id,
                    action,quantity,created_at)
                   VALUES(%s,%s,%s,%s,%s,'RETURN',%s,%s)""",
                (stock_id,uuid4(),item,org,loc,returned,instant),
            )
            db.execute(
                """INSERT INTO auto_work_order_stock_event
                   (stock_event_id,work_order_id,sku_id,action,quantity,created_at)
                   VALUES(%s,%s,%s,'RETURN',%s,%s)""",
                (stock_id,order,item,returned,instant),
            )
    for _ in range(24):
        sku(db,org,loc)
    return months,result


def test_full_stock_abc_xyz_all_skus_paging_real_issue_return_and_role_revoke(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    with psycopg.connect(DSN) as db:
        months,ids=seed(db,d,org,loc,bay)
    first=months[0]
    last=months[-1]
    path=url(org,loc)
    query={"fromMonth":first,"toMonth":last}
    assert http.get(path,params=query).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert http.get(path,params=query).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    response=http.get(path,params=query)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    data=response.json()
    assert data["syntheticOnly"] is True
    assert data["basis"]=="GROSS_ISSUE_UNITS_CLOSED_MONTHS"
    assert data["moneyValuation"] is False
    assert data["months"]==months
    assert data["timeZone"]=="Europe/Berlin"
    assert data["pageSize"]==25
    assert data["nextOffset"]==25
    assert data["offset"]==0
    assert data["summary"]=={
        "skuCount":29,"activeSkus":4,"grossIssued":315,
        "grossReturned":5,"netPeriod":310,
        "classCounts":{"A":2,"B":1,"C":1,"X":2,
                       "Y":1,"Z":1,"none":25},
    }
    assert len(data["items"])==25
    assert [r["skuId"] for r in data["items"][:4]]==[
        ids["A"],ids["B"],ids["C"],ids["D"],
    ]
    assert [(r["abc"],r["xyz"]) for r in data["items"][:5]]==[
        ("A","X"),("A","X"),("B","Y"),("C","Z"),(None,None),
    ]
    assert data["items"][2]["grossIssued"]==45
    assert data["items"][2]["grossReturned"]==5
    assert data["items"][2]["netPeriod"]==40
    assert data["items"][2]["monthlyIssued"]==[12,15,18]
    assert data["items"][2]["variationBasisPoints"]==_variation([12,15,18])[0]
    second=http.get(path,params={**query,"offset":25})
    assert second.status_code==200,second.text
    page=second.json()
    assert page["nextOffset"] is None and page["offset"]==25
    assert page["summary"]==data["summary"]
    assert len(page["items"])==4
    ids_report=[r["skuId"] for r in data["items"]+page["items"]]
    assert len(ids_report)==29 and len(set(ids_report))==29
    assert ids["E"] in ids_report

    # Every page reevaluates live manager rights; cursor/offset grants nothing.
    assert http.get(url(foreign,foreign_loc),params={
        **query,"offset":25,
    }).status_code==403
    assert http.get(url(org,foreign_loc),params=query).status_code==403
    assert str(d["link"]) not in response.text
    assert str(d["vehicle"]) not in response.text
    assert str(d["ca"]) not in response.text
    assert str(order if False else d["manager"]) not in response.text
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s
                 AND user_id=%s AND role_code='MANAGER'""",
            (org,loc,d["manager"]),
        )
    assert http.get(path,params={**query,"offset":25}).status_code==403


def test_report_invalid_period_scope_offset_and_disabled_branch(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    months=last_months()
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    base=url(org,loc)
    pairs=[
        ("2030-01","2030-02"),
        ("2030-02","2030-01"),
        ("bad","2030-01"),("2030-13","2030-15"),
        ("9998-01","9998-03"),
    ]
    for start,end in pairs:
        assert http.get(base,params={
            "fromMonth":start,"toMonth":end,
        }).status_code==422
    for offset in ("-1","1","3.1","1000001"):
        assert http.get(base,params={
            "fromMonth":months[0],"toMonth":months[-1],"offset":offset,
        }).status_code==422
    assert http.get(base).status_code==422
    assert http.get(url("bad",loc),params={
        "fromMonth":months[0],"toMonth":months[-1],
    }).status_code==422
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_service_location SET disabled_at=now() WHERE id=%s",(loc,))
    assert http.get(base,params={
        "fromMonth":months[0],"toMonth":months[-1],
    }).status_code==403


def test_no_silent_cap_capacity_is_explicit_conflict(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    import roboport_api.auto_stock_abc_xyz as module
    with psycopg.connect(DSN) as db:
        for _ in range(3):
            sku(db,org,loc)
    monkeypatch.setattr(module,"MAX_SKUS",2)
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    months=last_months()
    result=http.get(url(org,loc),params={
        "fromMonth":months[0],"toMonth":months[-1],
    })
    assert result.status_code==409,result.text
    assert "items" not in result.json()


def test_capacity_probe_rejects_before_event_aggregation(monkeypatch):
    """No expensive history join when the branch catalogue already exceeds cap."""
    from contextlib import nullcontext
    from uuid import uuid4
    import roboport_api.auto_stock_abc_xyz as module

    class Result:
        def __init__(self,rows):
            self.rows=rows
        def fetchone(self):
            return self.rows[0] if self.rows else None
        def fetchall(self):
            return self.rows

    class ProbeConnection:
        def __init__(self):
            self.queries=[]
        def transaction(self):
            return nullcontext()
        def execute(self,sql,params):
            self.queries.append(sql)
            if "SELECT timezone_name" in sql:
                return Result([("Europe/Berlin",)])
            if "SELECT id FROM auto_stock_sku" in sql:
                assert params[-1]==3
                return Result([(uuid4(),),(uuid4(),),(uuid4(),)])
            raise AssertionError("aggregate query must never execute above capacity")

    monkeypatch.setattr(module,"organization_role_allows",lambda *a,**k:True)
    monkeypatch.setattr(module,"MAX_SKUS",2)
    conn=ProbeConnection()
    months=last_months()
    with pytest.raises(module.StockReportCapacity):
        module.stock_abc_xyz(conn,"",uuid4(),uuid4(),months[0],months[-1])
    assert len(conn.queries)==2
    assert not any("auto_stock_event" in query for query in conn.queries)


def test_exact_integer_cv_boundaries_and_abc_of_zero_volume():
    assert _variation([0,0,0])==(None,None)
    assert _variation([60,60,60])==(0,"X")
    assert _variation([12,15,18])[1]=="Y"
    assert _variation([0,0,15])[1]=="Z"
    # X 10% and Y 25% threshold boundary comparisons use integer maths.
    assert _variation([9,10,11])[1]=="X"
    assert _variation([10,10,16])[1]=="Y"


def test_entire_branch_csv_export_is_authenticated_complete_and_scoped(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    with psycopg.connect(DSN) as db:
        months,ids=seed(db,d,org,loc,bay)
    path=url(org,loc)+"/export"
    params={"fromMonth":months[0],"toMonth":months[-1]}
    assert http.get(path,params=params).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert http.get(path,params=params).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    response=http.get(path,params=params)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    assert response.headers["x-content-type-options"]=="nosniff"
    assert response.headers["content-disposition"].endswith('filename="auto-stock-abc-xyz.csv"')
    assert response.text.startswith("\ufeffSKU UUID;Артикул;")
    rows=list(csv.reader(StringIO(response.text.removeprefix("\ufeff")),delimiter=";"))
    assert len(rows)==30  # header and all 29 SKU, not the first page only
    assert rows[0][-3:]==months
    assert len(rows[0])==11
    assert len({row[0] for row in rows[1:]})==29
    assert rows[1][0]==ids["A"] and rows[1][2]=="180"
    assert rows[3][0]==ids["C"] and rows[3][4]=="40"
    assert rows[3][-3:]==["12","15","18"]
    assert http.get(url(foreign,foreign_loc)+"/export",params=params).status_code==403
    assert http.get(path,params={"fromMonth":"2026-01","toMonth":"2026-01"}).status_code==422
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                      WHERE organization_id=%s AND location_id=%s
                        AND user_id=%s AND role_code='MANAGER'""",
                   (org,loc,d["manager"]))
    assert http.get(path,params=params).status_code==403


def test_csv_escapes_spreadsheet_formulas_and_handles_malformed_article_text():
    report={"months":["2026-06","2026-07","2026-08"],"items":[
        {"skuId":str(uuid4()),"article":text,"grossIssued":1,
         "grossReturned":0,"netPeriod":1,"abc":"A","xyz":"Z",
         "variationBasisPoints":14142,"monthlyIssued":[1,0,0]}
        for text in ("=HYPERLINK(\\"http://bad\\")","+SUM(1,2)",
                     "  -1","\\t@external","normal;quoted\\nrow")
    ]}
    rows=list(csv.reader(StringIO(_csv_report(report).removeprefix("\ufeff")),delimiter=";"))
    assert [row[1] for row in rows[1:]]==[
        "'=HYPERLINK(\\"http://bad\\")","'+SUM(1,2)",
        "'  -1","' @external","normal;quoted row",
    ]
    assert len(rows)==6 and all(len(row)==11 for row in rows)
