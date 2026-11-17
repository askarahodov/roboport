"""PG17 + HTTPS: эффективные ACL после 520+ immutable версий, постраничный аудит.

Никаких реальных документов/ПДн. Проверяет действующие synthetic сессии,
branch scope, price denial, history cutoff, admin revocation и keyset.
"""
from uuid import UUID,uuid4

import psycopg
import pytest

from roboport_api.auto_document_access import (
    DocumentPolicyDenied,require_document_access,
)
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_document_access_http import admin,set_rule,url
from test_pg_auto_work_order_estimate_http import scenario,api,token,path as estimate_path
from test_pg_baseline import DSN


def seed_many_versions(db,org,loc,actor,subject,count=520,
                       document="ESTIMATE",capability="READ"):
    """Строгий синтетический порядок без зависящих от clock_timestamp() ties."""
    with db.transaction():
        db.execute("SELECT set_config('roboport.policy_actor',%s,true)",
                   (str(actor),))
        db.execute("""
          INSERT INTO auto_document_access_policy(
            id,request_key,organization_id,location_id,role_code,user_id,
            document_kind,capability,allowed,not_before,actor_id,created_at)
          SELECT gen_random_uuid(),gen_random_uuid(),%s,%s,'MANAGER',%s,
                 %s,%s,(n % 2 = 0),NULL,%s,
                 '2020-01-01T00:00:00+00'::timestamptz + n*interval '1 second'
          FROM generate_series(1,%s) AS n
        """,(org,loc,subject,document,capability,actor,count))
        db.execute("SELECT set_config('roboport.policy_actor','',true)")


def test_520_versions_real_order_access_keyset_cursor_and_admin_recovery(monkeypatch):
    d,org,loc,order,service,foreign,_=scenario()
    http=api(monkeypatch)
    policies=url(org)
    estimate=estimate_path(org,loc,order)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["manager"])
        seed_many_versions(db,org,loc,d["manager"],d["intake"])
        assert db.execute("""SELECT count(*) FROM auto_document_access_policy
          WHERE organization_id=%s""",(org,)).fetchone()==(520,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_document_access_policy SET allowed=false
                  WHERE organization_id=%s""",(org,))
        with db.transaction():
            # Роль, объект и SQL-локи действуют и после 520 версий.
            assert require_document_access(db,d["tokens"][d["intake"]],
                org,loc,("MANAGER",),"ESTIMATE","READ",order=order) is None

    manager=token(http,d,d["intake"])
    assert http.get(estimate).status_code==200
    owner=token(http,d,d["manager"])
    preview=http.get(policies+"/effective",params={
        "locationId":str(loc),"userId":str(d["intake"]),
        "documentKind":"ESTIMATE"})
    assert preview.status_code==200,preview.text
    assert preview.json()["capabilities"]["READ"]["allowed"] is True
    assert preview.json()["capabilities"]["READ"]["notBefore"] is None
    assert http.get(policies,params={"limit":"0"}).status_code==422
    assert http.get(policies,params={"limit":"101"}).status_code==422
    assert http.get(policies+"?limit=2&limit=3").status_code==422
    assert http.get(policies+"?before="+str(uuid4())).status_code==422
    assert http.get(policies+"?before=bad").status_code==422
    assert http.get(policies+"?before="+str(uuid4())+"&before="+str(uuid4())).status_code==422

    first=http.get(policies,params={"limit":50})
    assert first.status_code==200,first.text
    assert len(first.json()["items"])==50
    cursor=first.json()["nextBefore"]
    all_ids=[x["eventId"] for x in first.json()["items"]]
    assert cursor==all_ids[-1]
    # Новый DENY, опубликованный после первой страницы, не попадает
    # в продолжение старого keyset и немедленно запрещает документ.
    key=uuid4()
    changed=set_rule(http,policies,owner,loc=loc,user=d["intake"],
                     document="ESTIMATE",cap="READ",allowed=False,key=key)
    assert changed.status_code==201,changed.text
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="READ",allowed=False,
                    key=key).status_code==200
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="READ",allowed=True,
                    key=key).status_code==409
    manager=token(http,d,d["intake"])
    assert http.get(estimate).status_code==403
    owner=token(http,d,d["manager"])
    denied=http.get(policies+"/effective",params={
        "locationId":str(loc),"userId":str(d["intake"]),
        "documentKind":"ESTIMATE"})
    assert denied.status_code==200
    assert denied.json()["capabilities"]["READ"]["allowed"] is False

    # Читаем реально все 520 предыдущих immutable событий, без дублей;
    # более новая запись DENY должна находиться только на свежей странице 1.
    while cursor is not None:
        part=http.get(policies,params={"limit":50,"before":cursor})
        assert part.status_code==200,part.text
        payload=part.json()
        entries=payload["items"]
        assert 0<len(entries)<=50
        next_ids=[x["eventId"] for x in entries]
        assert not(set(all_ids)&set(next_ids))
        all_ids.extend(next_ids)
        cursor=payload.get("nextBefore")
    assert len(all_ids)==520
    assert len(set(all_ids))==520
    refreshed=http.get(policies,params={"limit":50}).json()
    assert refreshed["items"][0]["allowed"] is False
    assert refreshed["items"][0]["eventId"] not in all_ids
    with psycopg.connect(DSN) as db:
        count=db.execute("""SELECT count(*) FROM auto_document_access_policy
          WHERE organization_id=%s""",(org,)).fetchone()[0]
        assert count==521

    # Попытка использовать курсор чужой организации — тот же 422.
    with psycopg.connect(DSN) as db:
        other_org,other_loc,_=_org_and_bay(db)
        admin(db,other_org,d["manager"])
    owner=token(http,d,d["manager"])
    other_rule=set_rule(http,url(other_org),owner,role="MANAGER",
                        document="ESTIMATE",cap="READ",allowed=False)
    assert other_rule.status_code==201,other_rule.text
    other_cursor=other_rule.json()["items"][0]["eventId"]
    assert http.get(policies,params={"before":other_cursor}).status_code==422
    assert http.get(url(other_org),params={"before":all_ids[0]}).status_code==422

    # Восстановление через свежую ALLOW без удаления старого запрета.
    restored=set_rule(http,policies,owner,loc=loc,user=d["intake"],
                      document="ESTIMATE",cap="READ",allowed=True)
    assert restored.status_code==201,restored.text
    manager=token(http,d,d["intake"])
    assert http.get(estimate).status_code==200

    # Серверный cutoff зависит от created_at WorkOrder, не от browser input.
    owner=token(http,d,d["manager"])
    cutoff=set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="READ",allowed=True,
                    not_before="2099-01-01T00:00:00Z")
    assert cutoff.status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(estimate).status_code==403
    owner=token(http,d,d["manager"])
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="READ",allowed=True).status_code==201
    # DENY в общем шаблоне сильнее личной ALLOW даже при большой истории.
    assert set_rule(http,policies,owner,role="MANAGER",
                    document="ESTIMATE",cap="READ",allowed=False).status_code==201
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="READ",allowed=True).status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(estimate).status_code==403
    owner=token(http,d,d["manager"])
    assert set_rule(http,policies,owner,role="MANAGER",
                    document="ESTIMATE",cap="READ",allowed=True).status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(estimate).status_code==200

    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s
            AND role_code='ADMIN' AND location_id IS NULL
            AND revoked_at IS NULL""",(org,d["manager"]))
    owner=token(http,d,d["manager"])
    assert http.get(policies,params={"before":all_ids[-1]}).status_code==403
    assert http.get(policies+"/effective",params={
        "locationId":str(loc),"userId":str(d["intake"]),
        "documentKind":"ESTIMATE"}).status_code==403
    assert set_rule(http,policies,owner,document="ESTIMATE").status_code==403


