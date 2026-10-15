"""Real PostgreSQL 17 constraints; executed only against disposable CI DB."""
import os
from uuid import uuid4

import psycopg
import pytest

DSN = os.environ["ROBOport_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)


def test_foreign_key_and_ticket_state_and_version():
    with psycopg.connect(DSN) as conn:
        with conn.transaction():
            customer, ticket = uuid4(), uuid4()
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",
                        (ticket, customer),
                    )
            conn.execute("INSERT INTO customer(id,label) VALUES(%s,'synthetic')", (customer,))
            conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)", (ticket, customer))
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute("UPDATE service_ticket SET state='WRONG_STATE' WHERE id=%s", (ticket,))
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute("UPDATE service_ticket SET version=0 WHERE id=%s", (ticket,))


def test_ticket_owner_is_fixed_on_creation():
    with psycopg.connect(DSN) as conn:
        first, second, ticket = uuid4(), uuid4(), uuid4()
        conn.execute("INSERT INTO customer(id,label) VALUES(%s,'first'),(%s,'second')", (first,second))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)", (ticket,first))
        owner=conn.execute("SELECT customer_id FROM service_ticket WHERE id=%s",(ticket,)).fetchone()[0]
        assert owner == first
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute("UPDATE service_ticket SET customer_id=%s WHERE id=%s",(uuid4(),ticket))


def test_claimant_cannot_be_reassigned_and_audit_is_append_only():
    with psycopg.connect(DSN) as conn:
        a, b, ticket, audit = uuid4(), uuid4(), uuid4(), uuid4()
        conn.execute("INSERT INTO customer(id,label) VALUES(%s,'owner a'),(%s,'owner b')", (a,b))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)", (ticket,a))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE service_ticket SET customer_id=%s WHERE id=%s", (b,ticket))
        conn.execute("INSERT INTO audit_event(id,ticket_id,action) VALUES(%s,%s,'created')",(audit,ticket))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE audit_event SET action='tampered' WHERE id=%s",(audit,))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("DELETE FROM audit_event WHERE id=%s",(audit,))
