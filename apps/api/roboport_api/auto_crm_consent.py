"""Synthetic-only service contact consent and inert CRM mock outbox.

This is not legal consent evidence and never sends a message or calls a provider.
"""
from uuid import UUID,uuid4
import hashlib
import psycopg
from .keyset_cursor import encode_cursor, decode_cursor

from .session_lookup import session_subject
from .auto_crm_leads import _authority,_lead,LeadDenied,LeadConflict

CHANNELS=("CALL","EMAIL","SMS")
TEMPLATES=("BOOKING_REMINDER","SERVICE_UPDATE","POST_SERVICE_REVIEW")
PURPOSE="SERVICE_UPDATE"


def _client(conn,token):
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None or not conn.execute("""
      SELECT 1 FROM user_role role WHERE role.user_id=%s AND role.role_code='CLIENT'
        AND role.revoked_at IS NULL AND (role.expires_at IS NULL OR role.expires_at>now())
      FOR SHARE OF role
    """,(actor,)).fetchone():
        raise LeadDenied("FORBIDDEN")
    return actor


def _account_users(conn,customer_id):
    rows=conn.execute("""
      SELECT acl.user_id FROM account_customer_link acl
      JOIN user_role role ON role.user_id=acl.user_id
      WHERE acl.customer_id=%s AND acl.revoked_at IS NULL
        AND role.role_code='CLIENT' AND role.revoked_at IS NULL
        AND (role.expires_at IS NULL OR role.expires_at>now())
      ORDER BY acl.user_id
      FOR SHARE OF acl,role
    """,(customer_id,)).fetchall()
    return list(dict.fromkeys(row[0] for row in rows))


def _snapshot(conn,customer_id):
    users=_account_users(conn,customer_id)
    flags={channel:False for channel in CHANNELS}
    event_ids={}
    if not users:
        return flags,event_ids
    for channel in CHANNELS:
        rows=conn.execute("""
          SELECT DISTINCT ON(user_id) id,user_id,enabled
          FROM auto_crm_contact_consent_event
          WHERE user_id=ANY(%s::uuid[]) AND channel_code=%s
            AND purpose_code=%s
          ORDER BY user_id,version DESC
        """,(users,channel,PURPOSE)).fetchall()
        latest={user:(ident,enabled) for ident,user,enabled in rows}
        flags[channel]=(len(latest)==len(users) and all(
            latest[user][1] is True for user in users))
        if flags[channel]:
            event_ids[channel]=[latest[user][0] for user in users]
    return flags,event_ids


def _customer_state(conn,actor):
    rows=conn.execute("""
      SELECT DISTINCT ON(channel_code) channel_code,enabled,version
      FROM auto_crm_contact_consent_event
      WHERE user_id=%s AND purpose_code=%s
      ORDER BY channel_code,version DESC
    """,(actor,PURPOSE)).fetchall()
    latest={channel:(enabled,version) for channel,enabled,version in rows}
    return {
      "syntheticOnly":True,"purposeCode":PURPOSE,
      "legalConsentVerified":False,"sendAllowed":False,
      "channels":{channel:latest.get(channel,(False,0))[0] for channel in CHANNELS},
      "revisions":{channel:latest.get(channel,(False,0))[1] for channel in CHANNELS},
    }


def read_customer_consent(conn,token):
    with conn.transaction():
        actor=_client(conn,token)
        return _customer_state(conn,actor)


def change_customer_consent(conn,token,channel,enabled,expected_revision,key):
    if (channel not in CHANNELS or type(enabled) is not bool or
        type(expected_revision) is not int or not 0<=expected_revision<2147483647 or
        type(key) is not UUID):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_client(conn,token)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,10102))",
                     (str(actor),))
        prior=conn.execute("""
          SELECT user_id,channel_code,purpose_code,enabled,version
          FROM auto_crm_contact_consent_event WHERE request_key=%s
        """,(key,)).fetchone()
        if prior:
            if prior!=(actor,channel,PURPOSE,enabled,expected_revision+1):
                raise LeadConflict("KEY_REUSED")
            return {"replayed":True,**_customer_state(conn,actor)}
        state=_customer_state(conn,actor)
        if expected_revision!=state["revisions"][channel]:
            raise LeadConflict("STALE_REVISION")
        try:
            conn.execute("""
              INSERT INTO auto_crm_contact_consent_event
                (id,user_id,channel_code,purpose_code,enabled,version,evidence_code,request_key)
              VALUES(%s,%s,%s,%s,%s,%s,'SYNTHETIC_SELF_SERVICE',%s)
            """,(uuid4(),actor,channel,PURPOSE,enabled,expected_revision+1,key))
        except psycopg.errors.UniqueViolation:
            # Блокировка пользователя не сериализует чужой ключ запроса.
            # При конкурирующей записи не раскрываем владельца ключа и
            # откатываем транзакцию через штатный конфликт.
            raise LeadConflict("KEY_REUSED") from None
        return {"replayed":False,**_customer_state(conn,actor)}


