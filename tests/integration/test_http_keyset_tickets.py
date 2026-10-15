"""Keyset list must isolate clients and resist offset drift."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def test_keyset_pagination_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user,mine,other,sid=(uuid4() for _ in range(4))
    tickets=[uuid4() for _ in range(3)]
    foreign=uuid4()
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)",(user,"cursor-"+user.hex))
        for cid in (mine,other):
            db.execute("INSERT INTO customer(id,label) VALUES (%s,%s)",(cid,str(cid)))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES (%s,%s)",(user,mine))
        for tid in tickets:
            db.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(tid,mine))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(foreign,other))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s)""",(sid,user,token_digest(token),
                      token_digest(new_token()),now,now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app, base_url="https://127.0.0.1")
    path="/api/v1/dev/tickets/keyset"
    assert client.get(path).status_code==401
    client.cookies.set("rp_session",token)
    first=client.get(path,params={"limit":2})
    assert first.status_code==200,first.text
    assert first.json()["nextCursor"]
    assert client.get(path,params={"before":first.json()["nextCursor"],"state":"COMPLETED"}).status_code==422
    second=client.get(path,params={"limit":2,"before":first.json()["nextCursor"]})
    assert second.status_code==200,second.text
    collected=first.json()["items"]+second.json()["items"]
    assert {item["id"] for item in collected}=={str(t) for t in tickets}
    assert all(set(item)=={"id","state","version"} for item in collected)
    assert client.get(path,params={"before":"bad"}).status_code==422
    assert client.get(path,params={"state":"invalid"}).status_code==422
    assert client.get(path,params={"limit":51}).status_code==422
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user,))
    assert client.get(path).json()["items"]==[]
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API")
    assert client.get(path).status_code==404


def test_newer_ticket_inserted_between_pages_does_not_shift_cursor(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user,customer,sid=(uuid4() for _ in range(3))
    older,oldest,newest=(uuid4() for _ in range(3))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(user,"drift-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,"drift-"+customer.hex))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(user,customer))
        for tid,age in ((older,2),(oldest,3)):
            db.execute("INSERT INTO service_ticket(id,customer_id,created_at) VALUES(%s,%s,%s)",
                       (tid,customer,now-timedelta(days=age)))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (sid,user,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app, base_url="https://127.0.0.1")
    client.cookies.set("rp_session",token)
    url="/api/v1/dev/tickets/keyset"
    first=client.get(url,params={"limit":1})
    assert first.status_code==200,first.text
    assert first.json()["items"][0]["id"]==str(older)
    cursor=first.json()["nextCursor"]
    assert cursor
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO service_ticket(id,customer_id,created_at) VALUES(%s,%s,%s)",
                   (newest,customer,now))
    second=client.get(url,params={"limit":1,"before":cursor})
    assert second.status_code==200,second.text
    assert [v["id"] for v in second.json()["items"]]==[str(oldest)]
    assert second.json()["nextCursor"] is None
    fresh=client.get(url,params={"limit":1})
    assert fresh.json()["items"][0]["id"]==str(newest)

def test_keyset_signed_cursor_rejected_after_logout_and_wrong_session(monkeypatch):
    """Устаревший cursor не переносит права между пользовательскими сессиями."""
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    user,customer,session_a,session_b=(uuid4() for _ in range(4))
    first_ticket,second_ticket=(uuid4() for _ in range(2))
    token_a,token_b=new_token(),new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"cursor-revoke-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                   (customer,"cursor-revoke-"+customer.hex))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (user,customer))
        for tid in (first_ticket,second_ticket):
            db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                       (tid,customer))
        for sid,token in ((session_a,token_a),(session_b,token_b)):
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                          VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                       (sid,user,token_digest(token),token_digest(new_token()),now,
                        now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app,base_url="https://127.0.0.1")
    client.cookies.clear()
    client.cookies.set("rp_session",token_a)
    url="/api/v1/dev/tickets/keyset"
    first=client.get(url,params={"limit":1})
    assert first.status_code==200,first.text
    cursor=first.json()["nextCursor"]
    assert cursor

    # 1: курсор сессии A не принимается в B, даже если пользователь один.
    client.cookies.clear()
    client.cookies.set("rp_session",token_b)
    wrong_session=client.get(url,params={"limit":1,"before":cursor})
    assert wrong_session.status_code==422
    # 2: собственная активная сессия B продолжает работать.
    assert client.get(url,params={"limit":1}).status_code==200

    # 3: после отзыва A прежний cursor не даёт прочитать ни страницу.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE id=%s",(session_a,))
    client.cookies.clear()
    client.cookies.set("rp_session",token_a)
    assert client.get(url,params={"limit":1,"before":cursor}).status_code==401
    # 4: отозванная сессия не получает первую страницу.
    assert client.get(url,params={"limit":1}).status_code==401
    # 5: отзыв A не блокирует B.
    client.cookies.clear()
    client.cookies.set("rp_session",token_b)
    assert client.get(url,params={"limit":1}).status_code==200

    # 6: отзыв связки customer запрещает B просматривать заявки.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user,))
    empty=client.get(url,params={"limit":1})
    assert empty.status_code==200 and empty.json()["items"]==[]
    # 7: после revoke старая сессия A остаётся заблокирована.
    client.cookies.clear()
    client.cookies.set("rp_session",token_a)
    assert client.get(url,params={"limit":1}).status_code==401
    # 8: связка с customer не может обойти изоляцию посредством другой сессии.
    client.cookies.clear()
    client.cookies.set("rp_session",token_b)
    assert client.get(url,params={"limit":1}).json()["items"]==[]


