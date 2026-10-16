"""Transactional outbox and two-worker negative tests on disposable PostgreSQL 17."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import psycopg
import pytest

from roboport_api.notification_outbox import claim_one,finish_one,run_once
from test_pg_baseline import DSN


def seed_ticket():
    customer,ticket=uuid4(),uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES (%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(ticket,customer))
    return ticket


def test_ticket_and_estimate_outbox_atomic_with_transitions():
    ticket=seed_ticket()
    estimate=uuid4()
    with psycopg.connect(DSN) as db:
        created=db.execute("""SELECT event_type,payload FROM notification_outbox
           WHERE aggregate_id=%s""",(ticket,)).fetchall()
        assert len(created)==1
        assert created[0][0]=="CREATED" and created[0][1]["state"]=="DRAFT"
        db.execute("UPDATE service_ticket SET state='SUBMITTED',version=version+1 WHERE id=%s",(ticket,))
        db.execute("""INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency)
          VALUES(%s,%s,1,'DRAFT',0,'RUB')""",(estimate,ticket))
        db.execute("UPDATE estimate SET state='SENT' WHERE id=%s",(estimate,))
        events=db.execute("""SELECT aggregate_type,event_type,payload FROM notification_outbox
            WHERE aggregate_id IN (%s,%s) ORDER BY created_at,event_key""",(ticket,estimate)).fetchall()
        assert len(events)==4
        assert len({(x[0],x[1],x[2]["state"]) for x in events})==4
        assert all(set(x[2])=={"state","version"} for x in events)
        assert all("email" not in str(x[2]).lower() for x in events)


def test_rollback_never_leaves_orphan_outbox():
    customer,ticket=uuid4(),uuid4()
    with psycopg.connect(DSN) as db:
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
                db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,customer))
                assert db.execute("SELECT count(*) FROM notification_outbox WHERE aggregate_id=%s",
                                  (ticket,)).fetchone()[0]==1
                raise RuntimeError("simulate business rollback")
        assert db.execute("SELECT count(*) FROM notification_outbox WHERE aggregate_id=%s",
                          (ticket,)).fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM service_ticket WHERE id=%s",
                          (ticket,)).fetchone()[0]==0


def test_concurrent_workers_only_one_commits_dev_effect():
    ticket=seed_ticket()
    barrier=Barrier(2)
    def work(name):
        with psycopg.connect(DSN) as db:
            barrier.wait(timeout=20)
            return run_once(db,name,aggregate_id=ticket)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=[pool.submit(work,"w-"+str(i)) for i in range(2)]
        answers=[f.result(timeout=30) for f in future]
    assert sorted("none" if x is None else x["status"] for x in answers)==["DELIVERED","none"]
    with psycopg.connect(DSN) as db:
        row=db.execute("""SELECT o.attempts,o.status,count(d.outbox_id)
          FROM notification_outbox o LEFT JOIN synthetic_notification_delivery d ON d.outbox_id=o.id
          WHERE o.aggregate_id=%s GROUP BY o.id""",(ticket,)).fetchone()
        assert row==(1,"DELIVERED",1)


def test_crash_reclaim_is_fenced_and_sink_exactly_once():
    ticket=seed_ticket()
    with psycopg.connect(DSN) as db:
        first=claim_one(db,"crashed-worker",lease_seconds=1,aggregate_id=ticket)
        assert first and first["attempt"]==1
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE notification_outbox SET claimed_at=now()-interval '2 minutes' WHERE id=%s",
                   (first["id"],))
    with psycopg.connect(DSN) as db:
        second=claim_one(db,"rescue-worker",lease_seconds=1,aggregate_id=ticket)
        assert second and second["id"]==first["id"] and second["attempt"]==2
        assert finish_one(db,"crashed-worker",first["id"],1) is False
        assert finish_one(db,"rescue-worker",second["id"],2) is True
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM synthetic_notification_delivery WHERE outbox_id=%s",
                          (first["id"],)).fetchone()[0]==1
        assert run_once(db,"late-worker",aggregate_id=ticket) is None


def test_backoff_dead_letter_and_no_external_delivery():
    ticket=seed_ticket()
    with psycopg.connect(DSN) as db:
        event=db.execute("SELECT id FROM notification_outbox WHERE aggregate_id=%s",(ticket,)).fetchone()[0]
    for attempt in range(1,6):
        with psycopg.connect(DSN) as db:
            result=run_once(db,"failure-worker",simulate_failure=True,aggregate_id=ticket)
            assert result["status"]==("DEAD" if attempt==5 else "RETRY")
            status,attempts,next_time=db.execute("""SELECT status,attempts,
                available_at>clock_timestamp() FROM notification_outbox WHERE id=%s""",
                (event,)).fetchone()
            assert attempts==attempt
            assert status==("DEAD" if attempt==5 else "PENDING")
            if attempt<5:
                assert next_time
                db.execute("UPDATE notification_outbox SET available_at=now()-interval '1 minute' WHERE id=%s",
                           (event,))
    with psycopg.connect(DSN) as db:
        assert run_once(db,"unused-worker",aggregate_id=ticket) is None
        assert db.execute("SELECT count(*) FROM synthetic_notification_delivery WHERE outbox_id=%s",
                          (event,)).fetchone()[0]==0


def test_exhausted_crash_lease_goes_dead_without_duplicate_delivery():
    ticket=seed_ticket()
    with psycopg.connect(DSN) as db:
        event=db.execute("SELECT id FROM notification_outbox WHERE aggregate_id=%s",(ticket,)).fetchone()[0]
        db.execute("""UPDATE notification_outbox SET status='PROCESSING',attempts=5,
          claimed_by='crashed-fifth',claimed_at=now()-interval '2 minutes' WHERE id=%s""",(event,))
        assert claim_one(db,"next-worker",lease_seconds=1,aggregate_id=ticket) is None
        assert db.execute("SELECT status FROM notification_outbox WHERE id=%s",
                          (event,)).fetchone()[0]=="DEAD"
