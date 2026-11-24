"""Four offline provider adapters and inbound phone-event reconciliation.

No network implementation, credentials, destinations or text; accepted is a
fixture receipt only. Replays retain identity; revoked consent blocks retries.
"""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL

import psycopg

from .auto_crm_leads import _authority,_lead,_proof,_state,create_lead,LeadDenied,LeadConflict
from .auto_crm_consent import _account_users,_snapshot,_lead_customer,CHANNELS


OUTCOMES=("ACCEPTED","TIMEOUT","RATE_LIMIT","REJECTED")


@dataclass(frozen=True)
class FixtureReceipt:
    outcome:str
    retryable:bool
    provider_code:str


class ProviderAdapter(Protocol):
    def simulate(self, outcome:str)->FixtureReceipt: ...


@dataclass(frozen=True)
class OfflineProvider:
    channel:str

    def simulate(self,outcome:str)->FixtureReceipt:
        if self.channel not in CHANNELS or outcome not in OUTCOMES:
            raise LeadDenied("INVALID_INPUT")
        return FixtureReceipt(outcome,outcome in ("TIMEOUT","RATE_LIMIT"),"FIXTURE_"+self.channel)


PROVIDERS={channel:OfflineProvider(channel) for channel in CHANNELS}


def _locked_lead(conn,token,org,loc,lead):
    actor,manager=_authority(conn,token,org,loc)
    conn.execute("SELECT id FROM auto_crm_lead WHERE id=%s AND organization_id=%s AND location_id=%s FOR UPDATE",
                 (lead,org,loc))
    return actor,_lead(conn,org,loc,lead,actor,manager)


def _message(conn,lead,org,loc,message):
    row=conn.execute("""
      SELECT channel_code,template_code,consent_event_ids FROM auto_crm_mock_outbox
      WHERE id=%s AND lead_id=%s AND organization_id=%s AND location_id=%s
    """,(message,lead,org,loc)).fetchone()
    if row is None:
        raise LeadDenied("FORBIDDEN")
    return row


def _attempts(conn,message,channel,active):
    rows=conn.execute("""
      SELECT id,ordinal,outcome,created_at FROM auto_crm_mock_attempt
      WHERE message_id=%s ORDER BY ordinal
    """,(message,)).fetchall()
    latest=rows[-1][2] if rows else None
    status=("SUPPRESSED" if not active else
            "MOCK_ACCEPTED" if latest=="ACCEPTED" else
            "MOCK_REJECTED" if latest=="REJECTED" else
            "EXHAUSTED" if len(rows)==3 else
            "RETRY_AVAILABLE" if rows else "READY")
    return {"syntheticOnly":True,"sendAllowed":False,"realDelivery":False,
            "messageId":str(message),"channelCode":channel,
            "providerCode":"FIXTURE_"+channel,"status":status,
            "attempts":[{"id":str(i),"ordinal":n,"outcome":outcome,"createdAt":stamp.isoformat()}
                        for i,n,outcome,stamp in rows]}


def provider_attempt(conn,token,org,loc,lead,message,*,key=None,outcome=None):
    with conn.transaction():
        actor,row=_locked_lead(conn,token,org,loc,lead)
        channel,_,snapshot=_message(conn,lead,org,loc,message)
        customer=_lead_customer(conn,row)
        for user in _account_users(conn,customer):
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,10102))",(str(user),))
        flags,event_ids=_snapshot(conn,customer)
        active=flags[channel] and event_ids.get(channel)==snapshot
        state=_attempts(conn,message,channel,active)
        if key is None:
            return state
        if type(key) is not UUID or outcome not in OUTCOMES:
            raise LeadDenied("INVALID_INPUT")
        if not active:
            raise LeadConflict("CONSENT_CHANGED")
        existing=conn.execute("SELECT message_id,actor_id,outcome FROM auto_crm_mock_attempt WHERE request_key=%s",(key,)).fetchone()
        if existing:
            if existing!=(message,actor,outcome):
                raise LeadConflict("KEY_REUSED")
            return {**state,"replayed":True}
        if state["status"] not in ("READY","RETRY_AVAILABLE"):
            raise LeadConflict("TERMINAL")
        result=PROVIDERS[channel].simulate(outcome)
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_crm_mock_attempt(id,message_id,actor_id,request_key,ordinal,outcome)
                  VALUES(%s,%s,%s,%s,%s,%s)
                """,(uuid4(),message,actor,key,len(state["attempts"])+1,result.outcome))
        except (psycopg.errors.IntegrityError,psycopg.errors.RaiseException):
            raise LeadConflict("ATTEMPT_CONFLICT") from None
        return {**_attempts(conn,message,channel,True),"replayed":False}


def ingest_call(conn,token,org,loc,link,event,disposition):
    if any(type(x) is not UUID for x in (link,event)) or disposition not in ("ANSWERED","MISSED"):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        if not _proof(conn,org,loc,link):
            raise LeadDenied("FORBIDDEN")
        # Provider event identity is global; link dedup is scoped to branch/PHONE.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,14001))",(str(event),))
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,14002))",(f"{org}:{loc}:{link}",))
        prior=conn.execute("""
          SELECT c.lead_id,c.actor_id,c.disposition,l.organization_id,l.location_id,l.customer_link_id
          FROM auto_crm_mock_call c JOIN auto_crm_lead l ON l.id=c.lead_id WHERE c.provider_event_id=%s
        """,(event,)).fetchone()
        if prior:
            if prior[1:]!=(actor,disposition,org,loc,link):
                raise LeadConflict("EVENT_REUSED")
            lead=_lead(conn,org,loc,prior[0],actor,manager)
            return {**_state(conn,lead),"replayed":True}
        lead=conn.execute("""
          SELECT id FROM auto_crm_lead WHERE organization_id=%s AND location_id=%s
            AND customer_link_id=%s AND source_code='PHONE'
        """,(org,loc,link)).fetchone()
        if lead is None:
            created=create_lead(conn,token,org,loc,link,"PHONE",
                                uuid5(NAMESPACE_URL,f"roboport:mock-call:{event}"))
            ident=UUID(created["leadId"])
        else:
            ident=lead[0]
        _,row=_locked_lead(conn,token,org,loc,ident)
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_crm_mock_call(id,provider_event_id,lead_id,actor_id,disposition)
                  VALUES(%s,%s,%s,%s,%s)
                """,(uuid4(),event,ident,actor,disposition))
        except (psycopg.errors.IntegrityError,psycopg.errors.RaiseException):
            raise LeadConflict("CALL_CONFLICT") from None
        return {**_state(conn,row),"replayed":False}


def call_history(conn,token,org,loc,lead):
    with conn.transaction():
        _,row=_locked_lead(conn,token,org,loc,lead)
        if row[3]!="PHONE":
            raise LeadDenied("FORBIDDEN")
        rows=conn.execute("""
          SELECT id,disposition,created_at FROM auto_crm_mock_call
          WHERE lead_id=%s ORDER BY created_at DESC,id DESC LIMIT 101
        """,(lead,)).fetchall()
        if len(rows)>100:
            raise LeadConflict("CALL_HISTORY_LIMIT")
        return {"syntheticOnly":True,"realCall":False,
                "items":[{"id":str(i),"disposition":code,"createdAt":stamp.isoformat()} for i,code,stamp in rows]}
