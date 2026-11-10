"""PG17/HTTPS restrictive role policies: scoped documents, prices, discounts, history.

This is a real synthetic tenant/session/CSRF test, not mocked role permissions.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_document_access import (
    publish_policy,require_document_access,
    DocumentPolicyConflict,DocumentPolicyDenied,
)
from test_pg_auto_work_order_estimate_http import (
    scenario,api,token,path as estimate_path,publish_payload,
)
from test_pg_auto_supplier_purchase_http import (
    fixture as supplier_fixture,post as supplier_post,
)
from test_pg_baseline import DSN


def admin(db,org,actor):
    db.execute("""
        INSERT INTO auto_organization_role_grant
        (id,organization_id,user_id,role_code)
        VALUES(%s,%s,%s,'ADMIN')
    """,(uuid4(),org,actor))


def url(org):
    return f"/api/v1/dev/auto/organizations/{org}/access-policies"


def set_rule(http,uri,headers,*,role="MANAGER",loc=None,user=None,
             document="ESTIMATE",cap="READ",allowed=False,
             not_before=None,key=None,**extra):
    return http.post(uri,headers=headers,json={
      "requestKey":str(key or uuid4()),"role":role,
      "locationId":str(loc) if loc else None,
      "userId":str(user) if user else None,
      "documentKind":document,"capability":cap,
      "allowed":allowed,"notBefore":not_before,**extra,
    })


def test_estimate_role_template_personal_override_price_discount_and_history(monkeypatch):
    d,org,loc,order,service,foreign,_=scenario()
    http=api(monkeypatch)
    endpoint=url(org)
    estimate=estimate_path(org,loc,order)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["manager"])
    staff=token(http,d,d["intake"])
    assert http.get(endpoint).status_code==403
    assert set_rule(http,endpoint,staff,user=d["intake"],loc=loc).status_code==403
    owner=token(http,d,d["manager"])
    assert http.get(endpoint).status_code==200
    assert http.get(endpoint).json()=={"syntheticOnly":True,"items":[]}
    assert set_rule(http,endpoint,owner,role="ADMIN").status_code==422
    assert set_rule(http,endpoint,owner,cap="ADMIN").status_code==422
    assert set_rule(http,endpoint,owner,cap="READ",allowed=False,
                    not_before="2030-01-01T00:00:00Z").status_code==422
    assert set_rule(http,endpoint,owner,cap="READ",allowed=True,
                    not_before="not-a-timestamp").status_code==422
    assert set_rule(http,endpoint,owner,user=d["client_b"],loc=loc).status_code==409
    assert set_rule(http,endpoint,owner,loc=uuid4()).status_code==409
    request_id=uuid4()
    deny=set_rule(http,endpoint,owner,role="MANAGER",loc=loc,
                  user=d["intake"],cap="READ",allowed=False,key=request_id)
    assert deny.status_code==201,deny.text
    assert deny.headers["cache-control"]=="no-store"
    assert deny.json()["items"][0]["actorId"]==str(d["manager"])
    assert set_rule(http,endpoint,owner,loc=loc,user=d["intake"],
                    cap="READ",allowed=False,key=request_id).status_code==200
    assert set_rule(http,endpoint,owner,loc=loc,user=d["intake"],
                    cap="READ",allowed=True,key=request_id).status_code==409
    staff=token(http,d,d["intake"])
    assert http.get(estimate).status_code==403
    customer=token(http,d,d["client_a"])
    assert http.get(estimate).status_code==200

    owner=token(http,d,d["manager"])
    assert set_rule(http,endpoint,owner,loc=loc,user=d["intake"],
                    cap="READ",allowed=True).status_code==201
    staff=token(http,d,d["intake"])
    assert http.get(estimate).status_code==200
    # Template DENY wins over personal ALLOW: cannot elevate by exception.
    owner=token(http,d,d["manager"])
    assert set_rule(http,endpoint,owner,role="MANAGER",cap="WRITE",
                    allowed=False).status_code==201
    assert set_rule(http,endpoint,owner,role="MANAGER",loc=loc,user=d["intake"],
                    cap="CREATE",allowed=True).status_code==201
    staff=token(http,d,d["intake"])
    assert http.post(estimate,headers=staff,
                     json=publish_payload(service)).status_code==403
    owner=token(http,d,d["manager"])
    assert set_rule(http,endpoint,owner,role="MANAGER",cap="WRITE",
                    allowed=True).status_code==201
    # Separately restrict discounts without denying ordinary estimates.
    assert set_rule(http,endpoint,owner,loc=loc,user=d["intake"],
                    cap="DISCOUNT",allowed=False).status_code==201
    staff=token(http,d,d["intake"])
    discounted={"requestKey":str(uuid4()),"expectedVersion":0,
       "workRates":[{"serviceId":str(service),"qualificationCode":"GENERAL",
         "rateMinorPerHour":2500,"quantity":1,"coefficientBps":10000,
         "discountBps":500}]}
    assert http.post(estimate,headers=staff,json=discounted).status_code==403
    created=http.post(estimate,headers=staff,json=publish_payload(service))
    assert created.status_code==201,created.text
    assert created.json()["items"][0]["totalMinor"]==9500
    owner=token(http,d,d["manager"])
    assert set_rule(http,endpoint,owner,loc=loc,user=d["intake"],
                    cap="PRICE_SALES",allowed=False).status_code==201
    staff=token(http,d,d["intake"])
    assert http.get(estimate).status_code==403
    customer=token(http,d,d["client_a"])
    assert http.get(estimate).status_code==200
    owner=token(http,d,d["manager"])
    assert set_rule(http,endpoint,owner,loc=loc,user=d["intake"],
                    cap="PRICE_SALES",allowed=True).status_code==201
    # Historical cutoff uses server-side WorkOrder creation, not browser dates.
    assert set_rule(http,endpoint,owner,loc=loc,user=d["intake"],
                    cap="READ",allowed=True,
                    not_before="2099-01-01T00:00:00Z").status_code==201
    staff=token(http,d,d["intake"])
    assert http.get(estimate).status_code==403
    owner=token(http,d,d["manager"])
    assert set_rule(http,endpoint,owner,loc=loc,user=d["intake"],
                    cap="READ",allowed=True).status_code==201
    staff=token(http,d,d["intake"])
    assert http.get(estimate).status_code==200
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_document_access_policy
                            WHERE organization_id=%s""",(org,)).fetchone()[0]>=9
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""DELETE FROM auto_document_access_policy
                    WHERE organization_id=%s""",(org,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_document_access_policy SET allowed=true
                    WHERE organization_id=%s""",(org,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_document_access_policy
                    (id,request_key,organization_id,location_id,role_code,user_id,
                     document_kind,capability,allowed,actor_id)
                    VALUES(%s,%s,%s,%s,'MANAGER',%s,'ESTIMATE','READ',true,%s)
                """,(uuid4(),uuid4(),org,loc,d["intake"],d["client_b"]))
    owner=token(http,d,d["manager"])
    assert http.get(endpoint+"?limit=1").json()["items"]!=[]
    assert http.get(endpoint+"?limit=0").status_code==422
    assert http.get(endpoint+"?limit=101").status_code==422
    assert http.get(url(uuid4())).status_code==403


def test_supplier_purchase_price_hidden_role_denial_and_page_history(monkeypatch):
    d,org,loc,sku,lot,order,offers,grant_id,manager,base,body=supplier_fixture(monkeypatch)
    http=api(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["manager"])
    warehouse=token(http,d,d["intake"])
    created=supplier_post(http,d,d["intake"],base,body)
    assert created.status_code==201,created.text
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert http.get(base,params={"locationId":str(loc)}).json()["items"][0]["purchaseUnitMinor"]==1200
    owner=token(http,d,d["manager"])
    endpoint=url(org)
    hidden=set_rule(http,endpoint,owner,role="WAREHOUSE",loc=loc,
                    user=d["intake"],document="SUPPLIER_ORDER",
                    cap="PRICE_PURCHASE",allowed=False)
    assert hidden.status_code==201,hidden.text
    warehouse=token(http,d,d["intake"])
    response=http.get(base,params={"locationId":str(loc)})
    assert response.status_code==200,response.text
    row=response.json()["items"][0]
    assert row["purchaseUnitMinor"] is None
    assert row["purchasePriceHidden"] is True
    assert supplier_post(http,d,d["intake"],base,body).status_code==403
    owner=token(http,d,d["manager"])
    assert http.get(base,params={"locationId":str(loc)}).json()["items"][0]["purchaseUnitMinor"]==1200
    assert set_rule(http,endpoint,owner,role="WAREHOUSE",loc=loc,
                    user=d["intake"],document="SUPPLIER_ORDER",
                    cap="READ",allowed=True,
                    not_before="2099-01-01T00:00:00Z").status_code==201
    warehouse=token(http,d,d["intake"])
    assert http.get(base,params={"locationId":str(loc)}).json()["items"]==[]
    owner=token(http,d,d["manager"])
    assert set_rule(http,endpoint,owner,role="WAREHOUSE",loc=loc,
                    user=d["intake"],document="SUPPLIER_ORDER",
                    cap="READ",allowed=True).status_code==201
    assert set_rule(http,endpoint,owner,role="WAREHOUSE",
                    document="SUPPLIER_ORDER",cap="READ",
                    allowed=False).status_code==201
    assert set_rule(http,endpoint,owner,role="WAREHOUSE",loc=loc,
                    user=d["intake"],document="SUPPLIER_ORDER",
                    cap="READ",allowed=True).status_code==201
    warehouse=token(http,d,d["intake"])
    assert http.get(base,params={"locationId":str(loc)}).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (grant_id,))
    assert http.get(base,params={"locationId":str(loc)}).status_code==403


def test_granular_admin_policy_write_serializes_against_doc_read(monkeypatch):
    d,org,loc,order,service,foreign,_=scenario()
    http=api(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["manager"])
    with psycopg.connect(DSN) as reading:
        with reading.transaction():
            require_document_access(reading,d["tokens"][d["intake"]],org,loc,
                                    ("MANAGER",),"ESTIMATE","READ",order=order)
            with psycopg.connect(DSN) as changing:
                changing.execute("SET lock_timeout='200ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    with changing.transaction():
                        publish_policy(changing,d["tokens"][d["manager"]],org,
                            request_key=uuid4(),role="MANAGER",loc=loc,
                            user=d["intake"],document="ESTIMATE",cap="READ",
                            allowed=False,not_before=None)
    barrier=Barrier(2)
    key=uuid4()
    def change(choice):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                result=publish_policy(db,d["tokens"][d["manager"]],org,
                    request_key=key,role="MANAGER",loc=loc,
                    user=d["intake"],document="ESTIMATE",cap="READ",
                    allowed=choice,not_before=None)
                return result["replayed"]
        except DocumentPolicyConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(change,(False,True)))
    assert results.count(False)==1
    assert results.count("CONFLICT")==1
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_document_access_policy
             WHERE request_key=%s""",(key,)).fetchone()==(1,)
