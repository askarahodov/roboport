"""Synthetic CRM contact-channel choices.

Client can express/withdraw an account-wide *preference* even after vehicle
proof revocation. This is NOT legal marketing consent and NEVER enables
messaging. Staff see only aggregate booleans for currently authorized leads.
"""
from uuid import UUID,uuid4

from .session_lookup import session_subject
from .auto_crm_leads import _authority,_lead,LeadDenied,LeadConflict

CHANNELS=("CALL","EMAIL","SMS")


def _client(conn,token):
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None or not conn.execute(
        """SELECT 1 FROM user_role r WHERE r.user_id=%s
           AND r.role_code='CLIENT' AND r.revoked_at IS NULL
           AND (r.expires_at IS NULL OR r.expires_at>now())
           FOR SHARE OF r""",(actor,),
    ).fetchone():
        raise LeadDenied("FORBIDDEN")
    return actor


def _current(conn,actor):
    rows=conn.execute(
        """SELECT DISTINCT ON (channel_code) channel_code,enabled,version
           FROM auto_crm_contact_choice_event WHERE user_id=%s
           ORDER BY channel_code,version DESC""",(actor,),
    ).fetchall()
    latest={code:(enabled,version) for code,enabled,version in rows}
    return {"syntheticOnly":True,"sendAllowed":False,
            "channels":{channel:latest.get(channel,(False,0))[0]
                        for channel in CHANNELS},
            "revisions":{channel:latest.get(channel,(False,0))[1]
                         for channel in CHANNELS}}


def read_customer_choices(conn,token):
    with conn.transaction():
        actor=_client(conn,token)
        return _current(conn,actor)


def change_customer_choice(conn,token,channel,enabled,expected_revision,key):
    if (channel not in CHANNELS or type(enabled) is not bool
            or type(expected_revision) is not int
            or not 0<=expected_revision<2147483647
            or type(key) is not UUID):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_client(conn,token)
        # Serialize preference changes and exact-key retries across requests.
        # Revoke of CLIENT role is serialized through the shared role lock.
        conn.execute("SELECT id FROM app_user WHERE id=%s FOR UPDATE",(actor,))
        prior=conn.execute(
            """SELECT user_id,channel_code,enabled,version
               FROM auto_crm_contact_choice_event WHERE request_key=%s""",(key,),
        ).fetchone()
        if prior:
            if prior!=(actor,channel,enabled,expected_revision+1):
                raise LeadConflict("KEY_REUSED")
            return {"replayed":True,**_current(conn,actor)}
        state=_current(conn,actor)
        if expected_revision!=state["revisions"][channel]:
            raise LeadConflict("STALE_REVISION")
        conn.execute(
            """INSERT INTO auto_crm_contact_choice_event
               (id,user_id,channel_code,enabled,version,request_key)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (uuid4(),actor,channel,enabled,expected_revision+1,key),
        )
        return {"replayed":False,**_current(conn,actor)}


def read_staff_choices(conn,token,org,loc,lead_id):
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        lead=_lead(conn,org,loc,lead_id,actor,manager)
        customer=conn.execute(
            "SELECT customer_id FROM auto_vehicle_customer_link WHERE id=%s",
            (lead[1],),
        ).fetchone()
        if customer is None:
            raise LeadDenied("FORBIDDEN")
        # Revoked account links and inactive CLIENT roles are excluded.
        # With multiple active accounts for the same customer, *every*
        # account must explicitly opt into a channel before it appears true.
        rows=conn.execute(
            """SELECT acl.user_id FROM account_customer_link acl
               JOIN user_role role ON role.user_id=acl.user_id
               WHERE acl.customer_id=%s AND acl.revoked_at IS NULL
                 AND role.role_code='CLIENT' AND role.revoked_at IS NULL
                 AND (role.expires_at IS NULL OR role.expires_at>now())
               FOR SHARE OF acl,role""",(customer[0],),
        ).fetchall()
        users=list(dict.fromkeys(row[0] for row in rows))
        if not users:
            return {"syntheticOnly":True,"sendAllowed":False,
                    "channels":{code:False for code in CHANNELS}}
        audit=conn.execute(
            """SELECT DISTINCT ON (user_id,channel_code)
                      user_id,channel_code,enabled
               FROM auto_crm_contact_choice_event
               WHERE user_id=ANY(%s::uuid[])
               ORDER BY user_id,channel_code,version DESC""",(users,),
        ).fetchall()
        latest={(user,code):enabled for user,code,enabled in audit}
        return {"syntheticOnly":True,"sendAllowed":False,
                "channels":{code:all(latest.get((user,code),False) for user in users)
                            for code in CHANNELS}}
