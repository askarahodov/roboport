"""End-to-end HTTP security and accounting of independently approved supplier returns."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_receipt_valuation import setup_receipt,value
from test_pg_auto_stock_lot_allocation import allocated
from test_pg_auto_stock_ledger import balance


def post(client,d,who,url,data,key=None,csrf=True):
    client.cookies.set("rp_session",d["tokens"][who])
    headers={"Origin":"https://127.0.0.1","Idempotency-Key":str(key or uuid4())}
    if csrf:headers["X-CSRF-Token"]=d["csrf"][who]
    return client.post(url,headers=headers,json=data)


def test_supplier_return_http_dual_control_replay_scope_vat_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,2)
        value(db,d,org,loc,receipt,vat=301)
    endpoint=f"/api/v1/dev/auto/organizations/{org}/warehouse/supplier-returns"
    payload={"locationId":str(loc),"sourceReceiptId":str(receipt),"quantity":2,
       "reason":"Возврат несоответствующей детали","supplierReference":"RMA-2026-001"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert post(client,d,d["intake"],endpoint,payload,csrf=False).status_code==403
        assert post(client,d,d["manager"],endpoint,payload).status_code==403
        assert post(client,d,d["intake"],endpoint,
                    {**payload,"quantity":True}).status_code==422
        assert post(client,d,d["intake"],endpoint,
                    {**payload,"locationId":str(uuid4())}).status_code==403
        key=uuid4()
        created=post(client,d,d["intake"],endpoint,payload,key)
        assert created.status_code==201,created.text
        assert created.headers["cache-control"]=="no-store"
        assert post(client,d,d["intake"],endpoint,payload,key).json()==created.json()
        assert post(client,d,d["intake"],endpoint,
                    {**payload,"quantity":1},key).status_code==409
        document=created.json()["documentId"]
        assert post(client,d,d["intake"],endpoint+"/approvals",
                    {"locationId":str(loc),"documentId":document}).status_code==403
        approval_payload={"locationId":str(loc),"documentId":document}
        assert post(client,d,d["manager"],endpoint+"/approvals",
                    {**approval_payload,"locationId":str(uuid4())}).status_code==403
        approval_key=uuid4()
        approved=post(client,d,d["manager"],endpoint+"/approvals",approval_payload,approval_key)
        assert approved.status_code==201,approved.text
        assert post(client,d,d["manager"],endpoint+"/approvals",
                    approval_payload,approval_key).json()==approved.json()
        with psycopg.connect(DSN) as db:
            assert allocated(db,lot)==0 and balance(db,sku)==(1,0)
            assert db.execute("""SELECT purchase_net_attributed_minor,
                purchase_vat_attributed_minor FROM auto_stock_supplier_return_acceptance
                WHERE id=%s""",(approved.json()["approvalId"],)).fetchone()==(2500,301)
            manager=db.execute("""SELECT id FROM auto_organization_role_grant
                WHERE user_id=%s AND organization_id=%s AND location_id=%s
                  AND role_code='MANAGER' AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1""",
                (d["manager"],org,loc)).fetchone()[0]
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (manager,))
        assert post(client,d,d["manager"],endpoint+"/approvals",
                    approval_payload,approval_key).status_code==403


def test_supplier_return_http_disabled(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.post(
          f"/api/v1/dev/auto/organizations/{uuid4()}/warehouse/supplier-returns",
          json={}).status_code==404



def test_supplier_return_manager_queue_full_draft_to_approval_lifecycle(monkeypatch):
    """Scoped review, keyset page, own-draft exclusion, revoke and finance evidence."""
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,3)
        value(db,d,org,loc,receipt,vat=301)
    endpoint=f"/api/v1/dev/auto/organizations/{org}/warehouse/supplier-returns"
    review=endpoint+"/pending"
    params={"locationId":str(loc)}
    body={"locationId":str(loc),"sourceReceiptId":str(receipt),
          "quantity":1,"reason":"Возврат после осмотра",
          "supplierReference":"RMA-PENDING-001"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.get(review,params=params).status_code==403
        client.cookies.set("rp_session",d["tokens"][d["intake"]])
        assert client.get(review,params=params).status_code==403
        a=post(client,d,d["intake"],endpoint,body)
        b=post(client,d,d["intake"],endpoint,{
            **body,"supplierReference":"RMA-PENDING-002"})
        assert a.status_code==201 and b.status_code==201
        ids={a.json()["documentId"],b.json()["documentId"]}
        client.cookies.set("rp_session",d["tokens"][d["manager"]])
        first=client.get(review,params={**params,"limit":1})
        assert first.status_code==200,first.text
        assert first.headers["cache-control"]=="no-store"
        assert first.json()["syntheticOnly"] is True
        assert len(first.json()["items"])==1
        cursor=first.json()["nextDocumentId"]
        assert cursor==first.json()["items"][0]["documentId"]
        second=client.get(review,params={**params,"limit":1,
            "afterDocumentId":cursor})
        assert second.status_code==200,second.text
        assert second.json()["nextDocumentId"] is None
        assert {first.json()["items"][0]["documentId"],
                second.json()["items"][0]["documentId"]}==ids
        for item in first.json()["items"]+second.json()["items"]:
            assert item["sourceReceiptId"]==str(receipt)
            assert item["lotId"]==str(lot)
            assert item["quantity"]==1
            assert item["reason"]=="Возврат после осмотра"
            assert item["supplierReference"].startswith("RMA-PENDING")
            for forbidden in ("createdBy","acceptedBy","requestId","customerId","vehicleId"):
                assert forbidden not in item
        assert str(d["vehicle"]) not in first.text
        assert str(d["intake"]) not in first.text
        assert client.get(review,params={**params,"limit":0}).status_code==422
        assert client.get(review,params={**params,"limit":51}).status_code==422
        assert client.get(review,params={**params,"afterDocumentId":"bad"}).status_code==422
        assert client.get(review,params={"locationId":str(uuid4())}).status_code==403

        chosen=first.json()["items"][0]["documentId"]
        approved=post(client,d,d["manager"],endpoint+"/approvals",
                      {"locationId":str(loc),"documentId":chosen})
        assert approved.status_code==201,approved.text
        pending=client.get(review,params=params)
        assert pending.status_code==200,pending.text
        assert [row["documentId"] for row in pending.json()["items"]]==[
            next(i for i in ids if i!=chosen)]
        with psycopg.connect(DSN) as db:
            assert allocated(db,lot)==2
            assert balance(db,sku)==(3,0)
            grant_id=db.execute("""SELECT id FROM auto_organization_role_grant
                WHERE user_id=%s AND organization_id=%s AND location_id=%s
                  AND role_code='MANAGER' AND revoked_at IS NULL
                  ORDER BY created_at DESC LIMIT 1""",
                (d["manager"],org,loc)).fetchone()[0]
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (grant_id,))
        assert client.get(review,params=params).status_code==403


def test_competing_return_documents_cannot_spend_another_receipt_quantity_or_vat(monkeypatch):
    """Two distinct documents, enough physical stock, one source receipt budget."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from roboport_api.auto_stock_receipt_document import (
        create_receipt_document,accept_receipt_document,
    )
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,3)
        value(db,d,org,loc,receipt,vat=601)
        # Another valued receipt in the SAME lot must not increase the first
        # receipt's return budget. Physical stock can satisfy both drafts.
        other=create_receipt_document(db,d["tokens"][d["intake"]],org,loc,lot,
                                      uuid4(),4,"SECOND-SOURCE")
        accept_receipt_document(db,d["tokens"][d["manager"]],org,loc,other,uuid4())
        value(db,d,org,loc,other,unit=2000,vat=799)
        assert balance(db,sku)==(8,0) and allocated(db,lot)==7
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/supplier-returns"
    body={"locationId":str(loc),"sourceReceiptId":str(receipt),"quantity":2,
          "reason":"Контроль возврата","supplierReference":"RMA-COMPETING"}
    keys=[uuid4(),uuid4()]
    with TestClient(app,base_url="https://127.0.0.1") as http:
        drafts=[post(http,d,d["intake"],base,body,key) for key in keys]
        assert all(x.status_code==201 for x in drafts),[x.text for x in drafts]
        docs=[x.json()["documentId"] for x in drafts]
        assert docs[0]!=docs[1]
    gate=Barrier(2)
    approval_keys=[uuid4(),uuid4()]
    def compete(index):
        with TestClient(app,base_url="https://127.0.0.1") as http:
            gate.wait(timeout=10)
            return post(http,d,d["manager"],base+"/approvals",
                {"locationId":str(loc),"documentId":docs[index]},approval_keys[index])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(compete,range(2)))
    assert sorted(x.status_code for x in responses)==[201,409],[x.text for x in responses]
    winner=next(i for i,x in enumerate(responses) if x.status_code==201)
    loser=1-winner
    with psycopg.connect(DSN) as db:
        assert balance(db,sku)==(6,0) and allocated(db,lot)==5
        assert db.execute("""SELECT sum(d.quantity),sum(a.purchase_net_attributed_minor),
                   sum(a.purchase_vat_attributed_minor)
            FROM auto_stock_supplier_return_document d
            JOIN auto_stock_supplier_return_acceptance a ON a.document_id=d.id
            WHERE d.source_receipt_id=%s""",(receipt,)).fetchone()==(2,2500,400)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        # A new impossible draft is rejected, while exact old draft and
        # approval replay are still valid. The losing draft stays in audit.
        denied=post(http,d,d["intake"],base,body)
        assert denied.status_code==409,denied.text
        assert "no-store" in denied.headers["cache-control"]
        assert post(http,d,d["intake"],base,body,keys[winner]).json()==drafts[winner].json()
        assert post(http,d,d["manager"],base+"/approvals",
            {"locationId":str(loc),"documentId":docs[winner]},approval_keys[winner]).json()==responses[winner].json()
        assert post(http,d,d["manager"],base+"/approvals",
            {"locationId":str(loc),"documentId":docs[loser]},approval_keys[loser]).status_code==409
        last=post(http,d,d["intake"],base,{**body,"quantity":1})
        assert last.status_code==201,last.text
        assert post(http,d,d["manager"],base+"/approvals",
            {"locationId":str(loc),"documentId":last.json()["documentId"]}).status_code==201
        assert post(http,d,d["intake"],base,{**body,"quantity":1}).status_code==409
        separate=post(http,d,d["intake"],base,{**body,"sourceReceiptId":str(other),"quantity":1})
        assert separate.status_code==201,separate.text
        assert post(http,d,d["manager"],base+"/approvals",
            {"locationId":str(loc),"documentId":separate.json()["documentId"]}).status_code==201
    with psycopg.connect(DSN) as db:
        assert balance(db,sku)==(4,0) and allocated(db,lot)==3
        rows=db.execute("""SELECT d.source_receipt_id,sum(d.quantity),
              sum(a.purchase_net_attributed_minor),sum(a.purchase_vat_attributed_minor)
            FROM auto_stock_supplier_return_document d
            JOIN auto_stock_supplier_return_acceptance a ON a.document_id=d.id
            WHERE d.source_receipt_id IN (%s,%s) GROUP BY d.source_receipt_id""",
            (receipt,other)).fetchall()
        assert {x[0]:x[1:] for x in rows}=={receipt:(3,3750,601),other:(1,2000,199)}
        assert db.execute("""SELECT count(*) FROM auto_stock_supplier_return_document
                             WHERE source_receipt_id=%s""",(receipt,)).fetchone()==(3,)
        assert db.execute("""SELECT count(*) FROM auto_stock_lot_disposition
                             WHERE lot_id=%s AND action='SUPPLIER_RETURN'""",(lot,)).fetchone()==(3,)


