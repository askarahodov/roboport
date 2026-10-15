"""A single SQL statement checks live sessions and ownership for ticket read."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from roboport_api.session_lookup import session_can_read_ticket
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def test_atomic_session_ticket_check_revocation_and_expiry():
    user,client,ticket,session=(uuid4() for _ in range(4))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(user,"atomic-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(client,"atomic-client"))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,client))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(user,client))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (session,user,token_digest(token),token_digest(new_token()),now,
             now+timedelta(minutes=30),now+timedelta(hours=4)))
        assert session_can_read_ticket(db,token,ticket)
        assert not session_can_read_ticket(db,"invalid",ticket)
        assert not session_can_read_ticket(db,token,uuid4())
        db.execute("UPDATE user_session SET idle_expires_at=now()-interval '1 second' WHERE id=%s",(session,))
        assert not session_can_read_ticket(db,token,ticket)
        db.execute("UPDATE user_session SET idle_expires_at=now()+interval '30 minutes' WHERE id=%s",(session,))
        assert session_can_read_ticket(db,token,ticket)
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user,))
        assert not session_can_read_ticket(db,token,ticket)
