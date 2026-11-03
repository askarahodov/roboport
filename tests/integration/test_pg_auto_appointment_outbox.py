"""PG17: атомарные synthetic события записи, переноса и отмены, безопасный retry."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_appointment_service import (
    create_appointment, reschedule_appointment, cancel_appointment,
)
from roboport_api.notification_outbox import claim_one, finish_one, run_once
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN


def _ready_scope():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        db.execute(
            "UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
            (d["manager"], d["link"]),
        )
        db.execute(
            "INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
            (d["vehicle"], loc),
        )
        db.execute(
            """INSERT INTO auto_organization_role_grant
               (id,organization_id,location_id,user_id,role_code)
               VALUES(%s,%s,%s,%s,'INTAKE')""",
            (uuid4(), org, loc, d["intake"]),
        )
    return d, org, loc, bay


def test_create_reschedule_cancel_enqueue_once_without_personal_data():
    d, org, loc, bay = _ready_scope()
    token = d["tokens"][d["intake"]]
    start = datetime.now(timezone.utc) + timedelta(days=21)
    with psycopg.connect(DSN) as db:
        appointment, first, order = create_appointment(
            db, token, org, loc, bay, d["link"], start,
            start + timedelta(hours=1), request_key=uuid4(),
        )
        new = reschedule_appointment(
            db, token, org, appointment, bay,
            start + timedelta(hours=2), start + timedelta(hours=3),
            request_key=uuid4(),
        )
        assert new != first
        assert cancel_appointment(db, token, org, appointment) == "CANCELED"
        assert cancel_appointment(db, token, org, appointment) == "ALREADY_CANCELED"
        rows = db.execute(
            """SELECT event_key,event_type,payload,status
               FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s
               ORDER BY created_at,event_key""",
            (appointment,),
        ).fetchall()
        assert len(rows) == 3
        assert {r[1] for r in rows} == {"CREATED", "RESCHEDULED", "CANCELED"}
        assert len({r[0] for r in rows}) == 3
        assert {r[1]:r[3] for r in rows} == {
            "CREATED":"SUPERSEDED", "RESCHEDULED":"SUPERSEDED",
            "CANCELED":"PENDING",
        }
        assert all(set(r[2]) == {"action"} and r[2]["action"] == r[1] for r in rows)
        assert all("customer" not in str(r[2]).lower() and
                   "vehicle" not in str(r[2]).lower() and
                   "email" not in str(r[2]).lower() for r in rows)
        assert db.execute(
            "SELECT count(*) FROM auto_work_order WHERE id=%s AND appointment_id=%s",
            (order, appointment),
        ).fetchone()[0] == 1


def test_auto_outbox_worker_backoff_delivery_and_no_duplicate_sink():
    d, org, loc, bay = _ready_scope()
    token = d["tokens"][d["intake"]]
    start = datetime.now(timezone.utc) + timedelta(days=22)
    with psycopg.connect(DSN) as db:
        appointment, _, _ = create_appointment(
            db, token, org, loc, bay, d["link"], start,
            start + timedelta(hours=1),
        )
        event = db.execute(
            """SELECT id FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0]
        first = run_once(db, "auto-failing", simulate_failure=True, aggregate_id=appointment)
        assert first and first["status"] == "RETRY"
        assert db.execute(
            "SELECT attempts,status FROM notification_outbox WHERE id=%s", (event,)
        ).fetchone() == (1, "PENDING")
        db.execute(
            "UPDATE notification_outbox SET available_at=now()-interval '1 minute' WHERE id=%s",
            (event,),
        )
        assert run_once(db, "auto-rescue", aggregate_id=appointment)["status"] == "DELIVERED"
        assert run_once(db, "auto-late", aggregate_id=appointment) is None
        assert db.execute(
            """SELECT attempts,status FROM notification_outbox WHERE id=%s""",
            (event,),
        ).fetchone() == (2, "DELIVERED")
        assert db.execute(
            "SELECT count(*) FROM synthetic_notification_delivery WHERE outbox_id=%s",
            (event,),
        ).fetchone()[0] == 1


