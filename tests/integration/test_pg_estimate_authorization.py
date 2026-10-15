"""Real PostgreSQL: estimate approval requires live role, ticket and latest SENT."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from roboport_api.authorization import authorize_estimate
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def test_estimate_acl_status_latest_and_revoke_bundle(monkeypatch):
    from fastapi.testclient import TestClient
    from roboport_api.main import app
    monkeypatch.setenv('ROBOport_SYNTHETIC_ACCESS_API','1')
    monkeypatch.setenv('ROBOport_DATABASE_URL',DSN)
    client,finance,customer,ticket,estimate1,estimate2=(uuid4() for _ in range(6))
    tokens=(new_token(),new_token())
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,customer))
        for user,token,role in ((client,tokens[0],"CLIENT"),(finance,tokens[1],"FINANCE")):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                       (user,"estimate-acl-"+user.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                       (uuid4(),user,role))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                          VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                       (uuid4(),user,token_digest(token),token_digest(new_token()),now,
                        now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (client,customer))
        for eid,v in ((estimate1,1),(estimate2,2)):
            db.execute("""INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency)
                          VALUES(%s,%s,%s,'DRAFT',100,'RUB')""",(eid,ticket,v))
        # DRAFT documents hidden even when a CLIENT has customer link.
        assert not authorize_estimate(db,tokens[0],"estimate.read",estimate1)
        assert not authorize_estimate(db,tokens[0],"estimate.approve",estimate2)
        db.execute("UPDATE estimate SET state='SENT' WHERE id=%s",(estimate2,))
        assert authorize_estimate(db,tokens[0],"estimate.read",estimate2)
        assert authorize_estimate(db,tokens[0],"estimate.approve",estimate2)
        db.commit()  # HTTP TestClient uses its own PostgreSQL connection
        http=TestClient(app,base_url="https://127.0.0.1")
        http.cookies.set("rp_session",tokens[0])
        response=http.get(f"/api/v1/dev/estimates/{estimate2}/access")
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="no-store"
        assert not authorize_estimate(db,tokens[0],"estimate.approve",estimate1)
        assert not authorize_estimate(db,tokens[1],"estimate.read",estimate2)
        acl=uuid4()
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'estimate.read')",
                   (acl,finance,ticket))
        assert authorize_estimate(db,tokens[1],"estimate.read",estimate2)
        assert not authorize_estimate(db,tokens[1],"estimate.approve",estimate2)
        db.execute("UPDATE acl_entry SET revoked_at=now() WHERE id=%s",(acl,))
        assert not authorize_estimate(db,tokens[1],"estimate.read",estimate2)
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(client,))
        assert not authorize_estimate(db,tokens[0],"estimate.read",estimate2)
        assert not authorize_estimate(db,tokens[0],"estimate.approve",estimate2)
        db.commit()
        assert http.get(f"/api/v1/dev/estimates/{estimate2}/access").status_code==404