def test_policy_reader_lock_serializes_admin_over_500_history(monkeypatch):
    d,org,loc,order,service,foreign,_=scenario()
    with psycopg.connect(DSN) as db:
        admin(db,org,d["manager"])
        seed_many_versions(db,org,loc,d["manager"],d["intake"],count=510)
    with psycopg.connect(DSN) as reader:
        with reader.transaction():
            assert require_document_access(reader,d["tokens"][d["intake"]],
                    org,loc,("MANAGER",),"ESTIMATE","READ",order=order) is None
            with psycopg.connect(DSN) as writer:
                writer.execute("SET lock_timeout='200ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    with writer.transaction():
                        from roboport_api.auto_document_access import publish_policy
                        publish_policy(writer,d["tokens"][d["manager"]],org,
                            request_key=uuid4(),role="MANAGER",loc=loc,
                            user=d["intake"],document="ESTIMATE",cap="READ",
                            allowed=False,not_before=None)
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_document_access_policy
          WHERE organization_id=%s""",(org,)).fetchone()[0]==510


def test_finance_prices_are_enforced_after_520_policy_versions(monkeypatch):
    from test_pg_auto_treasury_http import prepare as treasury_prepare

    d,org,loc,order,service,http,base,_=treasury_prepare(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
        seed_many_versions(db,org,loc,d["other"],d["intake"],
                           count=520,document="FINANCE",capability="PRICE_SALES")
    manager=token(http,d,d["intake"])
    assert http.get(base+"/accounts").status_code==200
    owner=token(http,d,d["other"])
    preview=http.get(url(org)+"/effective",params={
        "locationId":str(loc),"userId":str(d["intake"]),
        "documentKind":"FINANCE"})
    assert preview.status_code==200,preview.text
    assert preview.json()["capabilities"]["PRICE_SALES"]["allowed"] is True

    denied=set_rule(http,url(org),owner,loc=loc,user=d["intake"],
                    role="MANAGER",document="FINANCE",
                    cap="PRICE_SALES",allowed=False)
    assert denied.status_code==201,denied.text
    manager=token(http,d,d["intake"])
    assert http.get(base+"/accounts").status_code==403
    owner=token(http,d,d["other"])
    assert set_rule(http,url(org),owner,loc=loc,user=d["intake"],
                    role="MANAGER",document="FINANCE",
                    cap="PRICE_SALES",allowed=True).status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(base+"/accounts").status_code==200
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_document_access_policy
          WHERE organization_id=%s AND document_kind='FINANCE'""",
          (org,)).fetchone()[0]==522
