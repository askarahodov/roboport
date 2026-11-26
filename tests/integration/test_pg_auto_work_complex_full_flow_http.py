"""Synthetic full vertical: approved branch tariffs, complex stock, assignment,
additional consent, authorized labor, material handoff and independent QC.
"""
from datetime import datetime,timedelta,timezone
from io import BytesIO
from zipfile import ZipFile
import json
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_lot_work_order_return import return_work_order_to_lot
from roboport_api.auto_stock_service import AutoStockConflict
from test_pg_auto_work_order_stock import act as work_stock

from test_pg_baseline import DSN
from test_pg_auto_work_complexes_http import setup,paths,complex_body
from test_pg_auto_work_order_estimate_http import token,path as estimate_path
from test_pg_auto_work_order_execution_http import url as execution_path,post
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_lot_allocation import act as allocate_lot
from roboport_api.auto_workshop_qc import submit_work_order_inspection
from test_pg_auto_intake_act_http import custody,synthetic_png,publish as publish_act
from test_pg_auto_intake_act_review_http import review as review_act
from test_pg_auto_diagnostic_estimate_http import finding
from test_pg_auto_document_lifecycle_http import event as document_event
from scripts.verify_auto_technical_dossier import verify_dossier_bytes


@pytest.mark.parametrize("extra_decision",["APPROVED","DECLINED"])
def test_real_full_synthetic_complex_to_extra_consent_to_independent_qc(monkeypatch,extra_decision):
    d,org,loc,order,base_service,complex_service,part,consumable,http=setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MANAGER")
        grant(db,d["manager"],org,loc,"MECHANIC")
        grant(db,d["other"],org,loc,"MECHANIC")
        grant(db,d["intake"],org,loc,"WAREHOUSE")
        extra_service=uuid4()
        db.execute("""INSERT INTO auto_service_catalog
          (id,organization_id,location_id,code,title,duration_minutes)
          VALUES(%s,%s,%s,'AFTER-SCOPE','SYNTHETIC ADDITIONAL LABOR',30)
        """,(extra_service,org,loc))
        starts=datetime.now(timezone.utc)+timedelta(hours=2)
        ends=starts+timedelta(hours=2)
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)
        """,(uuid4(),org,loc,d["manager"],starts-timedelta(minutes=5),
             ends+timedelta(minutes=5)))
        stock_bin=add_bin(db,org,loc)
        part_lot=add_lot(db,org,loc,part,stock_bin)
        consumable_lot=add_lot(db,org,loc,consumable,stock_bin)
        allocate_lot(db,d,org,loc,part_lot,"ALLOCATE",2)
        allocate_lot(db,d,org,loc,consumable_lot,"ALLOCATE",1)
        submit_work_order_inspection(db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=1234,fuel_percent=60,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="DAMAGE")

    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    catalog,selected=paths(org,loc,order)
    manager=token(http,d,d["intake"])

    # Один и тот же заказ: две неизменяемые версии акта с фото,
    # спор клиента и техническое подтверждение только актуальной версии.
    acts=root+f"/orders/{order}/intake-acts"
    raw=synthetic_png()
    first=publish_act(http,manager,acts,custody(raw))
    assert first.status_code==201,first.text
    old_act=first.json()["items"][0]
    customer=token(http,d,d["client_a"])
    assert review_act(http,acts,old_act,customer,decision="DISPUTED",
                      reason="INVENTORY_MISMATCH").status_code==201
    manager=token(http,d,d["intake"])
    second=publish_act(http,manager,acts,custody(raw,condition="MISSING"),version=1)
    assert second.status_code==201,second.text
    act=second.json()["items"][-1]
    assert act["version"]==2 and act["sha256"]!=old_act["sha256"]
    customer=token(http,d,d["client_a"])
    assert review_act(http,acts,old_act,customer).status_code==409
    acknowledged=review_act(http,acts,act,customer)
    assert acknowledged.status_code==201,acknowledged.text
    assert acknowledged.json()["locked"] is True
    assert acknowledged.json()["legalSignatureStatus"]=="NOT_IMPLEMENTED"
    assert http.get(act["photos"][0]["path"]).status_code==200
    token(http,d,d["client_b"])
    assert http.get(act["photos"][0]["path"]).status_code==403
    manager=token(http,d,d["intake"])
    assert publish_act(http,manager,acts,custody(raw),version=2).status_code==409

    # Governance is real PG17: proposal author cannot independently approve.
    policies=root+"/tariff-policies"
    proposed=http.post(policies,headers=manager,json={
        "requestKey":str(uuid4()),"expectedVersion":0,
        "qualificationCode":"MASTER","rateMinorPerHour":10001,
        "maxDiscountBps":1000})
    assert proposed.status_code==201,proposed.text
    policy_id=proposed.json()["items"][0]["id"]
    assert http.post(policies+"/"+policy_id+"/approve",headers=manager,json={
        "requestKey":str(uuid4())}).status_code==403
    checker=token(http,d,d["manager"])
    assert http.post(policies+"/"+policy_id+"/approve",headers=checker,json={
        "requestKey":str(uuid4())}).status_code==201

    manager=token(http,d,d["intake"])
    defined=http.post(catalog,headers=manager,json=complex_body(
        complex_service,part,consumable))
    assert defined.status_code==201,defined.text
    chosen=http.post(selected,headers=manager,json={
        "requestKey":str(uuid4()),"complexId":defined.json()["complex"]["id"]})
    assert chosen.status_code==201,chosen.text
    assert len(chosen.json()["items"][0]["materialReceipts"])==2

    def priced(service):
        return {"serviceId":str(service),"qualificationCode":"MASTER",
                "rateMinorPerHour":10001,"quantity":2,
                "coefficientBps":12500,"discountBps":1000}

    diagnostics=root+f"/orders/{order}/diagnostics"
    mechanic=token(http,d,d["manager"])
    diagnosed=http.post(diagnostics,headers=mechanic,json=finding(base_service))
    assert diagnosed.status_code==201,diagnosed.text
    manager=token(http,d,d["intake"])
    recommendations=root+f"/orders/{order}/recommendations"
    suggested=http.post(recommendations,headers=manager,json={
        "requestKey":str(uuid4()),"serviceId":str(base_service),
        "reasonCode":"PERIODIC","dueDate":(datetime.now(timezone.utc).date()+timedelta(days=30)).isoformat(),
        "dueKm":2000})
    assert suggested.status_code==201,suggested.text
    recommendation=suggested.json()["items"][0]
    customer=token(http,d,d["client_a"])
    assert http.get(recommendations+"/customer").json()["items"][0]["id"]==recommendation["id"]
    manager=token(http,d,d["intake"])
    estimate=estimate_path(org,loc,order)
    revision=http.post(estimate,headers=manager,json={
        "requestKey":str(uuid4()),"expectedVersion":0,
        "workRates":[priced(base_service),priced(complex_service)]})
    assert revision.status_code==201,revision.text
    snapshot=revision.json()["items"][0]
    labor_sum=sum(line["amountMinor"] for line in snapshot["lines"])
    assert snapshot["totalMinor"]==labor_sum+1600
    assert len(snapshot["materialLines"])==2
    assert len(snapshot["tariffs"])==2
    assert snapshot["diagnostics"]==[{"serviceId":str(base_service),
        "findingCode":"FAULT_CONFIRMED","severityCode":"SOON"}]
    assert all(p["policyId"]==policy_id for p in snapshot["tariffs"])

    customer=token(http,d,d["client_a"])
    from pathlib import Path
    from jsonschema import Draft202012Validator,FormatChecker
    spec=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-customer-orders-dev-openapi.json").read_text(encoding="utf-8"))
    history=http.get("/api/v1/dev/auto/customer/orders/"+str(order))
    assert history.status_code==200,history.text
    Draft202012Validator({**spec,"$ref":"#/components/schemas/Detail"},
        format_checker=FormatChecker()).validate(history.json())
    assert history.json()["estimates"][-1]==snapshot
    assert history.json()["canDecide"] is True
    assert http.get(estimate).json()["items"][0]["materialLines"]==snapshot["materialLines"]
    assert http.post(estimate+"/decision",headers=customer,json={
        "requestKey":str(uuid4()),"revisionId":snapshot["id"],
        "decision":"APPROVED"}).status_code==201

    manager=token(http,d,d["intake"])
    assert http.post(estimate,headers=manager,json={
        "requestKey":str(uuid4()),"expectedVersion":1,
        "workRates":[priced(base_service),priced(complex_service)]}).status_code==409

    # The assigned executor needs an active shift and must accept personally.
    manager=token(http,d,d["intake"])
    assignment=http.post(root+f"/orders/{order}/assignments",headers=manager,
        json={"requestKey":str(uuid4()),"mechanicId":str(d["manager"]),
              "startsAt":starts.isoformat(),"endsAt":ends.isoformat()})
    assert assignment.status_code==201,assignment.text
    mechanic=token(http,d,d["manager"])
    accepted=http.post(root+"/assignments/"+assignment.json()["assignmentId"]+"/accept",
        headers=mechanic,json={"requestKey":str(uuid4())})
    assert accepted.status_code==201,accepted.text
    execution=execution_path(org,loc,order)
    manager=token(http,d,d["intake"])
    assert post(http,execution+"/start",manager).status_code==201
    mechanic=token(http,d,d["manager"])
    for service in (base_service,complex_service):
        labor=post(http,execution+"/labor",mechanic,
                   serviceId=str(service),actualMinutes=40)
        assert labor.status_code==201,labor.text

    # A supplemental scope is not silently inherited from the base consent.
    extra_path=root+f"/orders/{order}/extras"
    manager=token(http,d,d["intake"])
    supplemental=http.post(extra_path,headers=manager,json={
        "baseRevisionId":snapshot["id"],"requestKey":str(uuid4()),
        "expectedVersion":0,"workRates":[priced(extra_service)]})
    assert supplemental.status_code==201,supplemental.text
    extra=supplemental.json()["items"][0]
    mechanic=token(http,d,d["manager"])
    assert post(http,execution+"/labor",mechanic,
        serviceId=str(extra_service),actualMinutes=20).status_code==409
    assert post(http,execution+"/finish",mechanic).status_code==409
    customer=token(http,d,d["client_a"])
    assert http.get(extra_path).json()["items"][0]["digest"]==extra["digest"]
    assert http.post(extra_path+"/decision",headers=customer,json={
        "revisionId":extra["id"],"requestKey":str(uuid4()),
        "decision":extra_decision}).status_code==201
    mechanic=token(http,d,d["manager"])
    assert post(http,execution+"/labor",mechanic,
        serviceId=str(extra_service),actualMinutes=20).status_code==(
            201 if extra_decision=="APPROVED" else 409)
    other=token(http,d,d["other"])
    # Use an approved base service in both branches to isolate assignment ACL.
    assert post(http,execution+"/labor",other,
        serviceId=str(base_service),actualMinutes=20).status_code==403

    # Actual allocated lot issues and handoffs consume the package reservations.
    # Labor alone never fulfills the reserved stock of a selected complex.
    mechanic=token(http,d,d["manager"])
    assert post(http,execution+"/finish",mechanic).status_code==409
    warehouse=token(http,d,d["intake"])
    issue_ids=[]
    for lot,quantity in ((part_lot,2),(consumable_lot,1)):
        issue=post(http,execution+"/materials/issue",warehouse,
                   lotId=str(lot),quantity=quantity)
        assert issue.status_code==201,issue.text
        issue_ids.append(issue.json()["issueId"])
    mechanic=token(http,d,d["manager"])
    for issue_id in issue_ids:
        receipt=post(http,execution+"/materials/receive",mechanic,issueId=issue_id)
        assert receipt.status_code==201,receipt.text
    # Warehouse return after a mechanic handoff reverses actual stock usage;
    # the old immutable handoff must not count as retained complex material.
    with psycopg.connect(DSN) as db:
        returned=return_work_order_to_lot(
            db,d["tokens"][d["intake"]],org,loc,order,part_lot,uuid4(),1)
        assert returned
        execution_id=db.execute("""
          SELECT id FROM auto_work_order_execution WHERE work_order_id=%s
        """,(order,)).fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_execution_finish
                  (id,execution_id,actor_id,request_key) VALUES(%s,%s,%s,%s)
                """,(uuid4(),execution_id,d["manager"],uuid4()))
    assert post(http,execution+"/finish",mechanic).status_code==409
    # Re-reserve and re-issue the returned unit, then acknowledge again.
    with psycopg.connect(DSN) as db:
        work_stock(db,d,org,loc,order,part,"RESERVE",1)
    warehouse=token(http,d,d["intake"])
    replacement=post(http,execution+"/materials/issue",warehouse,
                     lotId=str(part_lot),quantity=1)
    assert replacement.status_code==201,replacement.text
    mechanic=token(http,d,d["manager"])
    accepted_replacement=post(http,execution+"/materials/receive",mechanic,
                     issueId=replacement.json()["issueId"])
    assert accepted_replacement.status_code==201,accepted_replacement.text
    finished=post(http,execution+"/finish",mechanic)
    assert finished.status_code==201,finished.text
    # Completed execution cannot lose a mandatory complex component later.
    with psycopg.connect(DSN) as db:
        with pytest.raises(AutoStockConflict):
            return_work_order_to_lot(
                db,d["tokens"][d["intake"]],org,loc,order,part_lot,uuid4(),1)
    assert finished.json()["status"]=="READY_FOR_QC"
    assert finished.json()["approvedExtraVersion"]==(1 if extra_decision=="APPROVED" else None)
    self_qc=post(http,root+f"/orders/{order}/qc",mechanic,
                 result="PASS",findingCode="NONE")
    assert self_qc.status_code==403
    independent=token(http,d,d["other"])
    qc_path=root+f"/orders/{order}/qc"
    failed=post(http,qc_path,independent,result="FAIL",findingCode="REWORK")
    assert failed.status_code==201,failed.text
    assert post(http,qc_path,independent,result="PASS",findingCode="NONE").status_code==409
    close=root+f"/orders/{order}/demo-close"
    manager=token(http,d,d["intake"])
    total=snapshot["totalMinor"]+(extra["totalMinor"] if extra_decision=="APPROVED" else 0)
    assert post(http,close+"/simulated-cash",manager,kind="SIM_PAYMENT",
                amountMinor=total).status_code==409
    assert post(http,close+"/seal-preview",manager).status_code==409
    mechanic=token(http,d,d["manager"])
    reworked=post(http,qc_path+"/rework",mechanic,
        failEventId=failed.json()["qcEvents"][-1]["id"],actionCode="ADJUSTMENT")
    assert reworked.status_code==201,reworked.text
    independent=token(http,d,d["other"])
    qc=post(http,qc_path,independent,result="PASS",findingCode="NONE")
    assert qc.status_code==201,qc.text
    assert qc.json()["releaseAllowed"] is False

    # Закрытие именно того же заказа: отклонённые допработы не оплачиваются.
    manager=token(http,d,d["intake"])
    before=http.get(close)
    assert before.status_code==200,before.text
    assert before.json()["approvedTotalMinor"]==total
    assert before.json()["approvedExtraMinor"]==(extra["totalMinor"] if extra_decision=="APPROVED" else 0)
    assert post(http,close+"/simulated-cash",manager,kind="SIM_PAYMENT",
                amountMinor=total+1).status_code==409
    paid=post(http,close+"/simulated-cash",manager,kind="SIM_PAYMENT",amountMinor=total)
    assert paid.status_code==201,paid.text
    assert paid.json()["simulatedDueMinor"]==0
    sealed=post(http,close+"/seal-preview",manager)
    assert sealed.status_code==201,sealed.text
    preview=sealed.json()["preview"]
    assert preview["snapshot"]["approvedTotalMinor"]==total
    assert preview["snapshot"]["releaseAllowed"] is False
    assert document_event(http,close,manager,act,preview,"POSTED").status_code==201
    customer=token(http,d,d["client_a"])
    assert document_event(http,close,customer,act,preview,"CLIENT_ACK").status_code==201
    manager=token(http,d,d["intake"])
    closed=document_event(http,close,manager,act,preview,"CLOSED_TECHNICAL")
    assert closed.status_code==201,closed.text
    assert closed.json()["status"]=="CLOSED_TECHNICAL"
    assert closed.json()["releaseAllowed"] is False
    dossier=close+"/technical-dossier.zip"
    archive=http.get(dossier)
    assert archive.status_code==200,archive.text[:200] if archive.status_code!=200 else ""
    verified=verify_dossier_bytes(archive.content,expected_sha256=archive.headers["x-technical-dossier-sha256"])
    assert verified["verified"] is True and verified["photos"]==1
    with ZipFile(BytesIO(archive.content)) as package:
        manifest=json.loads(package.read("manifest.json"))
        assert manifest["organizationId"]==str(org)
        assert manifest["locationId"]==str(loc)
        assert manifest["workOrderId"]==str(order)
        assert manifest["intakeActId"]==act["id"]
        assert manifest["intakeDigest"]==act["sha256"]
        assert manifest["previewId"]==preview["id"]
        assert manifest["previewDigest"]==preview["digest"]
        assert json.loads(package.read("preview.json"))==preview["snapshot"]
        settlement=json.loads(package.read("settlement.json"))
        assert settlement["simulatedPaidMinor"]==total
        assert settlement["simulatedDueMinor"]==0
        assert settlement["outstandingReserves"]==[]
        assert settlement["unreceivedMaterialCount"]==0
    customer=token(http,d,d["client_a"])
    assert http.get(dossier).content==archive.content
    token(http,d,d["client_b"])
    assert http.get(dossier).status_code==403
    with psycopg.connect(DSN) as db:
        assert balance(db,part)==(0,0)
        assert balance(db,consumable)==(2,0)
        assert db.execute("""SELECT count(*) FROM auto_work_order_labor_entry l
          JOIN auto_work_order_execution e ON e.id=l.execution_id
          WHERE e.work_order_id=%s""",(order,)).fetchone()==(
              3 if extra_decision=="APPROVED" else 2,)
        assert db.execute("""SELECT count(*) FROM auto_work_complex_material_reserved r
          JOIN auto_work_order_complex_selection s ON s.id=r.selection_id
          WHERE s.work_order_id=%s""",(order,)).fetchone()==(2,)
        assert db.execute("""SELECT count(*) FROM auto_stock_mechanic_handoff h
          JOIN auto_stock_lot_work_order_issue i ON i.id=h.issue_id
          WHERE i.work_order_id=%s""",(order,)).fetchone()==(3,)

    if extra_decision=="APPROVED":
        _verify_tenant_staff_lifecycle(monkeypatch,http,d,org,loc,order,archive.content)

    # Отзыв исходной связи закрывает даже уже технически закрытый архив.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    token(http,d,d["client_a"])
    assert http.get(dossier).status_code==403
    token(http,d,d["intake"])
    assert http.get(dossier).status_code==403


