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
from test_pg_auto_work_order_stock import work_order
from roboport_api.auto_work_order_return import return_work_order_stock
from roboport_api.auto_stock_service import AutoStockConflict
from roboport_api.auto_stock_lot_stocktake import count_stock_lot,approve_stock_lot_count
from roboport_api.auto_stock_receipt_document import (
    create_receipt_document, accept_receipt_document,
)
from test_pg_auto_stock_ledger import balance
from roboport_api.auto_supplier_purchase import (
    place_supplier_order, record_supplier_delivery,
)
from test_pg_auto_supplier_purchase import quotes


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
                    add_lot(db,org,loc,UUID(source),add_bin(db,org,loc,"B-04"))
            # New unpostable receipt drafts must also be rejected after
            # merge, even if an old, empty lot still exists on source.
            from roboport_api.auto_stock_service import AutoStockConflict
            with pytest.raises(AutoStockConflict):
                create_receipt_document(
                    db,d["tokens"][d["intake"]],org,loc,
                    source_lot,uuid4(),1,"POST-MERGE-DRAFT-BLOCK",
                )
            with pytest.raises(AutoStockConflict):
                count_stock_lot(
                    db,d["tokens"][d["intake"]],org,loc,
                    source_lot,uuid4(),1,"POST-MERGE-COUNT-BLOCK",
                )
            assert db.execute("SELECT sku_id FROM auto_stock_lot WHERE id=%s",
                (source_lot,)).fetchone()==(UUID(source),)
            assert db.execute("SELECT sku_id FROM auto_catalog_price_event WHERE sku_id=%s",
                (UUID(source),)).fetchone()==(UUID(source),)
            # A purchase created only after logical merge would later leave
            # a supplier delivery unpostable. The new order guard refuses it.
            bay=db.execute("SELECT id FROM auto_work_bay WHERE organization_id=%s AND location_id=%s",
                           (org,loc)).fetchone()[0]
            order,_=work_order(db,d,org,loc,bay)
            with pytest.raises(AutoStockConflict):
                place_supplier_order(
                    db,d["tokens"][d["intake"]],org,loc,order,source_lot,
                    uuid4(),1,quotes(db,UUID(source)),
                    "SYNTH-SUP-1","OFFLINE-1",
                )
        assert next(r for r in listed(http,d,org,loc).json()["items"]
                    if r["skuId"]==source)["mergedInto"]==target
        # Two independent historical batches with different identifiers
        # must not be silently conflated, even with zero on-hand stock.
        incompatible_source=create(http,d,org,loc,"SYNTH-447-BATCH-SRC")
        incompatible_target=create(http,d,org,loc,"SYNTH-447-BATCH-DST")
        with psycopg.connect(DSN) as db:
            add_lot(db,org,loc,UUID(incompatible_source),
                    add_bin(db,org,loc,"B-02"),batch="BATCH-02")
            add_lot(db,org,loc,UUID(incompatible_target),
                    add_bin(db,org,loc,"B-03"),batch="BATCH-03")
        assert post(http,d,d["intake"],url(org)+"/merges",{
            "locationId":str(loc),"sourceSkuId":incompatible_source,
            "targetSkuId":incompatible_target}).status_code==409


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


def test_catalog_replay_is_scoped_to_original_tenant_branch(monkeypatch):
    d,org,loc,foreign,foreign_loc,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        body={"locationId":str(loc),**{
          k:v for k,v in row("SYNTH-447-SCOPED").items() if k!="analogArticle"}}
        key=uuid4()
        assert post(http,d,d["intake"],url(org)+"/skus",body,key).status_code==201
        workbook=export_catalog_xlsx([row("SYNTH-447-SCOPED",1400,2500)])
        preview_key=uuid4()
        p=post(http,d,d["intake"],url(org)+"/imports/preview?locationId="+str(loc),
               workbook,preview_key,xlsx=True)
        assert p.status_code==201,p.text
        proof=p.json()
        commit_key=uuid4()
        committed={"locationId":str(loc),"previewId":proof["previewId"],
                   "sha256":proof["sha256"]}
        assert post(http,d,d["intake"],url(org)+"/imports/commit",
                    committed,commit_key).status_code==201
        with psycopg.connect(DSN) as db:
            grant(db,d["intake"],foreign,foreign_loc,"WAREHOUSE")
            grant(db,d["intake"],foreign,foreign_loc,"MANAGER")
        # Same actor, same payload/digest and same UUID replay key are still
        # NOT permission to return another branch's catalog receipts.
        assert post(http,d,d["intake"],url(foreign)+"/skus",
                    {**body,"locationId":str(foreign_loc)},key).status_code==409
        assert post(http,d,d["intake"],url(foreign)+
            "/imports/preview?locationId="+str(foreign_loc),
            workbook,preview_key,xlsx=True).status_code==409
        # Commit checks the preview's original tenant *before* replay:
        # an inaccessible preview is hidden with 403 rather than 409.
        assert post(http,d,d["intake"],url(foreign)+"/imports/commit",
            {**committed,"locationId":str(foreign_loc)},commit_key).status_code==403
        assert listed(http,d,foreign,foreign_loc).json()["items"]==[]


