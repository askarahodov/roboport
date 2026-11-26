"""PG17/HTTPS: серийное выбытие по независимым актам, без потери идентичности."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_stock_serial import register_lot_serial
from roboport_api import auto_serial_receipt_provenance as provenance
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_receipt_valuation import setup_receipt,value
from test_pg_auto_stock_supplier_return_http import post
from test_pg_auto_stock_lot_allocation import allocated
from test_pg_auto_stock_ledger import balance


def test_serial_disposition_full_lifecycle(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    warehouse,manager=d["intake"],d["manager"]
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,4)
        value(db,d,org,loc,receipt,vat=601)
        serials=[register_lot_serial(db,d["tokens"][warehouse],org,loc,lot,uuid4(),
                    "SYN-"+uuid4().hex) for _ in range(4)]
        for serial in (serials[0],serials[2]):
            asserted=provenance.propose(db,d["tokens"][warehouse],org,loc,serial,receipt,uuid4())
            provenance.approve(db,d["tokens"][manager],org,loc,UUID(asserted["assertionId"]),uuid4())
    s1,s2,s3,s4=map(str,serials)
    root=f"/api/v1/dev/auto/organizations/{org}/warehouse"
    sr=root+"/supplier-returns"
    wo=root+"/writeoffs"
    scope={"locationId":str(loc)}
    supplier={**scope,"sourceReceiptId":str(receipt),"quantity":1,"reason":"Возврат брака",
              "supplierReference":"RMA-123","serialIds":[s1]}
    writeoff={**scope,"lotId":str(lot),"quantity":1,"reasonCode":"DAMAGED","note":"Списание серии","serialIds":[s2]}
    with TestClient(app,base_url="https://127.0.0.1") as http:
        assert post(http,d,warehouse,sr,{**supplier,"serialIds":[s2]}).status_code==409
        assert post(http,d,warehouse,sr,{**supplier,"quantity":2,"serialIds":[s1,s1]}).status_code==422
        assert post(http,d,warehouse,wo,{**writeoff,"serialIds":[str(uuid4())]}).status_code==409
        assert post(http,d,warehouse,wo,writeoff,csrf=False).status_code==403
        # Детерминированно проверяем именно возврат поставщику до гонки.
        separate=post(http,d,warehouse,sr,{**supplier,"serialIds":[s3]})
        assert separate.status_code==201,separate.text
        separate_payload={**scope,"documentId":separate.json()["documentId"]}
        separate_key=uuid4()
        accepted=post(http,d,manager,sr+"/approvals",separate_payload,separate_key)
        assert accepted.status_code==201,accepted.text
        assert post(http,d,manager,sr+"/approvals",separate_payload,separate_key).json()==accepted.json()
        with psycopg.connect(DSN) as db:
            assert allocated(db,lot)==3 and balance(db,sku)==(4,0)
            assert db.execute("SELECT state FROM auto_stock_serial_current WHERE serial_id=%s",(serials[2],)).fetchone()==("SUPPLIER_RETURNED",)
            assert db.execute("SELECT purchase_net_attributed_minor,purchase_vat_attributed_minor FROM auto_stock_supplier_return_acceptance WHERE id=%s",(accepted.json()["approvalId"],)).fetchone()==(1250,150)
        key=uuid4()
        created=post(http,d,warehouse,sr,supplier,key)
        assert created.status_code==201,created.text
        assert post(http,d,warehouse,sr,supplier,key).json()==created.json()
        assert post(http,d,warehouse,sr,{**supplier,"serialIds":[]},key).status_code==409
        document=created.json()["documentId"]
        # Независимое списание той же серии конкурирует с возвратом поставщику.
        duplicate=post(http,d,warehouse,wo,{**writeoff,"serialIds":[s1]})
        assert duplicate.status_code==201,duplicate.text
        canceled=post(http,d,warehouse,wo,writeoff)
        assert canceled.status_code==201,canceled.text
        decision=wo+"/"+canceled.json()["documentId"]+"/decisions"
        assert post(http,d,warehouse,decision,{**scope,"action":"CANCEL","note":"Отмена акта"}).status_code==201
        actual=post(http,d,warehouse,wo,writeoff)
        assert actual.status_code==201,actual.text
        http.cookies.set("rp_session",d["tokens"][manager])
        assert http.get(sr+"/pending",params=scope).json()["items"][0]["serialIds"]==[s1]
        snapshot=http.get(wo,params=scope).json()
        assert any(x["serialIds"]==[s2] for x in snapshot["items"])
        import json
        from pathlib import Path
        from jsonschema import Draft202012Validator,FormatChecker
        spec=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-writeoff-dev-openapi.json").read_text(encoding="utf-8"))
        Draft202012Validator({**spec,"$ref":"#/components/schemas/Workspace"},format_checker=FormatChecker()).validate(snapshot)
        approve={**scope,"documentId":document}
        assert post(http,d,warehouse,sr+"/approvals",approve).status_code==403
        barrier=Barrier(2)
        keys=[uuid4(),uuid4()]
        def attempt(i):
            with TestClient(app,base_url="https://127.0.0.1") as client:
                barrier.wait(timeout=10)
                if i==0:
                    return post(client,d,manager,sr+"/approvals",approve,keys[i])
                return post(client,d,manager,wo+"/"+duplicate.json()["documentId"]+"/decisions",
                            {**scope,"action":"APPROVE","note":"Независимое списание"},keys[i])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(attempt,range(2)))
        assert sorted(x.status_code for x in results)==[201,409],[x.text for x in results]
        with psycopg.connect(DSN) as db:
            assert allocated(db,lot)==2 and balance(db,sku)==(3,0)
            expected="SUPPLIER_RETURNED" if results[0].status_code==201 else "WRITTEN_OFF"
            assert db.execute("SELECT state FROM auto_stock_serial_current WHERE serial_id=%s",(serials[0],)).fetchone()==(expected,)
            assert db.execute("SELECT state FROM auto_stock_serial_current WHERE serial_id=%s",(serials[1],)).fetchone()==("IN_STOCK",)
            assert db.execute("SELECT count(*) FROM auto_stock_serial_disposition WHERE serial_id=%s",(serials[0],)).fetchone()==(1,)
            if results[0].status_code==201:
                assert db.execute("SELECT purchase_net_attributed_minor,purchase_vat_attributed_minor FROM auto_stock_supplier_return_acceptance WHERE document_id=%s",(document,)).fetchone()==(1250,150)
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("DELETE FROM auto_stock_serial_disposition WHERE serial_id=%s",(serials[0],))
        target=wo+"/"+actual.json()["documentId"]+"/decisions"
        payload={**scope,"action":"APPROVE","note":"Независимое списание"}
        approval_key=uuid4()
        approved=post(http,d,manager,target,payload,approval_key)
        assert approved.status_code==201,approved.text
        assert post(http,d,manager,target,payload,approval_key).status_code==200
        # Нельзя обезличенно списать последнюю зарегистрированную серию.
        plain=post(http,d,warehouse,wo,{**writeoff,"serialIds":[]})
        assert plain.status_code==201,plain.text
        assert post(http,d,manager,wo+"/"+plain.json()["documentId"]+"/decisions",payload).status_code==409
        with psycopg.connect(DSN) as db:
            assert allocated(db,lot)==1 and balance(db,sku)==(2,0)
            assert db.execute("SELECT state FROM auto_stock_serial_current WHERE serial_id=%s",(serials[1],)).fetchone()==("WRITTEN_OFF",)
            assert db.execute("SELECT state FROM auto_stock_serial_current WHERE serial_id=%s",(serials[2],)).fetchone()==("SUPPLIER_RETURNED",)
            assert db.execute("SELECT state FROM auto_stock_serial_current WHERE serial_id=%s",(serials[3],)).fetchone()==("IN_STOCK",)
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE organization_id=%s AND user_id=%s AND role_code='MANAGER'",(org,manager))
        assert post(http,d,manager,target,payload,approval_key).status_code==403
        assert post(http,d,manager,sr+"/approvals",separate_payload,separate_key).status_code==403
