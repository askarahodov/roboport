"""Real PG17 HTTPS versioned vehicle intake custody and scoped image evidence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import base64
import binascii
import hashlib
import struct
import zlib
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_appointment_service import create_appointment
from roboport_api.auto_intake_act import (
    write_intake_act,IntakeActConflict,IntakeActInvalid,
    sanitize_png,PNG_SIGNATURE,
)
from test_pg_auto_workshop_qc_http import scenario,client_setup,token_as,inspection,target
from test_pg_baseline import DSN


def _chunk(kind, data):
    return (struct.pack(">I",len(data))+kind+data+
            struct.pack(">I",binascii.crc32(kind+data)&0xffffffff))


def synthetic_png(with_srgb=True,with_exif=False,width=2,height=2):
    header=struct.pack(">IIBBBBB",width,height,8,2,0,0,0)
    rows=b"".join(b"\x00"+bytes([41,82,122]) * width for _ in range(height))
    parts=[_chunk(b"IHDR",header)]
    if with_srgb:
        parts.append(_chunk(b"sRGB",b"\x00"))
    if with_exif:
        parts.append(_chunk(b"eXIf",b"secret EXIF GPS"))
    parts.extend([_chunk(b"IDAT",zlib.compress(rows)),
                  _chunk(b"IEND",b"")])
    return PNG_SIGNATURE+b"".join(parts)


def custody(photo,*,damaged=True,condition="PRESENT"):
    return {
      "vehicleKind":"PASSENGER",
      "damages":[{"areaCode":"FRONT","damageCode":"SCRATCH",
                  "severity":"MINOR"}] if damaged else [],
      "inventory":[{"itemCode":item,"condition":condition if item=="KEYS" else "NOT_CHECKED"}
                   for item in ("KEYS","SPARE_WHEEL","TOOLS","DOCS","CHARGER")],
      "photos":[{"areaCode":"FRONT","imageBase64":base64.b64encode(photo).decode()}]
               if photo else [],
    }


def url(org,loc,order):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/intake-acts"


def publish(http,head,path,act,key=None,version=0):
    return http.post(path,headers=head,json={
        "requestKey":str(key or uuid4()),"expectedVersion":version,"act":act,
    })


def test_private_intake_photos_versions_client_scope_replay_and_qc_lock(monkeypatch):
    d,org,loc,_,order,_,_,_,foreign_loc,intake_grant,_=scenario()
    http=client_setup(monkeypatch)
    path=url(org,loc,order)
    raw=synthetic_png(with_srgb=True)
    act=custody(raw)
    assert http.get(path).status_code==403
    assert publish(http,{},path,act).status_code==403
    staff=token_as(http,d,d["intake"])
    first=http.get(path)
    assert first.status_code==200 and first.json()["latestVersion"]==0
    assert first.headers["cache-control"]=="no-store"
    assert first.json()["legalSignatureStatus"]=="NOT_IMPLEMENTED"
    assert publish(http,staff,path,act).status_code==409
    assert publish(http,staff,path,{**act,"freeText":"secret"}).status_code==422
    assert publish(http,staff,path,{**act,"inventory":act["inventory"][:4]}).status_code==422
    assert publish(http,staff,path,{**act,"photos":[]}).status_code==422
    assert publish(http,staff,path,custody(synthetic_png(with_exif=True))).status_code==422
    assert publish(http,staff,path,custody(raw+b"trailing bytes")).status_code==422
    assert publish(http,staff,path,custody(synthetic_png(width=900,height=900))).status_code==422
    bad=bytearray(raw);bad[-9]^=0x01
    assert publish(http,staff,path,custody(bytes(bad))).status_code==422
    assert publish(http,staff,path,{
        **act,"damages":act["damages"]*2,
    }).status_code==422
    assert http.get(path.replace(str(loc),str(foreign_loc))).status_code==403

    _,inspect,QC=target(org,loc,order)
    assert http.post(inspect,headers=staff,json=inspection()).status_code==201
    key=uuid4()
    first=publish(http,staff,path,act,key)
    assert first.status_code==201,first.text
    result=first.json()
    assert result["latestVersion"]==1
    assert result["releaseAllowed"] is False
    assert result["items"][0]["version"]==1
    photo=result["items"][0]["photos"][0]
    clean,w,h=sanitize_png(raw)
    assert (w,h)==(2,2)
    assert photo["sha256"]==hashlib.sha256(clean).hexdigest()
    assert len(clean)<len(raw)
    assert b"sRGB" not in clean
    assert "secret EXIF" not in first.text
    photo_url=photo["path"]
    read=http.get(photo_url)
    assert read.status_code==200,read.text[:100] if read.status_code!=200 else ""
    assert read.content==clean
    assert read.headers["content-type"]=="image/png"
    assert read.headers["cache-control"]=="no-store, private"
    assert read.headers["x-content-type-options"]=="nosniff"
    assert http.get(photo_url.replace(str(order),str(uuid4()))).status_code==403
    assert http.get(photo_url.rsplit("/",1)[0]+"/4").status_code==422
    replay=publish(http,staff,path,act,key)
    assert replay.status_code==200 and replay.json()["replayed"] is True
    assert replay.json()["items"]==result["items"]
    assert publish(http,staff,path,custody(raw,condition="MISSING"),key).status_code==409
    assert publish(http,staff,path,custody(raw,condition="DAMAGED"),version=0).status_code==409

    # The client receives exactly their appointment's original verified proof.
    token_as(http,d,d["client_a"])
    client_read=http.get(path)
    assert client_read.status_code==200,client_read.text
    assert client_read.json()["items"][0]["damages"]==act["damages"]
    assert http.get(photo_url).content==clean
    assert publish(http,{},path,act).status_code==403
    token_as(http,d,d["client_b"])
    assert http.get(path).status_code==403
    assert http.get(photo_url).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_vehicle_customer_link
            SET verified_at=now(),verified_by=%s WHERE id=%s""",
            (d["manager"],d["other_link"]))
    # Even a verified new owner of the SAME vehicle cannot read old photos.
    assert http.get(path).status_code==403
    assert http.get(photo_url).status_code==403

    staff=token_as(http,d,d["intake"])
    second=publish(http,staff,path,custody(raw,condition="MISSING"),version=1)
    assert second.status_code==201,second.text
    assert [item["version"] for item in second.json()["items"]]==[1,2]
    assert second.json()["items"][0]["sha256"]!=second.json()["items"][1]["sha256"]
    assert second.json()["items"][0]["inventory"][0]["condition"]=="PRESENT"
    assert second.json()["items"][1]["inventory"][0]["condition"]=="MISSING"
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_work_order_intake_act WHERE work_order_id=%s",
                          (order,)).fetchone()==(2,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_order_intake_act SET vehicle_kind='SUV'
                    WHERE work_order_id=%s""",(order,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_intake_act WHERE work_order_id=%s",
                           (order,))
    # After QC begins, new versions freeze, but existing exact retries remain safe.
    checker=token_as(http,d,d["manager"])
    assert http.post(QC,headers=checker,json={
        "requestKey":str(uuid4()),"result":"FAIL","findingCode":"REWORK",
    }).status_code==201
    staff=token_as(http,d,d["intake"])
    assert publish(http,staff,path,custody(raw,condition="DAMAGED"),version=2).status_code==409
    assert publish(http,staff,path,act,key).status_code==200
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (intake_grant,))
    # The test fixture grants this employee both INTAKE and MECHANIC:
    # loss of INTAKE removes write, but live MECHANIC still grants read.
    assert publish(http,staff,path,act,key).status_code==403
    assert http.get(path).status_code==200
    assert http.get(photo_url).status_code==200
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
           WHERE organization_id=%s AND user_id=%s
             AND role_code='MECHANIC' AND revoked_at IS NULL""",
           (org,d["intake"]))
    assert http.get(path).status_code==403
    assert http.get(photo_url).status_code==403
    token_as(http,d,d["client_a"])
    assert http.get(path).status_code==200
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.get(path).status_code==403
    assert http.get(photo_url).status_code==403


