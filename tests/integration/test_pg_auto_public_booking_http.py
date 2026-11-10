"""PG17 HTTPS public booking: anonymous no-PII → verified owner → staff material
reserve + genuine appointment + CRM lineage + dev outbox. No real marketing.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from threading import Barrier
from uuid import UUID,uuid4

import psycopg
import pytest

from roboport_api.auto_public_booking import (
    fulfill_request,PublicBookingConflict,
)
from test_pg_auto_booking_materials_atomic import _ready
from test_pg_auto_customer_booking_http import _auth
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_ledger import balance
from test_pg_baseline import DSN


PUB="/api/v1/dev/auto/public-booking"
ANON={"Origin":"https://127.0.0.1","Content-Type":"application/json"}


def setup(monkeypatch):
    d,org,loc,bay,http,part,consumable,foreign_sku,service,warehouse=_ready(monkeypatch)
    with psycopg.connect(DSN) as db:
        grant(db,d["intake"],org,loc,"MANAGER")
    headers=_auth(http,d,d["intake"])
    staff=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/public-booking"
    create=http.post(staff+"/offers",headers=headers,json={
      "requestKey":str(uuid4()),"bayId":str(bay),"serviceId":str(service),
      "policyVersion":"DEV-CONSENT-2026",
      "materials":[
        {"skuId":str(part),"kind":"PART","quantity":2},
        {"skuId":str(consumable),"kind":"CONSUMABLE","quantity":3},
      ],
    })
    assert create.status_code==201,create.text
    offer=create.json()["id"]
    return d,org,loc,bay,http,part,consumable,service,staff,offer


def published_slot(http,org,loc,offer):
    assert http.get(PUB+"/offers",params={
      "organizationId":str(org),"locationId":str(loc),
    }).status_code==200
    day=(datetime.now(timezone.utc)+timedelta(days=22)).date().isoformat()
    slots=http.get(PUB+"/slots",params={"offerId":offer,"localDay":day})
    assert slots.status_code==200,slots.text
    assert slots.json()["guaranteed"] is False
    assert slots.json()["timeZone"]=="Europe/Berlin"
    assert slots.json()["materialsReady"] is True
    assert slots.json()["items"]
    return slots.json()["items"][0]


def challenge(http,offer):
    http.cookies.clear()
    response=http.post(PUB+"/challenges",json={"offerId":offer},headers=ANON)
    assert response.status_code==201,response.text
    body=response.json()
    numbers=body["question"].replace(" = ?","").split(" + ")
    assert body["captchaProvider"]=="DEV_ARITHMETIC_ONLY"
    return body["challengeId"],sum(map(int,numbers))


def submit(http,offer,slot,*,key=None,marketing=True,answer_add=0):
    ident,answer=challenge(http,offer)
    payload={
      "offerId":offer,"requestKey":str(key or uuid4()),
      "challengeId":ident,"answer":answer+answer_add,
      "startsAt":slot["startsAt"],"endsAt":slot["endsAt"],
      "processingConsent":True,"marketingConsent":marketing,
    }
    response=http.post(PUB+"/requests",headers=ANON,json=payload)
    return response,payload


def claim(http,d,request_id,receipt,*,actor=None,key=None,link=None):
    subject=d["client_a"] if actor is None else actor
    h=_auth(http,d,subject)
    key=key or uuid4()
    result=http.post(PUB+f"/requests/{request_id}/claim",headers=h,json={
      "receiptKey":receipt,"claimKey":str(key),
      "customerLinkId":str(link or d["link"]),"processingConsent":True,
    })
    return result,key


def fulfill(http,d,staff,ident,key=None):
    headers=_auth(http,d,d["intake"])
    key=key or uuid4()
    result=http.post(staff+f"/requests/{ident}/fulfill",
                     headers=headers,json={"requestKey":str(key)})
    return result,key


def test_full_anonymous_safe_published_booking_and_crm_stock_outbox(monkeypatch):
    d,org,loc,bay,http,part,consumable,service,staff,offer=setup(monkeypatch)
    slot=published_slot(http,org,loc,offer)
    raw=http.get(PUB+"/offers",params={"organizationId":str(org),"locationId":str(loc)})
    assert raw.headers["cache-control"]=="no-store"
    assert "vin" not in raw.text.lower()
    assert str(d["link"]) not in raw.text
    assert str(part) not in raw.text
    assert http.post(PUB+"/challenges",json={"offerId":offer}).status_code==403
    assert http.post(PUB+"/challenges",json={"offerId":offer},
         headers={"Origin":"https://evil.example","Content-Type":"application/json"}).status_code==403
    wrong,payload=submit(http,offer,slot,answer_add=1)
    assert wrong.status_code==409,wrong.text
    payload["answer"]-=1
    invalid=http.post(PUB+"/requests",headers=ANON,json={
        **payload,"processingConsent":False})
    assert invalid.status_code==422
    created=http.post(PUB+"/requests",headers=ANON,json=payload)
    assert created.status_code==201,created.text
    request_id=created.json()["requestId"]
    receipt=created.json()["receiptKey"]
    assert created.json()["noSlotHold"] is True
    again=http.post(PUB+"/requests",headers=ANON,json=payload)
    assert again.status_code==200 and again.json()["requestId"]==request_id
    assert http.post(PUB+"/requests",headers=ANON,json={
        **payload,"marketingConsent":False}).status_code==409
    assert http.post(PUB+f"/requests/{request_id}/status",
        headers=ANON,json={"receiptKey":str(uuid4())}).status_code==403
    status=http.post(PUB+f"/requests/{request_id}/status",
        headers=ANON,json={"receiptKey":receipt})
    assert status.status_code==200
    assert status.json()["status"]=="AWAITING_VERIFICATION"
    assert status.json()["marketingEnabled"] is True
    # Staff cannot convert an unclaimed guest request, even with warehouse access.
    pending,_=fulfill(http,d,staff,request_id)
    assert pending.status_code==409
    denied,_=claim(http,d,request_id,receipt,actor=d["client_b"])
    assert denied.status_code==403
    orig,key=claim(http,d,request_id,receipt)
    assert orig.status_code==201,orig.text
    assert orig.json()["status"]=="WAITING_FOR_STAFF"
    replay,_=claim(http,d,request_id,receipt,key=key)
    assert replay.status_code==200
    owner=_auth(http,d,d["client_a"])
    denied_claim=http.post(PUB+f"/requests/{request_id}/claim",headers=owner,json={
      "receiptKey":receipt,"claimKey":str(uuid4()),
      "customerLinkId":str(d["link"]),"processingConsent":True,
    })
    assert denied_claim.status_code==409
    # Staff must have INTAKE + WAREHOUSE, both live, for material reservation.
    queue_headers=_auth(http,d,d["intake"])
    queue=http.get(staff+"/queue")
    assert queue.status_code==200
    item=queue.json()["items"][0]
    assert item["status"]=="WAITING_FOR_STAFF"
    assert {(x["kind"],x["quantity"]) for x in item["materials"]}=={
      ("PART",2),("CONSUMABLE",3)}
    assert str(d["ca"]) not in queue.text
    finished,finish_key=fulfill(http,d,staff,request_id)
    assert finished.status_code==201,finished.text
    order=UUID(finished.json()["workOrderId"])
    appointment=UUID(finished.json()["appointmentId"])
    assert finished.json()["releaseAllowed"] is False
    retry,_=fulfill(http,d,staff,request_id,key=finish_key)
    assert retry.status_code==200
    assert retry.json()["workOrderId"]==str(order)
    denied,_=fulfill(http,d,staff,request_id)
    assert denied.status_code==409
    assert http.post(PUB+f"/requests/{request_id}/status",
        headers=ANON,json={"receiptKey":receipt}).json()["status"]=="BOOKED"
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_public_booking_fulfillment
          WHERE request_id=%s""",(UUID(request_id),)).fetchone()==(1,)
        assert db.execute("""SELECT count(*) FROM auto_work_order_service
          WHERE work_order_id=%s AND service_id=%s
            AND selection_origin='BOOKING'""",(order,service)).fetchone()==(1,)
        assert db.execute("""SELECT count(*) FROM auto_appointment_material_selection
          WHERE work_order_id=%s""",(order,)).fetchone()==(2,)
        assert balance(db,part)==(2,2)
        assert balance(db,consumable)==(5,3)
        assert db.execute("""SELECT count(*) FROM notification_outbox
          WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
          (appointment,)).fetchone()==(1,)
        stages=db.execute("""
          SELECT stage_code FROM auto_crm_lead_stage s
          JOIN auto_public_booking_fulfillment f ON f.crm_lead_id=s.lead_id
          WHERE f.request_id=%s ORDER BY s.created_at,s.id
        """,(UUID(request_id),)).fetchall()
        assert [x[0] for x in stages]==["QUALIFIED","BOOKED"]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_public_booking_request SET marketing_consent=false
                  WHERE id=%s""",(UUID(request_id),))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_public_booking_fulfillment WHERE request_id=%s",
                           (UUID(request_id),))
    # Verified claimant can irreversibly withdraw marketing, no delivery enabled.
    client=_auth(http,d,d["client_a"])
    withdraw_key=uuid4()
    path=PUB+f"/requests/{request_id}/withdraw"
    withdrew=http.post(path,headers=client,json={
        "receiptKey":receipt,"withdrawKey":str(withdraw_key)})
    assert withdrew.status_code==201,withdrew.text
    assert withdrew.json()["marketingEnabled"] is False
    assert http.post(path,headers=client,json={
        "receiptKey":receipt,"withdrawKey":str(withdraw_key)}).status_code==200
    assert http.post(path,headers=client,json={
        "receiptKey":receipt,"withdrawKey":str(uuid4())}).status_code==409
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.post(path,headers=client,json={
        "receiptKey":receipt,"withdrawKey":str(withdraw_key)}).status_code==403
    assert http.get(staff+"/queue").status_code==403  # live browser cookie is client


