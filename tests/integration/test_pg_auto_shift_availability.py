"""PG17 shift coverage, tenant boundaries, cancellation and invalid inputs."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg

from roboport_api.auto_shift_availability import shift_covers_interval
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN


def test_shift_coverage_requires_one_active_tenant_scoped_shift():
    actor=_scenario()["intake"]
    other_actor=_scenario()["intake"]
    with psycopg.connect(DSN) as db:
        org,loc,_=_org_and_bay(db)
        other,other_loc,_=_org_and_bay(db)
        start=datetime.now(timezone.utc)+timedelta(days=5)
        end=start+timedelta(hours=2)
        shift=uuid4()
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (shift,org,loc,actor,start,end))
        check=lambda o,l,u,s,e: shift_covers_interval(db,o,l,u,s,e)
        assert check(org,loc,actor,start,end)
        assert check(org,loc,actor,start+timedelta(minutes=15),end)
        assert not check(org,loc,actor,start-timedelta(seconds=1),end)
        assert not check(org,loc,actor,start,end+timedelta(seconds=1))
        assert not check(org,loc,other_actor,start,end)
        assert not check(other,other_loc,actor,start,end)
        assert not check(org,other_loc,actor,start,end)
        assert not check(org,loc,actor,start,end+timedelta(days=2))
        assert not check(org,loc,actor,start,start)
        assert not check(org,loc,actor,start.replace(tzinfo=None),end)
        assert not check("not-a-uuid",loc,actor,start,end)
        db.execute("UPDATE auto_staff_shift SET canceled_at=now() WHERE id=%s",(shift,))
        assert not check(org,loc,actor,start,end)


def test_adjacent_shifts_do_not_falsely_cover_single_long_appointment():
    actor=_scenario()["intake"]
    with psycopg.connect(DSN) as db:
        org,loc,_=_org_and_bay(db)
        start=datetime.now(timezone.utc)+timedelta(days=6)
        middle=start+timedelta(hours=2)
        end=middle+timedelta(hours=2)
        for a,b in ((start,middle),(middle,end)):
            db.execute("""INSERT INTO auto_staff_shift
              (id,organization_id,location_id,user_id,starts_at,ends_at)
              VALUES(%s,%s,%s,%s,%s,%s)""",(uuid4(),org,loc,actor,a,b))
        assert shift_covers_interval(db,org,loc,actor,start,middle)
        assert shift_covers_interval(db,org,loc,actor,middle,end)
        assert not shift_covers_interval(db,org,loc,actor,start,end)