def test_intake_act_concurrent_revisions_and_same_owner_previous_visit(monkeypatch):
    d,org,loc,bay,order,_,start,_,_,_,_=scenario()
    http=client_setup(monkeypatch)
    staff=token_as(http,d,d["intake"])
    _,inspect,_=target(org,loc,order)
    assert http.post(inspect,headers=staff,json=inspection()).status_code==201
    path=url(org,loc,order)
    raw=synthetic_png()
    assert publish(http,staff,path,custody(raw)).status_code==201
    # Any parallel version N+1 attempt serializes on WorkOrder; one wins.
    barrier=Barrier(2)
    def submit(condition):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                return write_intake_act(db,d["tokens"][d["intake"]],org,loc,order,
                    request_key=uuid4(),expected_version=1,
                    act=custody(raw,condition=condition))["latestVersion"]
        except IntakeActConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(submit,("MISSING","DAMAGED")))
    assert results.count(2)==1 and results.count("CONFLICT")==1
    with psycopg.connect(DSN) as db:
        prior=db.execute(
            "SELECT count(*) FROM auto_work_order_intake_act WHERE work_order_id=%s",
            (order,)).fetchone()[0]
        assert prior==2
        _,_,other_order=create_appointment(db,d["tokens"][d["intake"]],
            org,loc,bay,d["link"],start+timedelta(hours=3),
            start+timedelta(hours=4))
    _,inspect_second,_=target(org,loc,other_order)
    assert http.post(inspect_second,headers=staff,json=inspection()).status_code==201
    next_path=url(org,loc,other_order)
    history=http.get(next_path).json()
    assert history["previousVisit"]["orderId"]==str(order)
    assert history["previousVisit"]["version"]==2
    assert len(history["previousVisit"]["damages"])==1
    assert publish(http,staff,next_path,custody(raw)).status_code==201

    # Previous owner must not leak through another verified link on same vehicle.
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_vehicle_customer_link
          SET verified_at=now(),verified_by=%s WHERE id=%s""",
          (d["manager"],d["other_link"]))
        _,_,new_owner_order=create_appointment(db,d["tokens"][d["intake"]],
            org,loc,bay,d["other_link"],start+timedelta(hours=5),
            start+timedelta(hours=6))
    _,new_inspection,_=target(org,loc,new_owner_order)
    assert http.post(new_inspection,headers=staff,json=inspection()).status_code==201
    assert http.get(url(org,loc,new_owner_order)).json()["previousVisit"] is None


def test_png_sanitizer_rejects_exif_text_crc_bombs_and_unknown_chunks():
    valid=synthetic_png()
    clean,w,h=sanitize_png(valid)
    assert clean!=valid and (w,h)==(2,2)
    assert b"sRGB" not in clean
    for bad in (
        synthetic_png(with_exif=True),
        PNG_SIGNATURE+_chunk(b"IHDR",struct.pack(">IIBBBBB",512,512,8,2,0,0,0))+
          _chunk(b"IDAT",zlib.compress(b"\x00"*10000000))+
          _chunk(b"IEND",b""),
        b"<svg onload=alert(1)>",
        valid[:-5],
        valid+b"trailing",
        synthetic_png(width=0),
    ):
        with pytest.raises(IntakeActInvalid):
            sanitize_png(bad)



def test_previous_visit_is_branch_scoped_and_branch_b_direct_reads_need_grant(monkeypatch):
    """Runtime SQL must scope historic rows to the current branch.

    A vehicle has ONE immutable location binding. Build legitimate separate
    branch-B custody for a different synthetic vehicle/customer instead of
    trying to UPDATE the append-only binding to fabricate history.
    """
    from test_pg_auto_vehicle_claim_http import _scenario as vehicle_scenario
    from test_pg_auto_stock_service import grant
    from roboport_api.auto_intake_act import _read

    d,org,loc,bay,first_order,_,start,*_=scenario()
    other=vehicle_scenario()
    http=client_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        other_loc,other_bay=uuid4(),uuid4()
        db.execute("""INSERT INTO auto_service_location(id,code,organization_id)
                      VALUES(%s,%s,%s)""",
                   (other_loc,"LOC_"+other_loc.hex[:10].upper(),org))
        db.execute("""INSERT INTO auto_work_bay(id,organization_id,location_id,code)
                      VALUES(%s,%s,%s,'BAY_1')""",(other_bay,org,other_loc))
        db.execute("""INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id)
                      VALUES(%s,%s)""",(other["vehicle"],other_loc))
        db.execute("""UPDATE auto_vehicle_customer_link
                      SET verified_at=now(),verified_by=%s WHERE id=%s""",
                   (other["manager"],other["link"]))
        grant(db,other["intake"],org,other_loc,"INTAKE")
        _,_,branch_b_order=create_appointment(
            db,other["tokens"][other["intake"]],org,other_loc,
            other_bay,other["link"],start+timedelta(hours=2),
            start+timedelta(hours=3))
    bstaff=token_as(http,other,other["intake"])
    _,inspector_path,_=target(org,other_loc,branch_b_order)
    assert http.post(inspector_path,headers=bstaff,json=inspection()).status_code==201
    image=synthetic_png()
    saved=publish(http,bstaff,url(org,other_loc,branch_b_order),custody(image))
    assert saved.status_code==201,saved.text
    photo=saved.json()["items"][0]["photos"][0]["path"]

    staff_a=token_as(http,d,d["intake"])
    assert http.get(url(org,other_loc,branch_b_order)).status_code==403
    assert http.get(photo).status_code==403
    with psycopg.connect(DSN) as db:
        _,_,current_a_order=create_appointment(
            db,d["tokens"][d["intake"]],org,loc,bay,d["link"],
            start+timedelta(hours=4),start+timedelta(hours=5))

    # Spy on the REAL PostgreSQL query (do not fabricate the DB history).
    # The explicit predicate is the security regression condition: without
    # it, a migrated same-link cross-branch historic row would be exposed.
    class AuditedConnection:
        def __init__(self,real):
            self.real=real
            self.inspected=False
        def execute(self,sql,params=None):
            if "FROM auto_work_order current_order" in sql:
                assert "previous_order.location_id=current_order.location_id" in sql
                self.inspected=True
            return self.real.execute(sql,params)
    with psycopg.connect(DSN) as db:
        audited=AuditedConnection(db)
        result=_read(audited,current_a_order,org,loc)
        assert audited.inspected
        assert result["previousVisit"] is None
        assert result["items"]==[]
    current=url(org,loc,current_a_order)
    response=http.get(current)
    assert response.status_code==200,response.text
    assert response.json()["previousVisit"] is None
    token_as(http,d,d["client_a"])
    assert http.get(current).json()["previousVisit"] is None