def test_public_claim_revoked_staff_no_material_booking_and_race(monkeypatch):
    d,org,loc,bay,http,part,consumable,service,staff,offer=setup(monkeypatch)
    slot=published_slot(http,org,loc,offer)
    a,_=submit(http,offer,slot,marketing=False)
    b,_=submit(http,offer,slot,marketing=False)
    assert a.status_code==201 and b.status_code==201
    for request in (a.json(),b.json()):
        out,_=claim(http,d,request["requestId"],request["receiptKey"])
        assert out.status_code==201,out.text
    # A missing warehouse grant blocks the full booking, not just materials.
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE user_id=%s AND organization_id=%s AND location_id=%s
            AND role_code='WAREHOUSE'""",(d["intake"],org,loc))
    absent,_=fulfill(http,d,staff,a.json()["requestId"])
    assert absent.status_code==403
    with psycopg.connect(DSN) as db:
        grant(db,d["intake"],org,loc,"WAREHOUSE")
    barrier=Barrier(2)
    ident=UUID(a.json()["requestId"])
    def attempt(_):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                result=fulfill_request(db,d["tokens"][d["intake"]],
                  org,loc,ident,uuid4())
            return "CREATED" if not result["replayed"] else "REPLAYED"
        except PublicBookingConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==["CONFLICT","CREATED"]
    other,_=fulfill(http,d,staff,b.json()["requestId"])
    assert other.status_code==409  # same bay/time: DB exclusion
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_appointment a
          WHERE a.organization_id=%s AND a.location_id=%s""",(org,loc)).fetchone()==(1,)
        assert balance(db,part)==(2,2)
        assert balance(db,consumable)==(5,3)
        assert db.execute("""SELECT count(*) FROM auto_public_booking_fulfillment
          WHERE request_id=ANY(%s::uuid[])""",
          ([UUID(a.json()["requestId"]),UUID(b.json()["requestId"])],)).fetchone()==(1,)


