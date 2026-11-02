"""PG17: select synthetic branch catalog services on real synthetic work orders."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_baseline import DSN
from test_pg_auto_bay_booking import _org_and_bay, _book
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_service import grant
from roboport_api.auto_order_service_link import add_synthetic_order_service, OrderServiceDenied

def test_order_service_authority_branch_replay_and_trigger():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        foreign, foreign_loc, _ = _org_and_bay(db)
        booking = _book(db,org,loc,bay,'2035-04-01 08:00+00','2035-04-01 09:00+00')
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        appointment, order = uuid4(), uuid4()
        db.execute("""INSERT INTO auto_appointment(id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
                      VALUES(%s,%s,%s,%s,%s,%s)""",
                   (appointment,booking,org,loc,d["vehicle"],d["link"]))
        db.execute("""INSERT INTO auto_work_order(id,appointment_id,organization_id,location_id)
                      VALUES(%s,%s,%s,%s)""",(order,appointment,org,loc))
        service, alien = uuid4(), uuid4()
        db.execute("""INSERT INTO auto_service_catalog(id,organization_id,location_id,code,title,duration_minutes)
                      VALUES(%s,%s,%s,'TEST-LINK-01','SYNTHETIC',30)""",
                   (service,org,loc))
        db.execute("""INSERT INTO auto_service_catalog(id,organization_id,location_id,code,title,duration_minutes)
                      VALUES(%s,%s,%s,'TEST-LINK-02','SYNTHETIC',30)""",
                   (alien,foreign,foreign_loc))
        token = d["tokens"][d["intake"]]
        args = dict(organization_id=org,location_id=loc,work_order_id=order,
                    service_id=service,synthetic_only=True)
        with pytest.raises(OrderServiceDenied, match="FORBIDDEN"):
            add_synthetic_order_service(db,token,**args)
        key=grant(db,d["intake"],org,loc,"MANAGER")
        with pytest.raises(OrderServiceDenied, match="FORBIDDEN"):
            add_synthetic_order_service(db,token,**{**args,"synthetic_only":False})
        with pytest.raises(OrderServiceDenied, match="SCOPE_UNAVAILABLE"):
            add_synthetic_order_service(db,token,**{**args,"service_id":alien})
        with pytest.raises(psycopg.errors.RaiseException,match="scope or proof unavailable"):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_service
                              (id,work_order_id,service_id,organization_id,location_id)
                              VALUES(%s,%s,%s,%s,%s)""",
                           (uuid4(),order,alien,org,loc))
        assert add_synthetic_order_service(db,token,**args)["writes"] == 1
        assert add_synthetic_order_service(db,token,**args)["writes"] == 0
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_service WHERE work_order_id=%s",(order,))
        # A previously verified link can be revoked after the order exists.
        # New synthetic work-order service entries must then fail even with MANAGER.
        follow_up = uuid4()
        db.execute("""INSERT INTO auto_service_catalog
          (id,organization_id,location_id,code,title,duration_minutes)
          VALUES(%s,%s,%s,'TEST-LINK-03','FOLLOW UP',30)""",
          (follow_up,org,loc))
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
        with pytest.raises(psycopg.errors.RaiseException,
                           match="scope or proof unavailable"):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_service
                  (id,work_order_id,service_id,organization_id,location_id)
                  VALUES(%s,%s,%s,%s,%s)""",
                  (uuid4(),order,follow_up,org,loc))
        with pytest.raises(OrderServiceDenied, match="SCOPE_UNAVAILABLE"):
            add_synthetic_order_service(
                db,token,**{**args,"service_id":follow_up})
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(key,))
        with pytest.raises(OrderServiceDenied,match="FORBIDDEN"):
            add_synthetic_order_service(db,token,**args)
        assert db.execute("SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s",
                          (order,)).fetchone()[0] == 1