def _verify_tenant_staff_lifecycle(monkeypatch,http,d,org,loc,order,archive):
    """Две реальные закрытые вертикали, чужой филиал и живой ADMIN revoke."""
    from zoneinfo import ZoneInfo
    from test_pg_auto_mechanic_productivity_http import _close
    from test_pg_auto_document_access_http import admin

    foreign,other_org,other_loc,other_order=_close(monkeypatch)
    sibling=uuid4()
    actor=foreign["intake"]
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
        db.execute("""INSERT INTO auto_service_location(id,code,organization_id)
                      VALUES(%s,%s,%s)""",(sibling,"LOC_"+sibling.hex[:10].upper(),org))
        zone=db.execute("SELECT timezone_name FROM auto_service_location WHERE id=%s",
                        (loc,)).fetchone()[0]
        day=db.execute("""SELECT e.created_at FROM auto_order_demo_document_event e
            WHERE e.work_order_id=%s AND e.action='CLOSED_TECHNICAL'""",
            (order,)).fetchone()[0].astimezone(ZoneInfo(zone)).date().isoformat()

    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    order_root=root+f"/orders/{order}"
    other_root=f"/api/v1/dev/auto/organizations/{other_org}/locations/{other_loc}"
    report=root+"/reports/closed-orders"
    query={"fromDay":day,"toDay":day}
    routes=[order_root+suffix for suffix in (
        "/customer","/overview","/stock","/estimates","/intake-acts",
        "/execution","/demo-close","/demo-close/technical-dossier.zip")]
    routes.extend((report,report+"/export"))
    def read(path):
        return http.get(path,params=query if path.startswith(report) else None)
    def assert_denied():
        for path in routes:
            response=read(path)
            assert response.status_code==403,(path,response.text[:160])
            assert "no-store" in response.headers["cache-control"]
            assert "x-technical-dossier-sha256" not in response.headers
            assert str(order) not in response.text
        # Прямой POST с корректным CSRF чужого сотрудника не создаёт проводку.
        headers=token(http,foreign,actor)
        result=post(http,order_root+"/demo-close/simulated-cash",headers,
                    kind="SIM_PAYMENT",amountMinor=1)
        assert result.status_code==403,result.text

    token(http,d,d["intake"])
    original={}
    for path in routes:
        response=read(path)
        assert response.status_code==200,(path,response.status_code)
        original[path]=response.content
    assert original[order_root+"/demo-close/technical-dossier.zip"]==archive
    assert http.get(order_root+"/stock").json()["items"]
    assert http.get(report,params=query).json()["summary"]["orderCount"]==1

    token(http,foreign,actor)
    own=other_root+f"/orders/{other_order}/demo-close/technical-dossier.zip"
    own_archive=http.get(own)
    assert own_archive.status_code==200
    assert own_archive.content!=archive
    assert_denied()
    # MANAGER чужой компании не может назначить себя в организацию A.
    staff=f"/api/v1/dev/auto/organizations/{org}/staff"
    payload={"userId":str(actor),"role":"MANAGER","locationId":str(loc)}
    headers=token(http,foreign,actor)
    assert http.post(staff,headers=headers,json=payload).status_code==403
    assert http.post(staff,headers=headers,json={**payload,"role":"ADMIN"}).status_code==403

    # Выдача только в соседнем филиале той же организации не открывает заказ A.
    owner=token(http,d,d["other"])
    sibling_grant=http.post(staff,headers=owner,json={**payload,"locationId":str(sibling)})
    assert sibling_grant.status_code==201,sibling_grant.text
    token(http,foreign,actor)
    assert_denied()
    # Подмена tenant/branch в URL при наличии реального scoped grant также запрещена.
    for scope in (other_root,root.replace(str(loc),str(sibling))):
        for suffix in ("/customer","/stock","/estimates","/demo-close/technical-dossier.zip"):
            assert http.get(scope+f"/orders/{order}"+suffix).status_code==403

    owner=token(http,d,d["other"])
    assigned=http.post(staff,headers=owner,json=payload)
    assert assigned.status_code==201,assigned.text
    grant_id=assigned.json()["grantId"]
    token(http,foreign,actor)
    for path in routes:
        response=read(path)
        assert response.status_code==200,(path,response.status_code)
    assert http.get(order_root+"/demo-close/technical-dossier.zip").content==archive
    # Отзыв именно филиального назначения; та же сессия остаётся живой в B.
    owner=token(http,d,d["other"])
    revoked=http.post(staff+"/"+grant_id+"/revoke",headers=owner)
    assert revoked.status_code==200 and revoked.json()["status"]=="REVOKED"
    audit=http.get(staff+"/audit")
    assert audit.status_code==200
    events=[x for x in audit.json()["items"] if x["grantId"]==grant_id]
    assert {x["action"] for x in events}=={"GRANTED","REVOKED"}
    assert {x["actorId"] for x in events}=={str(d["other"])}
    token(http,foreign,actor)
    assert_denied()
    assert http.get(own).content==own_archive.content
    token(http,d,d["client_a"])
    assert http.get(order_root+"/demo-close/technical-dossier.zip").content==archive
    token(http,d,d["intake"])
    assert http.get(order_root+"/demo-close/technical-dossier.zip").content==archive
    assert http.get(report,params=query).content==original[report]
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_order_demo_cash_event
            WHERE work_order_id=%s""",(order,)).fetchone()==(1,)
        assert db.execute("""SELECT count(*) FROM auto_order_demo_document_event
            WHERE work_order_id=%s""",(order,)).fetchone()==(3,)
        assert db.execute("""SELECT count(*) FROM auto_org_staff_action
            WHERE grant_id=%s""",(grant_id,)).fetchone()==(2,)
