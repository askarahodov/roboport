"""PG17: material-bearing booking cancel and stock after reschedule (#113).

Reuses existing WAREHOUSE ledger, no real goods, public API or data.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg
import pytest

from roboport_api.auto_appointment_service import (
    create_appointment, reschedule_appointment, cancel_appointment,
    AppointmentConflict,
)
from roboport_api.auto_work_order_stock import mutate_work_order_stock
from roboport_api.auto_work_order_return import return_work_order_stock
from test_pg_auto_customer_booking_http import _setup, _auth, ROOT
from test_pg_auto_stock_ledger import sku, record, balance
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def _ready(monkeypatch):
    d,org,loc,bay,_,_,http=_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        grant(db,d["intake"],org,loc,"INTAKE")
        grant(db,d["intake"],org,loc,"WAREHOUSE")
        item=sku(db,org,loc)
        record(db,item,"RECEIPT",5)
    return d,org,loc,bay,http,item


def _book(d,org,loc,bay,token):
    start=datetime.now(timezone.utc)+timedelta(days=33)
    with psycopg.connect(DSN) as db:
        appointment,booking,order=create_appointment(
            db,token,org,loc,bay,d["link"],start,start+timedelta(hours=1),
            request_key=uuid4(),
        )
    return appointment,booking,order,start


def _stock(db,d,org,loc,order,item,action,qty,key=None):
    return mutate_work_order_stock(
        db,d["tokens"][d["intake"]],org,loc,order,item,key or uuid4(),action,qty,
    )


def test_reserved_material_blocks_both_api_cancellation_and_direct_sql(monkeypatch):
    d,org,loc,bay,http,item=_ready(monkeypatch)
    appointment,booking,order,_=_book(d,org,loc,bay,d["tokens"][d["intake"]])
    with psycopg.connect(DSN) as db:
        reserve=_stock(db,d,org,loc,order,item,"RESERVE",3)
        assert balance(db,item)==(5,3)
        with pytest.raises(psycopg.errors.RaiseException,match="reconciled before cancel"):
            with db.transaction():
                db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",
                           (booking,))
        assert db.execute("SELECT canceled_at IS NULL FROM auto_bay_booking WHERE id=%s",
                          (booking,)).fetchone()==(True,)
    headers=_auth(http,d,d["client_a"])
    own=http.get(ROOT+"/appointments")
    assert own.status_code==200,own.text
    assert own.json()["items"][0]["canManage"] is False
    customer_cancel=http.post(
        ROOT+"/appointments/"+str(appointment)+"/cancel",
        json={"organizationId":str(org)},headers=headers,
    )
    assert customer_cancel.status_code==403,customer_cancel.text
    staff_headers=_auth(http,d,d["intake"])
    staff_path=f"/api/v1/dev/auto/organizations/{org}/appointments/{appointment}/cancel"
    staff_cancel=http.post(staff_path,headers=staff_headers)
    assert staff_cancel.status_code==409,staff_cancel.text
    assert staff_cancel.json()["code"]=="STOCK_RECONCILIATION_REQUIRED"
    with psycopg.connect(DSN) as db:
        assert balance(db,item)==(5,3)
        assert db.execute(
            "SELECT count(*) FROM auto_appointment_event WHERE appointment_id=%s AND action='BOOKING_CANCELED'",
            (appointment,),
        ).fetchone()[0]==0
        assert db.execute(
            "SELECT count(*) FROM notification_outbox WHERE aggregate_type='auto_appointment' AND aggregate_id=%s",
            (appointment,),
        ).fetchone()[0]==1
        _stock(db,d,org,loc,order,item,"RELEASE",3)
        assert balance(db,item)==(5,0)
    released=http.post(staff_path,headers=staff_headers)
    assert released.status_code==200,released.text
    assert released.json()["status"]=="CANCELED"
    assert http.post(staff_path,headers=staff_headers).json()["status"]=="ALREADY_CANCELED"


def test_rescheduled_work_order_can_complete_reserved_issue_without_orphan(monkeypatch):
    d,org,loc,bay,http,item=_ready(monkeypatch)
    token=d["tokens"][d["intake"]]
    appointment,old,order,start=_book(d,org,loc,bay,token)
    with psycopg.connect(DSN) as db:
        _stock(db,d,org,loc,order,item,"RESERVE",2)
        newer=reschedule_appointment(
            db,token,org,appointment,bay,
            start+timedelta(hours=2),start+timedelta(hours=3),
            request_key=uuid4(),
        )
        assert newer!=old
        assert balance(db,item)==(5,2)
        # The former initial-booking-only ledger function failed here.
        event=_stock(db,d,org,loc,order,item,"ISSUE",1)
        assert event
        assert balance(db,item)==(4,1)
        with pytest.raises(AppointmentConflict,match="STOCK_RECONCILIATION_REQUIRED"):
            cancel_appointment(db,token,org,appointment)
        _stock(db,d,org,loc,order,item,"RELEASE",1)
        # Issued physical stock must still require a formal stock disposition.
        with pytest.raises(AppointmentConflict,match="STOCK_RECONCILIATION_REQUIRED"):
            cancel_appointment(db,token,org,appointment)
        assert db.execute(
            "SELECT canceled_at IS NULL FROM auto_bay_booking WHERE id=%s",(newer,)
        ).fetchone()==(True,)
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0]==2
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_stock_event WHERE work_order_id=%s",
            (order,),
        ).fetchone()[0]==3
        receipt=return_work_order_stock(
            db,token,org,loc,order,item,uuid4(),1,
        )
        assert receipt
        assert balance(db,item)==(5,0)
        assert cancel_appointment(db,token,org,appointment)=="CANCELED"
        assert db.execute(
            "SELECT canceled_at IS NOT NULL FROM auto_bay_booking WHERE id=%s",(newer,)
        ).fetchone()==(True,)
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0]==3
