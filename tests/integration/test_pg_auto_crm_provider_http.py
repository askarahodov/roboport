"""Full CRM provider rehearsal: phone → lead → booking, four-channel consent and bounded attempts."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_auto_crm_leads_http import _fixture,session
from test_pg_auto_crm_workflow_http import crm
from test_pg_baseline import DSN

CHANNELS=("CALL","EMAIL","SMS","MESSENGER")
CONSENT="/api/v1/dev/account/crm-provider-consent"


def test_call_ingress_dedup_consent_four_adapters_retry_and_revocation(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    root=crm(org,loc)
    staff=session(http,d,d["intake"])
    payload={"customerLinkId":str(d["link"]),"requestKey":str(uuid4()),"disposition":"MISSED"}
    endpoint=root+"/mock-calls"
    assert http.post(endpoint,json=payload).status_code==403
    created=http.post(endpoint,json=payload,headers=staff)
    assert created.status_code==201,created.text
    ident=created.json()["leadId"]
    assert created.json()["sourceCode"]=="PHONE"
    detail=root+"/leads/"+ident
    assert http.post(endpoint,json=payload,headers=staff).json()["replayed"] is True
    assert http.post(endpoint,json={**payload,"disposition":"ANSWERED"},headers=staff).status_code==409
    later=http.post(endpoint,json={**payload,"requestKey":str(uuid4()),"disposition":"ANSWERED"},headers=staff)
    assert later.status_code==201 and later.json()["leadId"]==ident
    history=http.get(detail+"/mock-calls")
    assert history.status_code==200 and history.json()["realCall"] is False
    assert [x["disposition"] for x in history.json()["items"]]==["ANSWERED","MISSED"]
    assert history.json()["nextCursor"] is None
    assert http.get(crm(foreign,foreign_loc)+"/leads/"+ident+"/mock-calls").status_code==403
    message_url=detail+"/provider-messages"
    def message(channel):
        return {"channelCode":channel,"templateCode":"SERVICE_UPDATE","requestKey":str(uuid4())}
    assert http.post(message_url,json=message("MESSENGER"),headers=staff).status_code==409
    client=session(http,d,d["client_a"])
    initial=http.get(CONSENT)
    assert initial.status_code==200 and initial.json()["channels"]==dict.fromkeys(CHANNELS,False)
    for channel in CHANNELS:
        got=http.post(CONSENT,headers=client,json={"channelCode":channel,"enabled":True,
            "expectedRevision":0,"requestKey":str(uuid4())})
        assert got.status_code==201,got.text
    assert "MESSENGER" not in http.get("/api/v1/dev/account/crm-contact-consent").json()["channels"]
    staff=session(http,d,d["intake"])
    assert http.get(detail+"/provider-consent").json()["channels"]==dict.fromkeys(CHANNELS,True)
    urls={}
    for channel in CHANNELS:
        body=message(channel)
        made=http.post(message_url,headers=staff,json=body)
        assert made.status_code==201,made.text
        assert made.json()["sendAllowed"] is False
        assert http.post(message_url,headers=staff,json=body).json()["messageId"]==made.json()["messageId"]
        path=message_url+"/"+made.json()["messageId"]+"/attempts"
        urls[channel]=path
        assert http.get(path).json()["status"]=="READY"
        key=str(uuid4())
        failed=http.post(path,headers=staff,json={"requestKey":key,"outcome":"TIMEOUT"})
        assert failed.status_code==201,failed.text
        assert failed.json()["status"]=="RETRY_AVAILABLE"
        assert failed.json()["providerCode"]=="FIXTURE_"+channel
        assert failed.json()["realDelivery"] is False
        replay=http.post(path,headers=staff,json={"requestKey":key,"outcome":"TIMEOUT"})
        assert replay.status_code==200 and replay.json()["attemptId"]==failed.json()["attemptId"]
        assert len(replay.json()["attempts"])==1
        assert http.post(path,headers=staff,json={"requestKey":key,"outcome":"ACCEPTED"}).status_code==409
    # Different fixture terminal outcomes, including three-failure exhaustion.
    for channel,sequence,expected in (("CALL",["ACCEPTED"],"MOCK_ACCEPTED"),
        ("EMAIL",["REJECTED"],"MOCK_REJECTED"),("SMS",["RATE_LIMIT","TIMEOUT"],"EXHAUSTED")):
        for outcome in sequence:
            response=http.post(urls[channel],headers=staff,json={"requestKey":str(uuid4()),"outcome":outcome})
            assert response.status_code==201,response.text
        assert response.json()["status"]==expected
        assert http.post(urls[channel],headers=staff,json={"requestKey":str(uuid4()),"outcome":"ACCEPTED"}).status_code==409
    client=session(http,d,d["client_a"])
    assert http.get(urls["MESSENGER"]).status_code==403
    assert http.post(CONSENT,headers=client,json={"channelCode":"MESSENGER","enabled":False,
        "expectedRevision":1,"requestKey":str(uuid4())}).status_code==201
    staff=session(http,d,d["intake"])
    assert http.get(urls["MESSENGER"]).json()["status"]=="SUPPRESSED"
    assert http.post(urls["MESSENGER"],headers=staff,json={"requestKey":str(uuid4()),"outcome":"ACCEPTED"}).status_code==409
    assert len(http.get(message_url).json()["items"])==4
    assert len(http.get(detail+"/mock-messages").json()["items"])==3
    client=session(http,d,d["client_a"])
    assert http.post(CONSENT,headers=client,json={"channelCode":"MESSENGER","enabled":True,
        "expectedRevision":2,"requestKey":str(uuid4())}).status_code==201
    staff=session(http,d,d["intake"])
    assert http.get(urls["MESSENGER"]).json()["status"]=="SUPPRESSED"
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_crm_mock_call WHERE lead_id=%s",(ident,)).fetchone()==(2,)
        message_id=UUID(urls["MESSENGER"].split("/")[-2])
        with pytest.raises(psycopg.errors.RaiseException),db.transaction():
            db.execute("UPDATE auto_crm_mock_attempt SET outcome='ACCEPTED' WHERE message_id=%s",(message_id,))
        with pytest.raises(psycopg.errors.RaiseException),db.transaction():
            db.execute("""INSERT INTO auto_crm_mock_attempt(id,message_id,actor_id,request_key,ordinal,outcome)
                       VALUES(%s,%s,%s,%s,2,'ACCEPTED')""",(uuid4(),message_id,d["intake"],uuid4()))
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert http.get(detail+"/mock-calls").status_code==403
    assert http.get(urls["CALL"]).status_code==403


def test_parallel_provider_event_and_attempt_exact_retry(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    root=crm(org,loc)
    event={"customerLinkId":str(d["link"]),"requestKey":str(uuid4()),"disposition":"ANSWERED"}
    def parallel(url,payload):
        barrier=Barrier(2)
        def post():
            with TestClient(app,base_url="https://127.0.0.1") as local:
                headers=session(local,d,d["intake"])
                barrier.wait(timeout=10)
                res=local.post(url,json=payload,headers=headers)
                return res.status_code,res.json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:post(),range(2)))
        assert sorted(code for code,_ in responses)==[200,201],responses
        return [body for _,body in responses]
    calls=parallel(root+"/mock-calls",event)
    assert calls[0]["leadId"]==calls[1]["leadId"]
    detail=root+"/leads/"+calls[0]["leadId"]
    client=session(http,d,d["client_a"])
    assert http.post(CONSENT,headers=client,json={"channelCode":"CALL","enabled":True,
        "expectedRevision":0,"requestKey":str(uuid4())}).status_code==201
    staff=session(http,d,d["intake"])
    made=http.post(detail+"/provider-messages",headers=staff,json={"channelCode":"CALL",
        "templateCode":"BOOKING_REMINDER","requestKey":str(uuid4())})
    assert made.status_code==201,made.text
    path=detail+"/provider-messages/"+made.json()["messageId"]+"/attempts"
    attempts=parallel(path,{"requestKey":str(uuid4()),"outcome":"TIMEOUT"})
    assert attempts[0]["attemptId"]==attempts[1]["attemptId"]
    assert len(http.get(path).json()["attempts"])==1
    assert len(http.get(detail+"/mock-calls").json()["items"])==1
