"""Actual cross-operation PostgreSQL races with explicit transaction overlap."""
import threading
import time
from uuid import uuid4

import psycopg
from test_pg_baseline import DSN


def _seed():
    customer, ticket, quote = uuid4(), uuid4(), uuid4()
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO customer(id,label) VALUES (%s,'intertype-race')", (customer,))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)", (ticket,customer))
        conn.execute(
            "INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency) VALUES (%s,%s,1,'SENT',100,'RUB')",
            (quote,ticket),
        )
    return ticket,quote


def test_new_version_first_blocks_old_approval():
    ticket,quote=_seed()
    began=threading.Event()
    outcome=[]
    def create_new():
        with psycopg.connect(DSN) as conn:
            conn.execute("INSERT INTO estimate(id,ticket_id,version,total_minor,currency) VALUES (%s,%s,2,125,'RUB')",(uuid4(),ticket))
            began.set()
            time.sleep(.2)  # Holds ticket row lock until commit.
    def approve():
        assert began.wait(10)
        try:
            with psycopg.connect(DSN) as conn:
                conn.execute("SET lock_timeout = '5s'")
                conn.execute("INSERT INTO estimate_approval(id,estimate_id,actor_id,decision) VALUES (%s,%s,%s,'APPROVED')",(uuid4(),quote,uuid4()))
            outcome.append("approved")
        except psycopg.errors.RaiseException as exc:
            outcome.append("stale" if "stale estimate version" in str(exc) else repr(exc))
    a=threading.Thread(target=create_new);d=threading.Thread(target=approve)
    a.start();d.start();a.join(12);d.join(12)
    assert not a.is_alive() and not d.is_alive()
    assert outcome==["stale"],outcome


def test_approval_first_blocks_subsequent_new_version():
    ticket,quote=_seed()
    began=threading.Event()
    outcome=[]
    def approve():
        with psycopg.connect(DSN) as conn:
            conn.execute("INSERT INTO estimate_approval(id,estimate_id,actor_id,decision) VALUES (%s,%s,%s,'APPROVED')",(uuid4(),quote,uuid4()))
            began.set()
            time.sleep(.2)
    def create_new():
        assert began.wait(10)
        try:
            with psycopg.connect(DSN) as conn:
                conn.execute("SET lock_timeout = '5s'")
                conn.execute("INSERT INTO estimate(id,ticket_id,version,total_minor,currency) VALUES (%s,%s,2,125,'RUB')",(uuid4(),ticket))
            outcome.append("created")
        except psycopg.errors.RaiseException as exc:
            outcome.append("finalized" if "decision finalized" in str(exc) else repr(exc))
    a=threading.Thread(target=approve);d=threading.Thread(target=create_new)
    a.start();d.start();a.join(12);d.join(12)
    assert not a.is_alive() and not d.is_alive()
    assert outcome==["finalized"],outcome