def test_supplier_return_terminal_decisions_preserve_audit_and_no_postings(monkeypatch):
    from test_pg_auto_stock_service import grant
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,3)
        value(db,d,org,loc,receipt,vat=601)
        grant(db,d["manager"],org,loc,"WAREHOUSE")
    endpoint=f"/api/v1/dev/auto/organizations/{org}/warehouse/supplier-returns"
    params={"locationId":str(loc)}
    payload={**params,"sourceReceiptId":str(receipt),"quantity":2,
             "reason":"Синтетический возврат","supplierReference":"RMA-TERMINAL"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        created=post(client,d,d["intake"],endpoint,payload)
        assert created.status_code==201,created.text
        doc=created.json()["documentId"]
        body={**params,"documentId":doc,"action":"CANCEL","note":"Ошибочный черновик"}
        client.cookies.set("rp_session",d["tokens"][d["intake"]])
        own=client.get(endpoint+"/pending",params={**params,"view":"own"})
        assert own.status_code==200 and [x["documentId"] for x in own.json()["items"]]==[doc]
        assert post(client,d,d["manager"],endpoint+"/decisions",body).status_code==403
        assert post(client,d,d["intake"],endpoint+"/decisions",body,csrf=False).status_code==403
        assert post(client,d,d["intake"],endpoint+"/decisions",{**body,"note":" "}).status_code==422
        assert post(client,d,d["intake"],endpoint+"/decisions",
                    {**body,"locationId":str(uuid4())}).status_code==403
        key=uuid4()
        canceled=post(client,d,d["intake"],endpoint+"/decisions",body,key)
        assert canceled.status_code==201,canceled.text
        assert canceled.headers["cache-control"]=="no-store"
        assert post(client,d,d["intake"],endpoint+"/decisions",body,key).json()==canceled.json()
        assert post(client,d,d["intake"],endpoint+"/decisions",{**body,"note":"Другая причина"},key).status_code==409
        assert post(client,d,d["intake"],endpoint+"/decisions",body).status_code==409
        assert client.get(endpoint+"/pending",params={**params,"view":"own"}).json()["items"]==[]
        assert post(client,d,d["manager"],endpoint+"/approvals",{**params,"documentId":doc}).status_code==409
        assert client.get(endpoint+"/pending",params=params).json()["items"]==[]
        second=post(client,d,d["intake"],endpoint,payload).json()["documentId"]
        rejection={**params,"documentId":second,"action":"REJECT","note":"Отказ в утверждении"}
        assert post(client,d,d["intake"],endpoint+"/decisions",rejection).status_code==403
        rejected=post(client,d,d["manager"],endpoint+"/decisions",rejection)
        assert rejected.status_code==201,rejected.text
        assert post(client,d,d["manager"],endpoint+"/approvals",{**params,"documentId":second}).status_code==409
        detail=f"/api/v1/dev/auto/organizations/{org}/warehouse/reconciliation/{sku}"
        report=client.get(detail,params={**params,"kind":"supplier-returns"})
        assert report.status_code==200,report.text
        rows={x["documentId"]:x for x in report.json()["items"]}
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator, FormatChecker
        spec=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-supplier-return-dev-openapi.json").read_text(encoding="utf-8"))
        for schema, payload in [("ReconciliationReturn",item) for item in rows.values()]+[
                ("DecisionReceipt",canceled.json()),("PendingPage",own.json())]:
            Draft202012Validator({**spec,"$ref":"#/components/schemas/"+schema},
                                 format_checker=FormatChecker()).validate(payload)
        assert rows[doc]["status"]=="CANCELED" and rows[second]["status"]=="REJECTED"
        for ident,decision in [(doc,canceled),(second,rejected)]:
            assert rows[ident]["decision"]["id"]==decision.json()["decisionId"]
            assert rows[ident]["attribution"] is None and rows[ident]["dispositionId"] is None
        receipts=client.get(detail,params={**params,"kind":"receipts"}).json()["items"]
        assert next(x for x in receipts if x["documentId"]==str(receipt))["pendingReturnCount"]==0
        with psycopg.connect(DSN) as db:
            assert balance(db,sku)==(4,0) and allocated(db,lot)==3
            assert db.execute("SELECT count(*) FROM auto_stock_supplier_return_acceptance WHERE document_id IN (%s,%s)",(doc,second)).fetchone()==(0,)
            assert db.execute("SELECT count(*) FROM auto_stock_lot_disposition WHERE lot_id=%s",(lot,)).fetchone()==(0,)
            grant_id=db.execute("""SELECT id FROM auto_organization_role_grant
                WHERE user_id=%s AND organization_id=%s AND location_id=%s
                AND role_code='WAREHOUSE' AND revoked_at IS NULL""",(d["intake"],org,loc)).fetchone()[0]
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant_id,))
        assert post(client,d,d["intake"],endpoint+"/decisions",body,key).status_code==403