def test_keyset_multi_link_empty_and_filtered_regression_bundle(monkeypatch):
    """Единый набор: пагинация, ACL и пустые выборки при нескольких связях."""
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    user, customer_a, customer_b, session_id=(uuid4() for _ in range(4))
    older, newer=(uuid4() for _ in range(2))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"multi-link-"+user.hex))
        for customer in (customer_a,customer_b):
            db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                       (customer,str(customer)))
            db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                       (user,customer))
        db.execute("INSERT INTO service_ticket(id,customer_id,state,created_at) VALUES(%s,%s,%s,%s)",
                   (newer,customer_a,"DRAFT",now))
        db.execute("INSERT INTO service_ticket(id,customer_id,state,created_at) VALUES(%s,%s,%s,%s)",
                   (older,customer_a,"COMPLETED",now-timedelta(days=1)))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session_id,user,token_digest(token),token_digest(new_token()),
                    now,now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app,base_url="https://127.0.0.1")
    client.cookies.set("rp_session",token)
    url="/api/v1/dev/tickets/keyset"

    # 1–3: только реальные записи, правильный порядок и одна следующая страница.
    first=client.get(url,params={"limit":1})
    assert first.status_code==200,first.text
    assert [v["id"] for v in first.json()["items"]]==[str(newer)]
    cursor=first.json()["nextCursor"]
    assert cursor
    second=client.get(url,params={"limit":1,"before":cursor})
    assert [v["id"] for v in second.json()["items"]]==[str(older)]
    assert second.json()["nextCursor"] is None

    # 4–6: полноразмерная страница и фильтры не возвращают NULL-заглушки.
    full=client.get(url,params={"limit":2})
    assert [v["id"] for v in full.json()["items"]]==[str(newer),str(older)]
    assert full.json()["nextCursor"] is None
    draft=client.get(url,params={"state":"DRAFT","limit":1})
    assert [v["id"] for v in draft.json()["items"]]==[str(newer)]
    assert draft.json()["nextCursor"] is None
    completed=client.get(url,params={"state":"COMPLETED","limit":1})
    assert [v["id"] for v in completed.json()["items"]]==[str(older)]
    assert completed.json()["nextCursor"] is None

    # 7: действующая сессия с фильтром без результатов — 200, не 401.
    empty=client.get(url,params={"state":"SAFETY_HOLD","limit":1})
    assert empty.status_code==200 and empty.json()=={"items":[],"nextCursor":None}
    # 8: отзыв только одной customer-связи не блокирует вторую.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s AND customer_id=%s",
                   (user,customer_b))
    still_visible=client.get(url,params={"limit":2})
    assert {v["id"] for v in still_visible.json()["items"]}=={str(newer),str(older)}
    # 9: отзыв оставшейся связи убирает обе заявки, сохраняя активную сессию.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s AND customer_id=%s",
                   (user,customer_a))
    revoked=client.get(url,params={"limit":1})
    assert revoked.status_code==200 and revoked.json()=={"items":[],"nextCursor":None}
