"""Synthetic full vertical: approved branch tariffs, complex stock, assignment,
additional consent, authorized labor, material handoff and independent QC.
"""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg

from test_pg_baseline import DSN
from test_pg_auto_work_complexes_http import setup,paths,complex_body
from test_pg_auto_work_order_estimate_http import token,path as estimate_path
from test_pg_auto_work_order_execution_http import url as execution_path,post
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_lot_allocation import act as allocate_lot
from roboport_api.auto_workshop_qc import submit_work_order_inspection


def test_real_full_synthetic_complex_to_extra_consent_to_independent_qc(monkeypatch):
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
        part_lot=add_lot(db,org,loc,part,add_bin(db,org,loc))
        consumable_lot=add_lot(db,org,loc,consumable,add_bin(db,org,loc))
        allocate_lot(db,d,org,loc,part_lot,"ALLOCATE",2)
        allocate_lot(db,d,org,loc,consumable_lot,"ALLOCATE",1)
        submit_work_order_inspection(db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=1234,fuel_percent=60,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="CLEAR")

    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    catalog,selected=paths(org,loc,order)
    manager=token(http,d,d["intake"])

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
    assert all(p["policyId"]==policy_id for p in snapshot["tariffs"])

    customer=token(http,d,d["client_a"])
    assert http.get(estimate).json()["items"][0]["materialLines"]==snapshot["materialLines"]
    assert http.post(estimate+"/decision",headers=customer,json={
        "requestKey":str(uuid4()),"revisionId":snapshot["id"],
        "decision":"APPROVED"}).status_code==201

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
        "decision":"APPROVED"}).status_code==201
    mechanic=token(http,d,d["manager"])
    assert post(http,execution+"/labor",mechanic,
        serviceId=str(extra_service),actualMinutes=20).status_code==201
    other=token(http,d,d["other"])
    assert post(http,execution+"/labor",other,
        serviceId=str(extra_service),actualMinutes=20).status_code==403

    # Actual allocated lot issues and handoffs consume the package reservations.
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
    finished=post(http,execution+"/finish",mechanic)
    assert finished.status_code==201,finished.text
    assert finished.json()["status"]=="READY_FOR_QC"
    assert finished.json()["approvedExtraVersion"]==1
    self_qc=post(http,root+f"/orders/{order}/qc",mechanic,
                 result="PASS",findingCode="NONE")
    assert self_qc.status_code==403
    independent=token(http,d,d["other"])
    qc=post(http,root+f"/orders/{order}/qc",independent,
            result="PASS",findingCode="NONE")
    assert qc.status_code==201,qc.text
    assert qc.json()["releaseAllowed"] is False
    with psycopg.connect(DSN) as db:
        assert balance(db,part)==(0,0)
        assert balance(db,consumable)==(2,0)
        assert db.execute("""SELECT count(*) FROM auto_work_order_labor_entry l
          JOIN auto_work_order_execution e ON e.id=l.execution_id
          WHERE e.work_order_id=%s""",(order,)).fetchone()==(3,)
        assert db.execute("""SELECT count(*) FROM auto_work_complex_material_reserved r
          JOIN auto_work_order_complex_selection s ON s.id=r.selection_id
          WHERE s.work_order_id=%s""",(order,)).fetchone()==(2,)
        assert db.execute("""SELECT count(*) FROM auto_stock_mechanic_handoff h
          JOIN auto_stock_lot_work_order_issue i ON i.id=h.issue_id
          WHERE i.work_order_id=%s""",(order,)).fetchone()==(2,)
