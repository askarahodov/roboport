"""Real PostgreSQL approval requires latest SENT version; decisions immutable."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_baseline import DSN


def test_outdated_quote_rejected_and_latest_decision_is_immutable():
    with psycopg.connect(DSN) as conn:
        customer,ticket,older,latest,actor,decision = (uuid4() for _ in range(6))
        conn.execute("INSERT INTO customer(id,label) VALUES (%s,'approval synthetic')",(customer,))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(ticket,customer))
        for quote,version in [(older,1),(latest,2)]:
            conn.execute("INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency) VALUES (%s,%s,%s,'SENT',100,'RUB')",(quote,ticket,version))
        with pytest.raises(psycopg.errors.RaiseException,match="stale estimate version"):
            with conn.transaction():
                conn.execute("INSERT INTO estimate_approval(id,estimate_id,actor_id,decision) VALUES (%s,%s,%s,'APPROVED')",(uuid4(),older,actor))
        conn.execute("INSERT INTO estimate_approval(id,estimate_id,actor_id,decision) VALUES (%s,%s,%s,'APPROVED')",(decision,latest,actor))
        assert conn.execute("SELECT state FROM estimate WHERE id=%s",(latest,)).fetchone()[0] == "APPROVED"
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("INSERT INTO estimate_approval(id,estimate_id,actor_id,decision) VALUES (%s,%s,%s,'DECLINED')",(uuid4(),latest,actor))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE estimate_approval SET decision='DECLINED' WHERE id=%s",(decision,))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("DELETE FROM estimate_approval WHERE id=%s",(decision,))


def test_draft_quote_cannot_be_approved():
    with psycopg.connect(DSN) as conn:
        customer,ticket,quote,actor=(uuid4() for _ in range(4))
        conn.execute("INSERT INTO customer(id,label) VALUES (%s,'draft synthetic')",(customer,))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(ticket,customer))
        conn.execute("INSERT INTO estimate(id,ticket_id,version,total_minor,currency) VALUES (%s,%s,1,100,'RUB')",(quote,ticket))
        with pytest.raises(psycopg.errors.RaiseException,match="only SENT"):
            with conn.transaction():
                conn.execute("INSERT INTO estimate_approval(id,estimate_id,actor_id,decision) VALUES (%s,%s,%s,'APPROVED')",(uuid4(),quote,actor))
