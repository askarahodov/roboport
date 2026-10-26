"""Real PostgreSQL guest-claim acceptance: no access until proof and separate review."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN

def test_claim_real_db_two_roles_proof_replay_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    client,other,intake,manager=[uuid4() for _ in range(4)]
    customer,ticket=uuid4(),uuid4()
    tokens={u:new_token() for u in (client,other,intake,manager)}
    csrf={u:new_token() for u in tokens}
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        for u in tokens:
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(u,u.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                (uuid4(),u,"INTAKE" if u==intake else "MANAGER" if u==manager else "CLIENT"))
            db.execute("""INSERT INTO user_session
                (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(),u,token_digest(tokens[u]),token_digest(csrf[u]),now,
                 now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,'synthetic guest')",(customer,))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,customer))
        for u,action in ((intake,"ticket.intake"),(manager,"ticket.manage")):
            db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,%s)",
                (uuid4(),u,ticket,action))
    http=TestClient(app,base_url="https://127.0.0.1")
    headers=lambda u:{"Origin":"https://127.0.0.1","X-CSRF-Token":csrf[u]}
    def login(u):http.cookies.set("rp_session",tokens[u])
    issue=f"/api/v1/dev/guest-claims/tickets/{ticket}/issue"
    account="/api/v1/dev/account/tickets"
    login(client)
    assert http.post(issue,headers=headers(client)).status_code==403
    assert http.get(account).json()["items"]==[]
    login(intake)
    assert http.post(issue).status_code==403
    issued=http.post(issue,headers=headers(intake))
    assert issued.status_code==200,issued.text
    cid=issued.json()["claimId"]
    proof=issued.json()["proof"]
    assert len(proof)>=40
    assert "no-store" in issued.headers["cache-control"]
    assert http.post(issue,headers=headers(intake)).status_code==403
    redeem=f"/api/v1/dev/guest-claims/{cid}/redeem"
    review=f"/api/v1/dev/guest-claims/{cid}/review"
    login(other)
    assert http.post(review,headers=headers(other),json={"approve":True}).status_code==403
    login(client)
    assert http.post(redeem,headers=headers(client),json={"proof":"x"*44}).json()["status"]=="REJECTED"
    assert http.get(account).json()["items"]==[]
    assert http.post(redeem,headers=headers(client),json={"proof":proof}).json()["status"]=="PENDING_REVIEW"
    assert http.post(redeem,headers=headers(client),json={"proof":proof}).status_code==403
    assert http.get(account).json()["items"]==[]
    login(manager)
    approved=http.post(review,headers=headers(manager),json={"approve":True})
    assert approved.status_code==200,approved.text
    assert approved.json()["status"]=="VERIFIED"
    assert http.post(review,headers=headers(manager),json={"approve":True}).status_code==403
    login(client)
    assert [x["id"] for x in http.get(account).json()["items"]]==[str(ticket)]
    login(other)
    assert http.get(account).json()["items"]==[]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s AND customer_id=%s",(client,customer))
    login(client)
    assert http.get(account).json()["items"]==[]
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM guest_claim_audit WHERE claim_id=%s",(cid,)).fetchone()[0]==4
        assert db.execute("SELECT count(*) FROM account_customer_link WHERE user_id=%s AND customer_id=%s AND revoked_at IS NULL",(client,customer)).fetchone()[0]==0

def test_claim_expired_proof_never_binds(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    from roboport_api.guest_claim import redeem_proof,ClaimRejected
    customer,ticket,user,claim=uuid4(),uuid4(),uuid4(),uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES(%s,'synthetic expired')",(customer,))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,customer))
        db.execute("INSERT INTO guest_ticket_claim(id,ticket_id,customer_id,proof_hash,proof_expires_at) VALUES(%s,%s,%s,%s,now()-interval '1 minute')",
            (claim,ticket,customer,"0"*64))
        assert db.execute("SELECT status FROM guest_ticket_claim WHERE id=%s",(claim,)).fetchone()[0]=="PENDING_REVIEW"
        assert not db.execute("SELECT 1 FROM account_customer_link WHERE customer_id=%s",(customer,)).fetchone()
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(user,user.hex))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",(uuid4(),user))
        token=new_token()
        now=datetime.now(timezone.utc)
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s)""",(uuid4(),user,token_digest(token),token_digest(new_token()),
              now,now+timedelta(minutes=30),now+timedelta(hours=4)))
        from roboport_api.guest_claim import redeem_proof,ClaimRejected
        import pytest
        with pytest.raises(ClaimRejected,match="expired"):
            redeem_proof(db,token,claim,"synthetic-proof-with-sufficient-length")
        assert not db.execute("SELECT 1 FROM account_customer_link WHERE customer_id=%s",(customer,)).fetchone()



def test_anonymous_guest_intake_has_no_ticket_access_and_staff_only_assignment(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    http=TestClient(app,base_url="https://127.0.0.1")
    url="/api/v1/dev/guest-intake"
    origin={"Origin":"https://127.0.0.1","Idempotency-Key":"synthetic-guest-"+uuid4().hex}
    assert http.post(url,json={},headers={"Idempotency-Key":origin["Idempotency-Key"]}).status_code==403
    assert http.post(url,json={"userId":str(uuid4())},headers=origin).status_code==422
    new=http.post(url,json={},headers=origin)
    assert new.status_code==202,new.text
    assert new.json()["status"]=="PENDING_REVIEW"
    receipt=new.json()["requestId"]
    assert "ticketId" not in new.text and "customerId" not in new.text
    replay=http.post(url,json={},headers=origin)
    assert replay.status_code==202 and replay.json()["requestId"]==receipt
    assert http.get("/api/v1/dev/account/tickets").status_code==401
    assert http.post(f"{url}/{receipt}/assign",headers=origin).status_code==403
    with psycopg.connect(DSN) as db:
        staff,token,csrf=uuid4(),new_token(),new_token()
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(staff,staff.hex))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",(uuid4(),staff))
        now=datetime.now(timezone.utc)
        db.execute("""INSERT INTO user_session
          (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s)""",
          (uuid4(),staff,token_digest(token),token_digest(csrf),now,
           now+timedelta(minutes=30),now+timedelta(hours=4)))
    http.cookies.set("rp_session",token)
    assigned=http.post(f"{url}/{receipt}/assign",headers={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf})
    assert assigned.status_code==200,assigned.text
    ticket=assigned.json()["ticketId"]
    assert http.post(f"{url}/{receipt}/assign",headers={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf}).status_code==403
    assert http.post(f"/api/v1/dev/guest-claims/tickets/{ticket}/issue",
       headers={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf}).status_code==200
