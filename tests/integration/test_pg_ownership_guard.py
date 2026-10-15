"""Ownership is based on original ticket claimant and active linked account."""
from uuid import uuid4
import psycopg
from test_pg_baseline import DSN
from roboport_api.ownership import may_read_ticket


def test_ticket_access_requires_active_link_and_user():
    a,b,customer_a,customer_b,ticket_a,ticket_b=(uuid4() for _ in range(6))
    with psycopg.connect(DSN) as conn:
        for user,login in [(a,"client-a"),(b,"client-b")]:
            conn.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(user,login))
        for cid,label in [(customer_a,"a"),(customer_b,"b")]:
            conn.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(cid,label))
        for ticket,cid in [(ticket_a,customer_a),(ticket_b,customer_b)]:
            conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,cid))
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s),(%s,%s)",(a,customer_a,b,customer_b))
        assert may_read_ticket(conn,a,ticket_a)
        assert not may_read_ticket(conn,a,ticket_b)
        assert may_read_ticket(conn,b,ticket_b)
        assert not may_read_ticket(conn,b,ticket_a)
        # Sharing device ownership never grants access to a former claimant's ticket.
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(b,customer_a))
        assert may_read_ticket(conn,b,ticket_a)  # explicit verified link grants access
        conn.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s AND customer_id=%s",(b,customer_a))
        assert not may_read_ticket(conn,b,ticket_a)
        conn.execute("UPDATE app_user SET status='DISABLED' WHERE id=%s",(a,))
        assert not may_read_ticket(conn,a,ticket_a)
        assert not may_read_ticket(conn,a,uuid4())