def test_public_scope_rejects_foreign_service_and_unverified_link(monkeypatch):
    d,org,loc,bay,http,part,consumable,service,staff,offer=setup(monkeypatch)
    slot=published_slot(http,org,loc,offer)
    foreign=http.get(PUB+"/offers",params={
      "organizationId":str(uuid4()),"locationId":str(uuid4())})
    assert foreign.status_code==200 and foreign.json()["items"]==[]
    resp,_=submit(http,offer,slot)
    assert resp.status_code==201
    wrong_user,_=claim(http,d,resp.json()["requestId"],
                       resp.json()["receiptKey"],actor=d["client_b"])
    assert wrong_user.status_code==403
    staff_headers=_auth(http,d,d["intake"])
    assert http.post(staff+"/offers",headers=staff_headers,json={
       "requestKey":str(uuid4()),"bayId":str(uuid4()),"serviceId":str(service),
       "policyVersion":"DEV-CONSENT-2026","materials":[],
    }).status_code==409
    assert http.get(staff+"/queue").status_code==200
    # A direct URL cannot expose queue to an anonymous request.
    http.cookies.clear()
    assert http.get(staff+"/queue").status_code==403



def test_manager_withdraws_published_offer_and_prevents_stale_fulfillment(monkeypatch):
    d,org,loc,bay,http,part,consumable,service,staff,offer=setup(monkeypatch)
    slot=published_slot(http,org,loc,offer)
    response,_=submit(http,offer,slot)
    assert response.status_code==201,response.text
    req=response.json()
    linked,_=claim(http,d,req["requestId"],req["receiptKey"])
    assert linked.status_code==201,linked.text
    manager=_auth(http,d,d["intake"])
    url=staff+"/offers/"+offer+"/revoke"
    key=str(uuid4())
    revoked=http.post(url,headers=manager,json={"requestKey":key})
    assert revoked.status_code==201,revoked.text
    assert revoked.json()=={"syntheticOnly":True,"revoked":True,"replayed":False}
    assert http.post(url,headers=manager,json={"requestKey":key}).status_code==200
    assert http.post(url,headers=manager,json={"requestKey":str(uuid4())}).status_code==409
    assert http.get(PUB+"/offers",params={
      "organizationId":str(org),"locationId":str(loc)}).json()["items"]==[]
    assert http.get(PUB+"/slots",params={
      "offerId":offer,"localDay":(datetime.now(timezone.utc)+timedelta(days=22)).date().isoformat()
    }).status_code==403
    assert http.post(PUB+"/challenges",headers=ANON,json={"offerId":offer}).status_code==403
    current=http.post(PUB+f'/requests/{req["requestId"]}/status',
      headers=ANON,json={"receiptKey":req["receiptKey"]})
    assert current.status_code==200 and current.json()["status"]=="OFFER_WITHDRAWN"
    queue=http.get(staff+"/queue")
    assert queue.status_code==200 and queue.json()["items"][0]["status"]=="OFFER_WITHDRAWN"
    denied,_=fulfill(http,d,staff,req["requestId"])
    assert denied.status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_appointment
            WHERE organization_id=%s""",(org,)).fetchone()==(0,)
        assert balance(db,part)==(2,0)
        assert balance(db,consumable)==(5,0)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_public_booking_offer_revoke
                    (offer_id,actor_id,request_key)
                    VALUES(%s,%s,gen_random_uuid())""",(UUID(offer),d["client_b"]))