def test_estimate_read_history_cutoff_masks_old_sales_price_and_export(monkeypatch):
    from datetime import datetime,timezone
    from roboport_api.auto_document_access import publish_policy
    from test_pg_auto_document_access_http import admin
    d,org,loc,_,_,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        item=create(http,d,org,loc,"SYNTH-447-CUTOFF")
        before=listed(http,d,org,loc).json()["items"][0]
        assert before["saleMinor"]==2300 and before["purchaseMinor"]==1250
        with psycopg.connect(DSN) as db:
            admin(db,org,d["manager"])
            publish_policy(db,d["tokens"][d["manager"]],org,
               request_key=uuid4(),role="MANAGER",loc=loc,
               user=d["intake"],document="ESTIMATE",cap="READ",
               allowed=True,not_before=datetime(2099,1,1,tzinfo=timezone.utc))
        after=listed(http,d,org,loc).json()["items"][0]
        assert after["skuId"]==item
        assert after["purchaseMinor"]==1250
        assert after["saleMinor"] is None and after["saleHidden"] is True
        http.cookies.set("rp_session",d["tokens"][d["intake"]])
        assert http.get(url(org)+"/export",
             params={"locationId":str(loc)}).status_code==403


def test_xlsx_cannot_override_existing_sku_edit_deny_even_on_replay(monkeypatch):
    from roboport_api.auto_document_access import publish_policy
    from test_pg_auto_document_access_http import admin
    from datetime import datetime,timezone
    d,org,loc,_,_,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        item=create(http,d,org,loc,"SYNTH-447-EDIT")
        workbook=export_catalog_xlsx([row("SYNTH-447-EDIT",1450,2450)])
        uri=url(org)+"/imports/preview?locationId="+str(loc)
        key=uuid4()
        prepared=post(http,d,d["intake"],uri,workbook,key,xlsx=True)
        assert prepared.status_code==201,prepared.text
        proof=prepared.json()
        assert proof["items"][0]["skuId"]==item
        # A successful import receipt is not an authorization to replay
        # or further modify after an explicit WAREHOUSE EDIT denial.
        body={"locationId":str(loc),"previewId":proof["previewId"],
              "sha256":proof["sha256"]}
        committed_key=uuid4()
        completed=post(http,d,d["intake"],url(org)+"/imports/commit",
                       body,committed_key)
        assert completed.status_code==201,completed.text
        with psycopg.connect(DSN) as db:
            admin(db,org,d["manager"])
            publish_policy(db,d["tokens"][d["manager"]],org,
                request_key=uuid4(),role="WAREHOUSE",loc=loc,
                user=d["intake"],document="SUPPLIER_ORDER",cap="EDIT",
                allowed=False,not_before=None)
        assert post(http,d,d["intake"],url(org)+"/prices",{
            "locationId":str(loc),"entries":[{
                "skuId":item,"expectedVersion":2,
                "purchaseMinor":1550,"saleMinor":2550}]}).status_code==403
        assert post(http,d,d["intake"],uri,workbook,
                    uuid4(),xlsx=True).status_code==403
        assert post(http,d,d["intake"],uri,workbook,
                    key,xlsx=True).status_code==403
        assert post(http,d,d["intake"],url(org)+"/imports/commit",
                    body,committed_key).status_code==403
        with psycopg.connect(DSN) as db:
            assert db.execute("""SELECT count(*) FROM auto_catalog_price_event
                WHERE sku_id=%s""",(UUID(item),)).fetchone()==(2,)