def _lead_customer(conn,lead):
    customer=conn.execute("""
      SELECT customer_id FROM auto_vehicle_customer_link WHERE id=%s
    """,(lead[1],)).fetchone()
    if customer is None:
        raise LeadDenied("FORBIDDEN")
    return customer[0]


def read_staff_consent(conn,token,org,loc,lead_id):
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        lead=_lead(conn,org,loc,lead_id,actor,manager)
        flags,_=_snapshot(conn,_lead_customer(conn,lead))
        return {"syntheticOnly":True,"purposeCode":PURPOSE,
                "legalConsentVerified":False,"sendAllowed":False,
                "channels":flags}


def create_mock_message(conn,token,org,loc,lead_id,key,channel,template):
    if (any(type(x) is not UUID for x in (org,loc,lead_id,key)) or
        channel not in CHANNELS or template not in TEMPLATES):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        conn.execute("""
          SELECT id FROM auto_crm_lead
          WHERE id=%s AND organization_id=%s AND location_id=%s FOR UPDATE
        """,(lead_id,org,loc))
        lead=_lead(conn,org,loc,lead_id,actor,manager)
        customer_id=_lead_customer(conn,lead)
        # Lock each active client account in a stable order. The DB trigger
        # repeats these locks and consent checks to protect direct SQL writes.
        users=_account_users(conn,customer_id)
        for user in users:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,10102))",
                         (str(user),))
        flags,event_ids=_snapshot(conn,customer_id)
        if not flags[channel]:
            raise LeadConflict("CONSENT_REQUIRED")
        consent_ids=event_ids[channel]
        existing=conn.execute("""
          SELECT lead_id,organization_id,location_id,channel_code,template_code,
                 consent_event_ids,actor_id
          FROM auto_crm_mock_outbox WHERE request_key=%s
        """,(key,)).fetchone()
        expected=(lead_id,org,loc,channel,template,consent_ids,actor)
        if existing:
            if existing!=expected:
                raise LeadConflict("KEY_REUSED_OR_CONSENT_CHANGED")
            row=conn.execute("""
              SELECT id,created_at FROM auto_crm_mock_outbox WHERE request_key=%s
            """,(key,)).fetchone()
            return {"replayed":True,"syntheticOnly":True,"sendAllowed":False,
                    "messageId":str(row[0]),"channelCode":channel,
                    "templateCode":template,"status":"MOCK_ONLY",
                    "createdAt":row[1].isoformat()}
        try:
            row=conn.execute("""
              INSERT INTO auto_crm_mock_outbox
                (id,lead_id,organization_id,location_id,channel_code,template_code,
                 consent_event_ids,actor_id,request_key)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
              RETURNING id,created_at
            """,(uuid4(),lead_id,org,loc,channel,template,consent_ids,actor,key)).fetchone()
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise LeadConflict("MOCK_OUTBOX_CONFLICT") from None
        return {"replayed":False,"syntheticOnly":True,"sendAllowed":False,
                "messageId":str(row[0]),"channelCode":channel,
                "templateCode":template,"status":"MOCK_ONLY",
                "createdAt":row[1].isoformat()}


def list_mock_messages(conn,token,org,loc,lead_id,cursor=None):
    if type(lead_id) is not UUID:
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        lead=_lead(conn,org,loc,lead_id,actor,manager)
        scope=hashlib.sha256(f"crm-mock:{org}:{loc}:{lead_id}".encode()).hexdigest()[:32]
        try:
            after=decode_cursor(cursor,scope,token) if cursor is not None else None
        except ValueError:
            raise LeadDenied("INVALID_INPUT") from None
        position=(after[0],after[0],after[1]) if after else (None,None,None)
        flags,event_ids=_snapshot(conn,_lead_customer(conn,lead))
        rows=conn.execute("""
          SELECT id,channel_code,template_code,consent_event_ids,created_at
          FROM auto_crm_mock_outbox
          WHERE lead_id=%s AND organization_id=%s AND location_id=%s
            AND (%s::timestamptz IS NULL OR (created_at,id)<(%s::timestamptz,%s::uuid))
          ORDER BY created_at DESC,id DESC LIMIT 101
        """,(lead_id,org,loc,*position)).fetchall()
        result={"syntheticOnly":True,"sendAllowed":False,
                "legalConsentVerified":False,"items":[{
                  "messageId":str(ident),"channelCode":channel,
                  "templateCode":template,
                  "status":"MOCK_ONLY" if flags[channel] and event_ids.get(channel)==snapshot else "SUPPRESSED",
                  "createdAt":stamp.isoformat(),
                } for ident,channel,template,snapshot,stamp in rows[:100]]}
        if len(rows)>100:
            result["nextCursor"]=encode_cursor(rows[99][4],rows[99][0],scope,token)
        return result
