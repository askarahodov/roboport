"""PG17/HTTPS catalog: create, version, XLSX atomic import, logical merge."""
from uuid import UUID,uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_catalog_xlsx import export_catalog_xlsx,parse_catalog_xlsx
from roboport_api.auto_sku_catalog import bulk_prices,CatalogConflict
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_baseline import DSN


def fixture(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,_=_org_and_bay(db)
        warehouse=grant(db,d["intake"],org,loc,"WAREHOUSE")
        manager=grant(db,d["intake"],org,loc,"MANAGER")
        other,other_loc,_=_org_and_bay(db)
    return d,org,loc,other,other_loc,warehouse,manager


def url(org):
    return f"/api/v1/dev/auto/organizations/{org}/warehouse/catalog"


def post(http,d,actor,path,payload,key=None,*,xlsx=False):
    http.cookies.set("rp_session",d["tokens"][actor])
    kw={"content":payload} if xlsx else {"json":payload}
    return http.post(path,**kw,headers={
        "Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][actor],
        "Idempotency-Key":str(key or uuid4()),
        **({"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"} if xlsx else {})
    })


def row(article,buy=1250,sale=2300,analog=None):
    return {"article":article,"category":"PARTS","uom":"PCS",
        "barcode":None,"brand":"SYNTHETIC","purchaseMinor":buy,
        "saleMinor":sale,"analogArticle":analog}


def create(http,d,org,loc,article,key=None):
    payload={k:v for k,v in row(article).items() if k!="analogArticle"}
    result=post(http,d,d["intake"],url(org)+"/skus",
                {"locationId":str(loc),**payload},key)
    assert result.status_code==201,result.text
    return result.json()["items"][0]["skuId"]


def listed(http,d,org,loc,actor=None):
    http.cookies.set("rp_session",d["tokens"][actor or d["intake"]])
    return http.get(url(org),params={"locationId":str(loc)})


def test_create_price_xlsx_preview_commit_and_export(monkeypatch):
    d,org,loc,other,other_loc,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        assert listed(http,d,org,loc,d["client_a"]).status_code==403
        first_key=uuid4()
        item=create(http,d,org,loc,"SYNTH-447-A",first_key)
        assert post(http,d,d["intake"],url(org)+"/skus",{
            "locationId":str(loc),**{k:v for k,v in row("SYNTH-447-A").items()
            if k!="analogArticle"}},first_key).json()["replayed"] is True
        new_price={"locationId":str(loc),"entries":[{"skuId":item,
              "expectedVersion":1,"purchaseMinor":1280,"saleMinor":2600}]}
        pk=uuid4()
        raised=post(http,d,d["intake"],url(org)+"/prices",new_price,pk)
        assert raised.status_code==201,raised.text
        assert raised.json()["items"]==[{"skuId":item,"version":2}]
        assert post(http,d,d["intake"],url(org)+"/prices",new_price,pk).json()["replayed"] is True
        assert post(http,d,d["intake"],url(org)+"/prices",new_price).status_code==409
        http.cookies.set("rp_session",d["tokens"][d["intake"]])
        exported=http.get(url(org)+"/export",params={"locationId":str(loc)})
        assert exported.status_code==200,exported.text[:150]
        assert parse_catalog_xlsx(exported.content)[0]["purchaseMinor"]==1280
        workbook=export_catalog_xlsx([
            row("SYNTH-447-A",1350,2900),
            row("SYNTH-447-B",1100,2100,"SYNTH-447-A")])
        preview=post(http,d,d["intake"],
            url(org)+"/imports/preview?locationId="+str(loc),
            workbook,xlsx=True)
        assert preview.status_code==201,preview.text
        info=preview.json()
        assert info["items"][0]=={"article":"SYNTH-447-A","skuId":item,"version":2}
        assert info["items"][1]["skuId"] is None
        payload={"locationId":str(loc),"previewId":info["previewId"],
                 "sha256":info["sha256"]}
        key=uuid4()
        saved=post(http,d,d["intake"],url(org)+"/imports/commit",payload,key)
        assert saved.status_code==201,saved.text
        assert saved.json()["rowsWritten"]==2
        replay=post(http,d,d["intake"],url(org)+"/imports/commit",payload,key)
        assert replay.status_code==201 and replay.json()["replayed"] is True
        assert post(http,d,d["intake"],url(org)+"/imports/commit",payload).status_code==409
        values={r["article"]:r for r in listed(http,d,org,loc).json()["items"]}
        assert values["SYNTH-447-A"]["version"]==3
        assert values["SYNTH-447-B"]["version"]==1
        with psycopg.connect(DSN) as db:
            assert db.execute("SELECT count(*) FROM auto_catalog_analog WHERE organization_id=%s",
                              (org,)).fetchone()==(1,)
            assert db.execute("SELECT count(*) FROM auto_catalog_price_event WHERE sku_id=%s",
                              (UUID(item),)).fetchone()==(3,)


def test_analog_merge_history_and_active_stock_denied(monkeypatch):
    d,org,loc,other,other_loc,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        source=create(http,d,org,loc,"SYNTH-447-SOURCE")
        target=create(http,d,org,loc,"SYNTH-447-TARGET")
        stocked=create(http,d,org,loc,"SYNTH-447-STOCKED")
        ak=uuid4()
        link={"locationId":str(loc),"leftSkuId":source,
              "rightSkuId":target,"evidenceNote":"Offline manual suggestion"}
        original=post(http,d,d["intake"],url(org)+"/analogs",link,ak)
        assert original.status_code==201,original.text
        assert post(http,d,d["intake"],url(org)+"/analogs",link,ak).json()["replayed"] is True
        with psycopg.connect(DSN) as db:
            source_lot=add_lot(db,org,loc,UUID(source),add_bin(db,org,loc))
            db.execute("SELECT record_auto_stock(%s,%s,'RECEIPT',3)",
                       (UUID(stocked),uuid4()))
        assert post(http,d,d["intake"],url(org)+"/merges",{
            "locationId":str(loc),"sourceSkuId":stocked,
            "targetSkuId":target}).status_code==409
        m={"locationId":str(loc),"sourceSkuId":source,"targetSkuId":target}
        key=uuid4()
        merged=post(http,d,d["intake"],url(org)+"/merges",m,key)
        assert merged.status_code==201,merged.text
        assert merged.json()["historicalFksRewritten"] is False
        assert post(http,d,d["intake"],url(org)+"/merges",m,key).json()["replayed"] is True
        assert post(http,d,d["intake"],url(org)+"/merges",m).status_code==409
        assert post(http,d,d["intake"],url(org)+"/prices",{
            "locationId":str(loc),"entries":[{
               "skuId":source,"expectedVersion":1,
               "purchaseMinor":1500,"saleMinor":3000}]}).status_code in (403,409)
        with psycopg.connect(DSN) as db:
            # Merged SKU cannot quietly acquire physical stock afterward,
            # even through a lower-level stock API bypassing catalog HTTP.
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("SELECT record_auto_stock(%s,%s,'RECEIPT',1)",
                               (UUID(source),uuid4()))
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    add_lot(db,org,loc,UUID(source),add_bin(db,org,loc))
            assert db.execute("SELECT sku_id FROM auto_stock_lot WHERE id=%s",
                (source_lot,)).fetchone()==(UUID(source),)
            assert db.execute("SELECT sku_id FROM auto_catalog_price_event WHERE sku_id=%s",
                (UUID(source),)).fetchone()==(UUID(source),)
        assert next(r for r in listed(http,d,org,loc).json()["items"]
                    if r["skuId"]==source)["mergedInto"]==target


def test_denied_scope_bad_input_stale_preview_and_role_revoke(monkeypatch):
    d,org,loc,foreign,foreign_loc,_,manager_grant=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        body={"locationId":str(loc),**{
          k:v for k,v in row("SYNTH-447-SEC").items() if k!="analogArticle"}}
        assert post(http,d,d["client_a"],url(org)+"/skus",body).status_code==403
        assert post(http,d,d["intake"],url(foreign)+"/skus",
             {**body,"locationId":str(foreign_loc)}).status_code==403
        assert post(http,d,d["intake"],url(org)+"/skus",
             {**body,"purchaseMinor":True}).status_code==422
        assert post(http,d,d["intake"],url(org)+"/skus",
             {**body,"article":"BAD INVALID"}).status_code==422
        item=create(http,d,org,loc,"SYNTH-447-SEC")
        assert post(http,d,d["intake"],
            url(org)+"/imports/preview?locationId="+str(loc),
            b"not-a-workbook",xlsx=True).status_code==422
        xlsx=export_catalog_xlsx([row("SYNTH-447-SEC",1500,2500)])
        preview=post(http,d,d["intake"],
            url(org)+"/imports/preview?locationId="+str(loc),xlsx,xlsx=True)
        assert preview.status_code==201,preview.text
        info=preview.json()
        assert post(http,d,d["intake"],url(org)+"/prices",{
            "locationId":str(loc),"entries":[{
                "skuId":item,"expectedVersion":1,
                "purchaseMinor":1510,"saleMinor":2510}]}).status_code==201
        commit={"locationId":str(loc),"previewId":info["previewId"],
                "sha256":info["sha256"]}
        assert post(http,d,d["intake"],url(org)+"/imports/commit",
                    commit).status_code==409
        with psycopg.connect(DSN) as db:
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (manager_grant,))
        assert listed(http,d,org,loc).status_code==200
        assert post(http,d,d["intake"],url(org)+"/prices",{
            "locationId":str(loc),"entries":[{
                "skuId":item,"expectedVersion":2,
                "purchaseMinor":1600,"saleMinor":2600}]}).status_code==403
        assert post(http,d,d["intake"],url(org)+"/imports/commit",commit).status_code==403
        assert listed(http,d,org,foreign_loc).status_code==403


def test_price_batch_lock_serialize_competing_versions(monkeypatch):
    d,org,loc,foreign,foreign_loc,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        item=create(http,d,org,loc,"SYNTH-447-RACE")
    line={"skuId":UUID(item),"expectedVersion":1,
          "purchaseMinor":1800,"saleMinor":2800}
    with psycopg.connect(DSN) as first,psycopg.connect(DSN) as second:
        with first.transaction():
            out=bulk_prices(first,d["tokens"][d["intake"]],org,loc,uuid4(),[line])
            assert out["items"][0]["version"]==2
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    bulk_prices(second,d["tokens"][d["intake"]],org,loc,
                                uuid4(),[line])
        with pytest.raises(CatalogConflict):
            bulk_prices(second,d["tokens"][d["intake"]],org,loc,uuid4(),[line])
        assert second.execute("SELECT max(version) FROM auto_catalog_price_event WHERE sku_id=%s",
                              (UUID(item),)).fetchone()==(2,)