def test_export_after_merge_never_refers_to_retired_analog(monkeypatch):
    d,org,loc,_,_,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        source=create(http,d,org,loc,"SYNTH-447-RETIRED")
        target=create(http,d,org,loc,"SYNTH-447-ACTIVE")
        link={"locationId":str(loc),"leftSkuId":source,"rightSkuId":target,
              "evidenceNote":"Unverified offline pair"}
        assert post(http,d,d["intake"],url(org)+"/analogs",link).status_code==201
        assert post(http,d,d["intake"],url(org)+"/merges",{
            "locationId":str(loc),"sourceSkuId":source,
            "targetSkuId":target}).status_code==201
        http.cookies.set("rp_session",d["tokens"][d["intake"]])
        exported=http.get(url(org)+"/export",params={"locationId":str(loc)})
        assert exported.status_code==200,exported.text[:120]
        values=parse_catalog_xlsx(exported.content)
        assert len(values)==1 and values[0]["article"]=="SYNTH-447-ACTIVE"
        assert values[0]["analogArticle"] is None
        preview=post(http,d,d["intake"],url(org)+
            "/imports/preview?locationId="+str(loc),
            exported.content,xlsx=True)
        assert preview.status_code==201,preview.text


def test_merge_refuses_outstanding_work_order_issue_then_allows_return(monkeypatch):
    # Zero physical stock alone does NOT imply safe merge: an automobile
    # WorkOrder can still return previously issued parts under the source SKU.
    d, org, loc, _, _, _, _ = fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        source=create(http,d,org,loc,"SYNTH-447-ISSUED")
        target=create(http,d,org,loc,"SYNTH-447-REPLACEMENT")
        with psycopg.connect(DSN) as db:
            bay=db.execute(
                "SELECT id FROM auto_work_bay WHERE organization_id=%s AND location_id=%s",
                (org,loc),
            ).fetchone()[0]
            order,_=work_order(db,d,org,loc,bay)
            db.execute("SELECT record_auto_stock(%s,%s,'RECEIPT',2)",
                       (UUID(source),uuid4()))
            reserve=uuid4()
            issued=uuid4()
            db.execute(
                "SELECT record_auto_work_order_stock(%s,%s,%s,'RESERVE',2)",
                (order,UUID(source),reserve),
            )
            db.execute(
                "SELECT record_auto_work_order_stock(%s,%s,%s,'ISSUE',2)",
                (order,UUID(source),issued),
            )
            assert balance(db,UUID(source))==(0,0)
            assert db.execute("""
                SELECT SUM(CASE WHEN action='ISSUE' THEN quantity
                                WHEN action='RETURN' THEN -quantity
                                ELSE 0 END)
                FROM auto_work_order_stock_event
                WHERE work_order_id=%s AND sku_id=%s
            """,(order,UUID(source))).fetchone()==(2,)

        merge={"locationId":str(loc),"sourceSkuId":source,"targetSkuId":target}
        denied=post(http,d,d["intake"],url(org)+"/merges",merge)
        assert denied.status_code==409,denied.text
        with psycopg.connect(DSN) as db:
            # Inserting via a SQL owner cannot bypass the immutable
            # DB guard either, even when on_hand and reserved are zero.
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(
                        "SELECT set_config('roboport.catalog_actor',%s,true)",
                        (str(d["intake"]),),
                    )
                    db.execute("""
                        INSERT INTO auto_catalog_merge(
                            id,request_key,organization_id,location_id,
                            source_sku_id,target_sku_id,created_by)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)
                    """,(uuid4(),uuid4(),org,loc,UUID(source),
                         UUID(target),d["intake"]))
            returned=return_work_order_stock(
                db,d["tokens"][d["intake"]],org,loc,order,
                UUID(source),uuid4(),2,
            )
            assert returned is not None
            assert balance(db,UUID(source))==(2,0)
        # Returning after a denied merge succeeds with its ORIGINAL SKU.
        # Physical stock now makes merging unsafe independently.
        assert post(http,d,d["intake"],url(org)+"/merges",merge).status_code==409


def test_merge_denied_for_unaccepted_receipt_then_manager_can_post(monkeypatch):
    # A WAREHOUSE draft has zero stock until a different MANAGER accepts it.
    # Merging the SKU while it is pending would make the stock posting fail.
    d,org,loc,_,_,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        source=create(http,d,org,loc,"SYNTH-447-PENDING-RECEIPT")
        target=create(http,d,org,loc,"SYNTH-447-RECEIPT-TARGET")
        with psycopg.connect(DSN) as db:
            grant(db,d["manager"],org,loc,"MANAGER")
            lot=add_lot(db,org,loc,UUID(source),add_bin(db,org,loc))
            doc=create_receipt_document(
                db,d["tokens"][d["intake"]],org,loc,
                lot,uuid4(),3,"SYNTH-UNACCEPTED-RECEIPT",
            )
            assert balance(db,UUID(source))==(0,0)
            assert db.execute("""
                SELECT count(*) FROM auto_stock_receipt_acceptance
                WHERE document_id=%s
            """,(doc,)).fetchone()==(0,)
        m={"locationId":str(loc),"sourceSkuId":source,"targetSkuId":target}
        rejected=post(http,d,d["intake"],url(org)+"/merges",m)
        assert rejected.status_code==409,rejected.text
        with psycopg.connect(DSN) as db:
            # Other entrypoints cannot bypass the same Postgres trigger.
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(
                        "SELECT set_config('roboport.catalog_actor',%s,true)",
                        (str(d["intake"]),),
                    )
                    db.execute("""
                        INSERT INTO auto_catalog_merge(
                          id,request_key,organization_id,location_id,
                          source_sku_id,target_sku_id,created_by)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)
                    """,(uuid4(),uuid4(),org,loc,UUID(source),
                         UUID(target),d["intake"]))
            accepted=accept_receipt_document(
                db,d["tokens"][d["manager"]],org,loc,doc,uuid4(),
            )
            assert accepted is not None
            assert balance(db,UUID(source))==(3,0)
        # Posting retains the original document and original SKU lineage.
        assert post(http,d,d["intake"],url(org)+"/merges",m).status_code==409