def test_rollback_drops_auto_outbox_with_appointment():
    d, org, loc, bay = _ready_scope()
    token = d["tokens"][d["intake"]]
    start = datetime.now(timezone.utc) + timedelta(days=23)
    appointment = None
    with psycopg.connect(DSN) as db:
        with pytest.raises(RuntimeError):
            with db.transaction():
                appointment, _, _ = create_appointment(
                    db, token, org, loc, bay, d["link"], start,
                    start + timedelta(hours=1),
                )
                assert db.execute(
                    """SELECT count(*) FROM notification_outbox
                       WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
                    (appointment,),
                ).fetchone()[0] == 1
                raise RuntimeError("synthetic rollback")
        assert appointment is not None
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT count(*) FROM auto_appointment WHERE id=%s", (appointment,)
        ).fetchone()[0] == 0


def test_supersession_dispatches_only_current_notification_and_preserves_audit():
    """Create/move/cancel before worker: only cancellation enters the DEV_SINK."""
    d,org,loc,bay=_ready_scope()
    token=d["tokens"][d["intake"]]
    start=datetime.now(timezone.utc)+timedelta(days=26)
    with psycopg.connect(DSN) as db:
        appointment,_,_=create_appointment(
            db,token,org,loc,bay,d["link"],start,start+timedelta(hours=1))
        reschedule_appointment(
            db,token,org,appointment,bay,
            start+timedelta(hours=2),start+timedelta(hours=3))
        assert cancel_appointment(db,token,org,appointment)=="CANCELED"
        rows=db.execute(
            """SELECT id,event_type,status FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchall()
        assert {event:status for _,event,status in rows} == {
            "CREATED":"SUPERSEDED",
            "RESCHEDULED":"SUPERSEDED",
            "CANCELED":"PENDING",
        }
        assert run_once(db,"auto-current",aggregate_id=appointment)["status"]=="DELIVERED"
        assert run_once(db,"auto-later",aggregate_id=appointment) is None
        assert db.execute(
            """SELECT o.event_type FROM synthetic_notification_delivery d
               JOIN notification_outbox o ON o.id=d.outbox_id
               WHERE o.aggregate_type='auto_appointment' AND o.aggregate_id=%s""",
            (appointment,),
        ).fetchall()==[("CANCELED",)]
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0]==3
        for event,event_type,status in rows:
            if status=="SUPERSEDED":
                with pytest.raises(psycopg.errors.RaiseException):
                    with db.transaction():
                        db.execute(
                            "UPDATE notification_outbox SET status='PENDING' WHERE id=%s",
                            (event,),
                        )


def test_reschedule_replaces_queued_created_but_not_already_delivered_notice():
    d,org,loc,bay=_ready_scope()
    token=d["tokens"][d["intake"]]
    start=datetime.now(timezone.utc)+timedelta(days=27)
    with psycopg.connect(DSN) as db:
        appointment,_,_=create_appointment(
            db,token,org,loc,bay,d["link"],start,start+timedelta(hours=1))
        created=run_once(db,"sent-first",aggregate_id=appointment)
        assert created and created["status"]=="DELIVERED"
        reschedule_appointment(
            db,token,org,appointment,bay,
            start+timedelta(hours=2),start+timedelta(hours=3))
        assert run_once(db,"sent-move",aggregate_id=appointment)["status"]=="DELIVERED"
        assert run_once(db,"sent-later",aggregate_id=appointment) is None
        assert db.execute(
            """SELECT event_type,status FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s
               ORDER BY created_at,event_key""",
            (appointment,),
        ).fetchall()==[("CREATED","DELIVERED"),("RESCHEDULED","DELIVERED")]


def test_supersession_fences_worker_that_claimed_an_obsolete_event():
    """Claim committed but not finished; move atomically revokes its lease."""
    d,org,loc,bay=_ready_scope()
    token=d["tokens"][d["intake"]]
    start=datetime.now(timezone.utc)+timedelta(days=28)
    with psycopg.connect(DSN) as db:
        appointment,_,_=create_appointment(
            db,token,org,loc,bay,d["link"],start,start+timedelta(hours=1))
    with psycopg.connect(DSN) as db:
        old=claim_one(db,"crashed-before-move",aggregate_id=appointment)
        assert old and old["eventType"]=="CREATED"
    with psycopg.connect(DSN) as db:
        reschedule_appointment(
            db,token,org,appointment,bay,
            start+timedelta(hours=2),start+timedelta(hours=3))
        assert finish_one(db,"crashed-before-move",old["id"],old["attempt"]) is False
        assert db.execute(
            "SELECT status,claimed_by FROM notification_outbox WHERE id=%s",
            (old["id"],),
        ).fetchone()==("SUPERSEDED",None)
        assert run_once(db,"current-move",aggregate_id=appointment)["status"]=="DELIVERED"
        assert db.execute(
            "SELECT count(*) FROM synthetic_notification_delivery WHERE outbox_id=%s",
            (old["id"],),
        ).fetchone()[0]==0


def test_rolled_back_cancellation_does_not_suppress_current_notice():
    d,org,loc,bay=_ready_scope()
    token=d["tokens"][d["intake"]]
    start=datetime.now(timezone.utc)+timedelta(days=29)
    with psycopg.connect(DSN) as db:
        appointment,_,_=create_appointment(
            db,token,org,loc,bay,d["link"],start,start+timedelta(hours=1))
        with pytest.raises(RuntimeError):
            with db.transaction():
                assert cancel_appointment(db,token,org,appointment)=="CANCELED"
                assert db.execute(
                    """SELECT status FROM notification_outbox
                       WHERE aggregate_type='auto_appointment'
                         AND aggregate_id=%s AND event_type='CREATED'""",
                    (appointment,),
                ).fetchone()==("SUPERSEDED",)
                raise RuntimeError("roll back cancellation")
        assert db.execute(
            """SELECT event_type,status FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchall()==[("CREATED","PENDING")]
        assert run_once(db,"after-rollback",aggregate_id=appointment)["status"]=="DELIVERED"
