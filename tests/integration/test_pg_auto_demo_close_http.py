"""PG17 + HTTP end-to-end: approved estimate → stock → QC → demo settlement → preview."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_work_order_execution import (
    start_execution,labor_execution,issue_material,receive_material,finish_execution,
)
from roboport_api.auto_workshop_qc import record_work_order_qc
from roboport_api.auto_demo_close import simulate_cash,DemoCloseConflict
from roboport_api.auto_work_order_stock import mutate_work_order_stock
from roboport_api.auto_stock_service import AutoStockConflict
from test_pg_auto_work_order_execution_http import fixture,session,post
from test_pg_baseline import DSN


def ready(monkeypatch,with_material=True):
    d,org,loc,order,service,sku,lot=fixture(monkeypatch,with_material=with_material)
    with psycopg.connect(DSN) as db:
        start_execution(db,d["tokens"][d["intake"]],org,loc,order,uuid4())
        labor_execution(db,d["tokens"][d["manager"]],org,loc,order,
                        service,45,uuid4())
        if with_material:
            result=issue_material(db,d["tokens"][d["intake"]],org,loc,order,lot,1,uuid4())
            receive_material(db,d["tokens"][d["manager"]],org,loc,order,
                             UUID(result["issueId"]),uuid4())
        finish_execution(db,d["tokens"][d["manager"]],org,loc,order,uuid4())
        record_work_order_qc(db,d["tokens"][d["other"]],org,loc,order,
                             request_key=uuid4(),result="PASS",finding_code="NONE")
    return d,org,loc,order,service,sku,lot


def url(org,loc,order):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/demo-close"


def test_complete_demo_reconcile_reserve_seal_client_view_and_immutability(monkeypatch):
    d,org,loc,order,service,sku,lot=ready(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    base=url(org,loc,order)
    assert http.get(base).status_code==403
    manager=session(http,d,d["intake"])
    state=http.get(base)
    assert state.status_code==200,state.text
    assert state.headers["cache-control"]=="no-store"
    assert state.json()["approvedTotalMinor"]==2500
    assert state.json()["simulatedDueMinor"]==2500
    assert state.json()["releaseAllowed"] is False
    assert state.json()["legalGatePassed"] is False
    assert state.json()["unreceivedMaterialCount"]==0
    assert state.json()["outstandingReserves"]==[{"skuId":str(sku),"quantity":1}]
    assert "SIMULATED_BALANCE_OPEN" in state.json()["blockers"]
    assert post(http,base+"/seal-preview",manager).status_code==409
    cash=base+"/simulated-cash"
    assert post(http,cash,kind="SIM_PAYMENT",amountMinor=2500).status_code==403
    assert post(http,cash,manager,kind="SIM_PAYMENT",amountMinor=0).status_code==422
    assert post(http,cash,manager,kind="SIM_REFUND",amountMinor=1).status_code==409
    key=uuid4()
    paid=post(http,cash,manager,key,kind="SIM_PAYMENT",amountMinor=2500)
    assert paid.status_code==201,paid.text
    assert paid.json()["simulatedDueMinor"]==0
    repeat=post(http,cash,manager,key,kind="SIM_PAYMENT",amountMinor=2500)
    assert repeat.status_code==200 and repeat.json()["replayed"] is True
    assert post(http,cash,manager,key,kind="SIM_PAYMENT",amountMinor=200).status_code==409
    assert post(http,cash,manager,kind="SIM_PAYMENT",amountMinor=1).status_code==409
    returned=post(http,cash,manager,kind="SIM_REFUND",amountMinor=500)
    assert returned.status_code==201 and returned.json()["simulatedDueMinor"]==500
    assert post(http,cash,manager,kind="SIM_REFUND",amountMinor=2501).status_code==409
    paid_again=post(http,cash,manager,kind="SIM_PAYMENT",amountMinor=500)
    assert paid_again.status_code==201 and paid_again.json()["simulatedDueMinor"]==0
    assert post(http,base+"/seal-preview",manager).status_code==409
    warehouse=session(http,d,d["intake"])
    release=post(http,base+"/unused-reserve-release",warehouse,
                 skuId=str(sku),quantity=1)
    assert release.status_code==201,release.text
    assert release.json()["outstandingReserves"]==[]
    # Only a manager can seal; exact same key must be replay-safe.
    seal_key=uuid4()
    sealed=post(http,base+"/seal-preview",manager,seal_key)
    assert sealed.status_code==201,sealed.text
    document=sealed.json()["preview"]
    assert len(document["digest"])==64
    assert document["snapshot"]["approvedTotalMinor"]==2500
    assert document["snapshot"]["simulatedPaidMinor"]==3000
    assert document["snapshot"]["simulatedRefundedMinor"]==500
    assert document["snapshot"]["releaseAllowed"] is False
    assert sealed.json()["blockers"]==[]
    assert sealed.json()["canSealPreview"] is False
    # Технический digest проверяется по каноническому JSONB не только API,
    # но и SQL-триггером; произвольный INSERT не может изменить атрибуты.
    with psycopg.connect(DSN) as db:
        canonical=db.execute("""
          SELECT encode(sha256(convert_to(snapshot::text,'UTF8')),'hex')
          FROM auto_order_demo_handover_preview WHERE work_order_id=%s
        """,(order,)).fetchone()[0]
        assert document["digest"]==canonical
        for changed_sql, actor_id, expected_error in (
            ("jsonb_set(snapshot,'{kind}','\"RELEASED\"'::jsonb)",d["intake"],
             "invalid synthetic preview metadata"),
            ("snapshot || '{\"documentPosted\":true}'::jsonb",d["intake"],
             "invalid synthetic preview metadata"),
            ("snapshot",d["client_b"],
             "preview actor lacks live MANAGER grant"),
        ):
            with pytest.raises(psycopg.errors.RaiseException,
                               match=expected_error):
                with db.transaction():
                    db.execute(f"""
                      INSERT INTO auto_order_demo_handover_preview
                        (id,work_order_id,revision_id,actor_id,request_key,
                         fingerprint,snapshot)
                      SELECT gen_random_uuid(),work_order_id,revision_id,%s,
                             gen_random_uuid(),fingerprint,{changed_sql}
                      FROM auto_order_demo_handover_preview WHERE work_order_id=%s
                    """,(actor_id,order))
        with pytest.raises(psycopg.errors.RaiseException,
                           match="preview fingerprint mismatch"):
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_order_demo_handover_preview
                    (id,work_order_id,revision_id,actor_id,request_key,
                     fingerprint,snapshot)
                  SELECT gen_random_uuid(),work_order_id,revision_id,
                         actor_id,gen_random_uuid(),repeat('f',64),snapshot
                  FROM auto_order_demo_handover_preview WHERE work_order_id=%s
                """,(order,))
    # Регрессия независимого review: удаление обязательного nullable ключа
    # + добавление флага выдачи сохраняет count=22 и корректный SHA-256.
    # И 0113, и 0114 отдельно пропускали такой подложный snapshot.
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException,
                           match="preview key set mismatch"):
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_order_demo_handover_preview
                    (id,work_order_id,revision_id,actor_id,request_key,
                     fingerprint,snapshot)
                  SELECT gen_random_uuid(),work_order_id,revision_id,
                         actor_id,gen_random_uuid(),
                         encode(sha256(convert_to(forged.payload::text,'UTF8')),'hex'),
                         forged.payload
                  FROM auto_order_demo_handover_preview doc
                  CROSS JOIN LATERAL (
                    SELECT (doc.snapshot - 'extraRevisionId' ||
                         '{"handoverCompleted":true}'::jsonb) AS payload
                  ) forged
                  WHERE doc.work_order_id=%s
                """,(order,))
    same=post(http,base+"/seal-preview",manager,seal_key)
    assert same.status_code==200 and same.json()["replayed"] is True
    assert post(http,base+"/seal-preview",manager).status_code==409
    assert post(http,cash,manager,kind="SIM_REFUND",amountMinor=1).status_code==409
    assert post(http,base+"/unused-reserve-release",warehouse,
                skuId=str(sku),quantity=1).status_code==409
    wrong=session(http,d,d["client_b"])
    assert http.get(base).status_code==403
    client=session(http,d,d["client_a"])
    visible=http.get(base)
    assert visible.status_code==200
    assert visible.json()["preview"]["id"]==document["id"]
    assert visible.json()["releaseAllowed"] is False
    assert post(http,cash,client,kind="SIM_PAYMENT",amountMinor=1).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_order_demo_cash_event WHERE work_order_id=%s",
                          (order,)).fetchone()[0]==3
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_order_demo_cash_event SET amount_minor=1 WHERE work_order_id=%s",
                           (order,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_order_demo_handover_preview WHERE work_order_id=%s",
                           (order,))
        with pytest.raises(AutoStockConflict):
            mutate_work_order_stock(db,d["tokens"][d["intake"]],org,loc,
                                    order,sku,uuid4(),"RESERVE",1)


def test_preview_blocks_without_qc_and_revoke_hides_all(monkeypatch):
    d,org,loc,order,service,sku,lot=fixture(monkeypatch,with_material=False)
    http=TestClient(app,base_url="https://127.0.0.1")
    base=url(org,loc,order)
    manager=session(http,d,d["intake"])
    before=http.get(base)
    assert before.status_code==200
    assert "EXECUTION_OR_INDEPENDENT_QC_INCOMPLETE" in before.json()["blockers"]
    assert post(http,base+"/simulated-cash",manager,
                kind="SIM_PAYMENT",amountMinor=1000).status_code==409
    assert post(http,base+"/seal-preview",manager).status_code==409
    assert http.get(base.replace(str(loc),str(uuid4()))).status_code==403
    assert http.get(base.replace(str(order),str(uuid4()))).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.get(base).status_code==403
    assert post(http,base+"/seal-preview",manager).status_code==403



def test_concurrent_simulated_payments_cannot_overpay(monkeypatch):
    d,org,loc,order,*_=ready(monkeypatch,with_material=False)
    barrier=Barrier(2)
    def attempt(_):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                simulate_cash(db,d["tokens"][d["intake"]],org,loc,order,
                              request_key=uuid4(),kind="SIM_PAYMENT",amount_minor=2000)
            return "RECORDED"
        except DemoCloseConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==["CONFLICT","RECORDED"]
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT COALESCE(SUM(amount_minor),0) FROM auto_order_demo_cash_event
                             WHERE work_order_id=%s""",(order,)).fetchone()[0]==2000