def test_merge_blocks_unfilled_supplier_purchase_and_preserves_delivery(monkeypatch):
    # A supplier order for a shortage has no stock yet, but can produce
    # future draft receipts. Merge must not strand it behind an immutable
    # source-SKU identity.
    d,org,loc,_,_,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        source=create(http,d,org,loc,"SYNTH-447-OPEN-PURCHASE")
        target=create(http,d,org,loc,"SYNTH-447-PURCHASE-TARGET")
        with psycopg.connect(DSN) as db:
            bay=db.execute(
                "SELECT id FROM auto_work_bay WHERE organization_id=%s AND location_id=%s",
                (org,loc),
            ).fetchone()[0]
            order,_=work_order(db,d,org,loc,bay)
            lot=add_lot(db,org,loc,UUID(source),add_bin(db,org,loc))
            offers=quotes(db,UUID(source))
            purchase,shortage=place_supplier_order(
                db,d["tokens"][d["intake"]],org,loc,order,lot,uuid4(),
                3,offers,"SYNTH-SUP-1","OFFLINE-1",
            )
            assert shortage==3 and balance(db,UUID(source))==(0,0)
        m={"locationId":str(loc),"sourceSkuId":source,"targetSkuId":target}
        blocked=post(http,d,d["intake"],url(org)+"/merges",m)
        assert blocked.status_code==409,blocked.text
        with psycopg.connect(DSN) as db:
            # Pending supplier delivery is still legal under the original SKU.
            event_id,receipt_id=record_supplier_delivery(
                db,d["tokens"][d["intake"]],org,loc,purchase,uuid4(),
                2,"SYNTH-DELIVERY-AFTER-DENIED-MERGE",
            )
            assert event_id is not None and receipt_id is not None
            assert balance(db,UUID(source))==(0,0)
            grant(db,d["manager"],org,loc,"MANAGER")
            accepted=accept_receipt_document(
                db,d["tokens"][d["manager"]],org,loc,receipt_id,uuid4(),
            )
            assert accepted is not None
            assert balance(db,UUID(source))==(2,0)
        assert post(http,d,d["intake"],url(org)+"/merges",m).status_code==409


def test_merge_denies_unapproved_stocktake_but_manager_can_post(monkeypatch):
    d,org,loc,_,_,_,_=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        source=create(http,d,org,loc,"SYNTH-447-COUNT-OPEN")
        target=create(http,d,org,loc,"SYNTH-447-COUNT-TARGET")
        with psycopg.connect(DSN) as db:
            lot=add_lot(db,org,loc,UUID(source),add_bin(db,org,loc))
            pending=count_stock_lot(
                db,d["tokens"][d["intake"]],org,loc,lot,uuid4(),
                1,"SYNTH-STOCKTAKE-RECOUNT",
            )
            assert balance(db,UUID(source))==(0,0)
            assert db.execute("""
                SELECT count(*) FROM auto_stock_lot_count_approval
                WHERE count_id=%s
            """,(pending,)).fetchone()==(0,)
        m={"locationId":str(loc),"sourceSkuId":source,"targetSkuId":target}
        refused=post(http,d,d["intake"],url(org)+"/merges",m)
        assert refused.status_code==409,refused.text
        with psycopg.connect(DSN) as db:
            grant(db,d["manager"],org,loc,"MANAGER")
            approval=approve_stock_lot_count(
                db,d["tokens"][d["manager"]],org,loc,pending,uuid4(),
            )
            assert approval is not None
            assert balance(db,UUID(source))==(1,0)
        assert post(http,d,d["intake"],url(org)+"/merges",m).status_code==409