def test_supplier_return_approval_and_cancel_are_mutually_exclusive(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,2)
        value(db,d,org,loc,receipt,vat=301)
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/supplier-returns"
    with TestClient(app,base_url="https://127.0.0.1") as client:
        created=post(client,d,d["intake"],base,{"locationId":str(loc),"sourceReceiptId":str(receipt),
            "quantity":2,"reason":"Синтетическая гонка","supplierReference":"RMA-RACE"})
        assert created.status_code==201,created.text
    doc=created.json()["documentId"]
    payload={"locationId":str(loc),"documentId":doc}
    gate=Barrier(2)
    def compete(cancel):
        with TestClient(app,base_url="https://127.0.0.1") as client:
            gate.wait(timeout=10)
            if cancel:
                return post(client,d,d["intake"],base+"/decisions",
                            {**payload,"action":"CANCEL","note":"Отмена при конкуренции"})
            return post(client,d,d["manager"],base+"/approvals",payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        approved,canceled=list(pool.map(compete,[False,True]))
    assert sorted([approved.status_code,canceled.status_code])==[201,409],[approved.text,canceled.text]
    with psycopg.connect(DSN) as db:
        terminal=db.execute("""SELECT action,approval_id FROM auto_stock_supplier_return_terminal
                              WHERE document_id=%s""",(doc,)).fetchall()
        assert len(terminal)==1
        if approved.status_code==201:
            assert terminal[0][0]=="APPROVE" and str(terminal[0][1])==approved.json()["approvalId"]
            assert balance(db,sku)==(1,0) and allocated(db,lot)==0
        else:
            assert terminal==[("CANCEL",None)]
            assert balance(db,sku)==(3,0) and allocated(db,lot)==2
        assert db.execute("SELECT count(*) FROM auto_stock_supplier_return_acceptance WHERE document_id=%s",
                          (doc,)).fetchone()==(int(approved.status_code==201),)


def test_supplier_return_terminal_sql_guard_rolls_back_physical_approval():
    """Даже прежняя SQL-процедура не может утвердить отменённый документ."""
    import pytest
    from roboport_api.auto_stock_supplier_return_document import (
        draft_supplier_return,decide_supplier_return,
    )
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,2)
        value(db,d,org,loc,receipt,vat=301)
        document=draft_supplier_return(db,d["tokens"][d["intake"]],org,loc,
            receipt,uuid4(),2,"Синтетический акт","RMA-SQL-TERMINAL")
        decision=decide_supplier_return(db,d["tokens"][d["intake"]],org,loc,
            document,uuid4(),"CANCEL","Ошибочный акт")
        with pytest.raises((psycopg.errors.RaiseException,psycopg.errors.UniqueViolation)):
            with db.transaction():
                db.execute("SELECT set_config('roboport.supplier_return_actor',%s,true)",(str(d["manager"]),))
                db.execute("SELECT accept_auto_stock_supplier_return_document(%s,%s)",(document,uuid4()))
        assert balance(db,sku)==(3,0) and allocated(db,lot)==2
        assert db.execute("SELECT count(*) FROM auto_stock_supplier_return_acceptance WHERE document_id=%s",(document,)).fetchone()==(0,)
        assert db.execute("SELECT count(*) FROM auto_stock_lot_disposition WHERE lot_id=%s",(lot,)).fetchone()==(0,)
        assert db.execute("SELECT id,action FROM auto_stock_supplier_return_terminal WHERE document_id=%s",(document,)).fetchone()==(decision,"CANCEL")
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_supplier_return_terminal WHERE id=%s",(decision,))
