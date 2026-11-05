"""PG17 + HTTPS: manager-only CRM source/status funnel of live lead cohorts."""
import csv
from io import StringIO
from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_crm_funnel import _funnel_csv
from roboport_api.auto_crm_leads import create_lead,transition_lead
from roboport_api.auto_appointment_service import create_appointment
from test_pg_auto_bay_occupancy_http import _fixture
from test_pg_baseline import DSN

URL=lambda org,loc:(f"/api/v1/dev/auto/organizations/{org}"
                    f"/locations/{loc}/crm/funnel")


def new_link(db,d,loc):
    vehicle,link=uuid4(),uuid4()
    db.execute(
        "INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Report')",
        (vehicle,),
    )
    db.execute(
        """INSERT INTO auto_vehicle_customer_link
           (id,vehicle_id,customer_id,verified_at,verified_by)
           VALUES(%s,%s,%s,now(),%s)""",
        (link,vehicle,d["ca"],d["manager"]),
    )
    db.execute(
        """INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id)
           VALUES(%s,%s)""",(vehicle,loc),
    )
    return link


def test_crm_funnel_full_new_qualified_booked_lost_cohort_and_live_revocation(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    zone=ZoneInfo("Europe/Berlin")
    day=datetime.now(zone).date().isoformat()
    endpoint=URL(org,loc)
    query={"fromDay":day,"toDay":day}
    assert http.get(endpoint,params=query).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert http.get(endpoint,params=query).status_code==403

    with psycopg.connect(DSN) as db:
        token=d["tokens"][d["intake"]]
        # Four independent verified synthetic leads: WEB=NEW + BOOKED,
        # PHONE=QUALIFIED, WALK_IN=LOST/PRICE. All have unique link+source.
        sources=[("WEB","NEW",d["link"]),
                 ("PHONE","QUALIFIED",new_link(db,d,loc)),
                 ("WALK_IN","LOST",new_link(db,d,loc)),
                 ("WEB","BOOKED",new_link(db,d,loc))]
        ids=[]
        for source,stage,link in sources:
            result=create_lead(db,token,org,loc,link,source,uuid4())
            ident=UUID(result["leadId"])
            ids.append((ident,link))
            if stage=="QUALIFIED":
                transition_lead(db,token,org,loc,ident,uuid4(),
                                "QUALIFIED",None,None)
            if stage=="LOST":
                transition_lead(db,token,org,loc,ident,uuid4(),
                                "LOST","PRICE",None)
            if stage=="BOOKED":
                transition_lead(db,token,org,loc,ident,uuid4(),
                                "QUALIFIED",None,None)
                start=datetime.now(timezone.utc)+timedelta(days=12)
                appointment,_,_=create_appointment(
                    db,token,org,loc,bay,link,start,start+timedelta(hours=1),
                )
                transition_lead(db,token,org,loc,ident,uuid4(),
                                "BOOKED",None,appointment)
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    report=http.get(endpoint,params=query)
    assert report.status_code==200,report.text
    assert report.headers["cache-control"]=="no-store"
    data=report.json()
    assert data["syntheticOnly"] is True
    assert data["basis"]=="LEAD_CREATED_COHORT_CURRENT_STAGE"
    assert data["timeZone"]=="Europe/Berlin"
    assert data["fromDay"]==day and data["toDay"]==day
    assert data["summary"]=={
        "sourceCode":"ALL","total":4,"new":1,"qualified":1,"booked":1,"lost":1,
        "bookedBasisPoints":2500,
        "lossReasons":{"PRICE":1,"TIMING":0,"NO_RESPONSE":0,"OTHER":0},
    }
    assert data["items"]==[
        {"sourceCode":"WEB","total":2,"new":1,"qualified":0,
         "booked":1,"lost":0,"bookedBasisPoints":5000,
         "lossReasons":{"PRICE":0,"TIMING":0,"NO_RESPONSE":0,"OTHER":0}},
        {"sourceCode":"PHONE","total":1,"new":0,"qualified":1,
         "booked":0,"lost":0,"bookedBasisPoints":0,
         "lossReasons":{"PRICE":0,"TIMING":0,"NO_RESPONSE":0,"OTHER":0}},
        {"sourceCode":"WALK_IN","total":1,"new":0,"qualified":0,
         "booked":0,"lost":1,"bookedBasisPoints":0,
         "lossReasons":{"PRICE":1,"TIMING":0,"NO_RESPONSE":0,"OTHER":0}},
    ]
    assert all(str(ident) not in report.text for ident,_ in ids)
    assert str(d["ca"]) not in report.text and str(d["vehicle"]) not in report.text
    assert str(d["intake"]) not in report.text and "vin" not in report.text.lower()

    # Repeated read never duplicates events/counts; result reflects
    # the latest stage, not multiple QUALIFIED+BOOKED historical events.
    assert http.get(endpoint,params=query).json()==data
    assert http.get(URL(foreign,foreign_loc),params=query).status_code==403
    assert http.get(URL(org,foreign_loc),params=query).status_code==403
    tomorrow=(datetime.now(zone).date()+timedelta(days=1)).isoformat()
    empty=http.get(endpoint,params={"fromDay":tomorrow,"toDay":tomorrow})
    assert empty.status_code==200,empty.text
    assert empty.json()["summary"]["total"]==0
    assert empty.json()["summary"]["bookedBasisPoints"] is None

    # Revocation of the booked lead's proof removes that lead from
    # today's live cohort before aggregation.
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
            (ids[-1][1],),
        )
    after=http.get(endpoint,params=query)
    assert after.status_code==200,after.text
    assert after.json()["summary"]["total"]==3
    assert after.json()["summary"]["booked"]==0
    assert after.json()["summary"]["bookedBasisPoints"]==0
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s AND user_id=%s
                 AND role_code='MANAGER'""",
            (org,loc,d["manager"]),
        )
    assert http.get(endpoint,params=query).status_code==403


def test_funnel_date_validation_including_32_days_and_untrusted_scope(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    endpoint=URL(org,loc)
    for first,last in (
        ("not-a-day","2030-01-02"),("2030-02-30","2030-03-01"),
        ("2030-01-03","2030-01-02"),("2030-01-01","2030-02-01"),
        ("9999-12-31","9999-12-31"),("2030-1-1","2030-01-01"),
    ):
        assert http.get(endpoint,params={"fromDay":first,
                                          "toDay":last}).status_code==422
    assert http.get(URL("not-a-uuid",loc),params={
        "fromDay":"2030-01-01","toDay":"2030-01-01",
    }).status_code==422
    assert http.get(endpoint).status_code==422
    assert http.get(endpoint,params={
        "fromDay":"2030-01-01","toDay":"2030-01-01",
    }).json()["summary"]["total"]==0
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE auto_service_location SET disabled_at=now() WHERE id=%s",
            (loc,),
        )
    assert http.get(endpoint,params={
        "fromDay":"2030-01-01","toDay":"2030-01-01",
    }).status_code==403


def test_funnel_berlin_dst_23_hour_day_counts_local_cohort_boundaries(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    zone=ZoneInfo("Europe/Berlin")
    # 2030-03-31 spring-forward: 23-hour local day from
    # March 30 23:00 UTC to March 31 22:00 UTC.
    instants=[
        datetime(2030,3,30,22,59,tzinfo=timezone.utc), # prior day
        datetime(2030,3,30,23,30,tzinfo=timezone.utc), # March 31
        datetime(2030,3,31,21,30,tzinfo=timezone.utc), # March 31
        datetime(2030,3,31,22,0,tzinfo=timezone.utc),  # April 1
    ]
    assert [dtime.astimezone(zone).date().isoformat() for dtime in instants]==[
        "2030-03-30","2030-03-31","2030-03-31","2030-04-01",
    ]
    with psycopg.connect(DSN) as db:
        for instant in instants:
            link=new_link(db,d,loc)
            db.execute(
                """INSERT INTO auto_crm_lead
                   (id,organization_id,location_id,customer_link_id,owner_id,
                    source_code,request_key,created_at)
                   VALUES(%s,%s,%s,%s,%s,'WEB',%s,%s)""",
                (uuid4(),org,loc,link,d["intake"],uuid4(),instant),
            )
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    url=URL(org,loc)
    def count(day):
        res=http.get(url,params={"fromDay":day,"toDay":day})
        assert res.status_code==200,res.text
        return res.json()["summary"]["total"]
    assert count("2030-03-30")==1
    assert count("2030-03-31")==2
    assert count("2030-04-01")==1
    total=http.get(url,params={"fromDay":"2030-03-30",
                               "toDay":"2030-04-01"}).json()
    assert total["summary"]["total"]==4
    assert total["summary"]["new"]==4



def test_crm_funnel_export_full_aggregate_and_revocation(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    zone=ZoneInfo("Europe/Berlin")
    day=datetime.now(zone).date().isoformat()
    query={"fromDay":day,"toDay":day}
    export=URL(org,loc)+"/export"
    assert http.get(export,params=query).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert http.get(export,params=query).status_code==403

    with psycopg.connect(DSN) as db:
        token=d["tokens"][d["intake"]]
        lead=create_lead(db,token,org,loc,d["link"],"WEB",uuid4())
        ident=UUID(lead["leadId"])
        transition_lead(db,token,org,loc,ident,uuid4(),"LOST","PRICE",None)

    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    response=http.get(export,params=query)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    assert response.headers["x-content-type-options"]=="nosniff"
    assert response.headers["content-disposition"].endswith(
        'filename="auto-crm-funnel.csv"')
    assert response.text.startswith("\ufeffИсточник;Всего;")
    rows=list(csv.reader(StringIO(response.text.removeprefix("\ufeff")),delimiter=";"))
    assert len(rows)==5 and all(len(row)==11 for row in rows)
    assert rows[0][0]=="Источник"
    assert rows[1]==["Все источники","1","0","0","0","1","0",
                      "1","0","0","0"]
    assert rows[2]==["Сайт","1","0","0","0","1","0",
                      "1","0","0","0"]
    assert rows[3][0]=="Звонок" and rows[3][1]=="0"
    assert rows[4][0]=="Личное обращение" and rows[4][1]=="0"
    for private in (str(d["ca"]),str(d["vehicle"]),str(ident),str(d["intake"])):
        assert private not in response.text

    assert http.get(URL(foreign,foreign_loc)+"/export",params=query).status_code==403
    assert http.get(export,params={"fromDay":day,"toDay":"invalid"}).status_code==422
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    after=http.get(export,params=query)
    assert after.status_code==200
    updated=list(csv.reader(StringIO(after.text.removeprefix("\ufeff")),delimiter=";"))
    assert updated[1][1]=="0"
    assert updated[2][1]=="0"
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                      WHERE organization_id=%s AND location_id=%s
                        AND user_id=%s AND role_code='MANAGER'""",
                   (org,loc,d["manager"]))
    assert http.get(export,params=query).status_code==403


def test_crm_funnel_csv_empty_cohort_keeps_zero_and_blank_conversion():
    from roboport_api.auto_crm_funnel import _blank,_finish
    report={"summary":_finish(_blank("ALL")),
            "items":[_finish(_blank(source)) for source in ("WEB","PHONE","WALK_IN")]}
    rows=list(csv.reader(StringIO(_funnel_csv(report).removeprefix("\ufeff")),delimiter=";"))
    assert len(rows)==5
    assert all(row[1]=="0" and row[6]=="" for row in rows[1:])