def test_quota_saturated_request_exact_replay_and_paged_queue(monkeypatch):
    """A lost 25th POST is replayable; queue stays functional past burst saturation."""
    d,org,loc,bay,http,part,consumable,service,staff,offer=setup(monkeypatch)
    slot=published_slot(http,org,loc,offer)
    last_payload=None
    last_id=None
    for _ in range(25):
        created,payload=submit(http,offer,slot)
        assert created.status_code==201,created.text
        last_payload=payload
        last_id=created.json()["requestId"]
    # The org is now exactly at 25 submissions / 10 min: new requests
    # are throttled, but an identical lost response must be recovered.
    repeated=http.post(PUB+"/requests",headers=ANON,json=last_payload)
    assert repeated.status_code==200,repeated.text
    assert repeated.json()["requestId"]==last_id
    assert repeated.json()["replayed"] is True
    new_challenge,new_answer=challenge(http,offer)
    different={**last_payload,"requestKey":str(uuid4()),
               "challengeId":new_challenge,"answer":new_answer}
    rejected=http.post(PUB+"/requests",headers=ANON,json=different)
    assert rejected.status_code==429,rejected.text
    # Retrying a known key with a changed payload is still KEY_REUSED, not 429.
    assert http.post(PUB+"/requests",headers=ANON,json={
        **last_payload,"marketingConsent":not last_payload["marketingConsent"]
    }).status_code==409
    _auth(http,d,d["intake"])
    queue=http.get(staff+"/queue?page=0")
    assert queue.status_code==200,queue.text
    assert queue.json()["page"]==0
    assert queue.json()["hasMore"] is False
    assert len(queue.json()["items"])==25
    assert http.get(staff+"/queue?page=1").json()["items"]==[]
    assert http.get(staff+"/queue?page=-1").status_code==422
    assert http.get(staff+"/queue?page=101").status_code==422
    assert http.get(staff+"/queue?page=no").status_code==422


def test_unusual_branch_timezone_slot_candidates_are_accepted(monkeypatch):
    """A local half-hour can be a UTC :15/:45 in Asia/Kathmandu."""
    d,org,loc,bay,http,part,consumable,service,staff,offer=setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_service_location SET timezone_name='Asia/Kathmandu'
                      WHERE id=%s AND organization_id=%s""",(loc,org))
    day=(datetime.now(timezone.utc)+timedelta(days=22)).date().isoformat()
    slots=http.get(PUB+"/slots",params={"offerId":offer,"localDay":day})
    assert slots.status_code==200,slots.text
    assert slots.json()["timeZone"]=="Asia/Kathmandu"
    values=slots.json()["items"]
    assert values
    assert all(datetime.fromisoformat(x["startsAt"]).astimezone(timezone.utc).minute
               in (15,45) for x in values)
    created,payload=submit(http,offer,values[0])
    assert created.status_code==201,created.text
    # Without local-zone matching the generated candidate would return 422.
    assert created.json()["noSlotHold"] is True
