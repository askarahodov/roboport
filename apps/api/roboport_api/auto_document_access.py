"""Restrictive, live staff document ACL with append-only admin policies.

The underlying role/branch/session authorization is always mandatory. An
ALLOW only clears a previous rule at the same exact scope, never grants
authority missing from the underlying role or overrides another DENY.
"""
from datetime import datetime,timezone
from uuid import UUID,uuid4
import psycopg

from .auto_org_staff import _admin,OrgStaffDenied
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject

ROLES=("MANAGER","INTAKE","MECHANIC","WAREHOUSE")
DOCUMENTS=("INTAKE_ACT","ESTIMATE","HANDOVER","SUPPLIER_ORDER")
CAPABILITIES=("READ","WRITE","CLOSE","PRICE_SALES","PRICE_PURCHASE","DISCOUNT")


class DocumentPolicyDenied(ValueError):
    pass


class DocumentPolicyInvalid(ValueError):
    pass


class DocumentPolicyConflict(ValueError):
    pass


def _valid_policy(org,role,loc,user,document,cap,allowed,not_before,key=None):
    if (type(org) is not UUID or role not in ROLES or
        (loc is not None and type(loc) is not UUID) or
        (user is not None and type(user) is not UUID) or
        document not in DOCUMENTS or cap not in CAPABILITIES or
        type(allowed) is not bool or
        (key is not None and type(key) is not UUID) or
        (not_before is not None and
         (type(not_before) is not datetime or
          not_before.tzinfo is None or not_before.utcoffset() is None)) or
        (not_before is not None and (cap!="READ" or not allowed))):
        raise DocumentPolicyInvalid("INVALID_INPUT")


def publish_policy(conn,token,org,*,request_key,role,loc,user,
                   document,cap,allowed,not_before):
    _valid_policy(org,role,loc,user,document,cap,allowed,not_before,request_key)
    with conn.transaction():
        try:
            actor=_admin(conn,token,org)
        except OrgStaffDenied:
            raise DocumentPolicyDenied("FORBIDDEN") from None
        # Hold exclusive organization lock until commit; all scoped readers
        # hold a shared lock. No in-flight forbidden request can race through
        # after an ADMIN restriction commits.
        conn.execute("""SELECT id FROM auto_organization
                        WHERE id=%s AND disabled_at IS NULL FOR UPDATE""",(org,))
        existing=conn.execute("""
            SELECT organization_id,location_id,role_code,user_id,
                   document_kind,capability,allowed,not_before,actor_id
            FROM auto_document_access_policy WHERE request_key=%s
        """,(request_key,)).fetchone()
        expected=(org,loc,role,user,document,cap,allowed,not_before,actor)
        if existing:
            if existing!=expected:
                raise DocumentPolicyConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,"items":_audit(conn,org)}
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.policy_actor',%s,true)",
                             (str(actor),))
                conn.execute("""
                  INSERT INTO auto_document_access_policy(
                    id,request_key,organization_id,location_id,role_code,user_id,
                    document_kind,capability,allowed,not_before,actor_id)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,(uuid4(),request_key,org,loc,role,user,
                      document,cap,allowed,not_before,actor))
                conn.execute("SELECT set_config('roboport.policy_actor','',true)")
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise DocumentPolicyConflict("POLICY_CONFLICT") from None
        return {"ok":True,"replayed":False,"items":_audit(conn,org)}


def _audit(conn,org,limit=100):
    rows=conn.execute("""
       SELECT id,role_code,user_id,location_id,document_kind,capability,
              allowed,not_before,actor_id,created_at
       FROM auto_document_access_policy WHERE organization_id=%s
       ORDER BY created_at DESC,id DESC LIMIT %s
    """,(org,limit)).fetchall()
    return [{"eventId":str(i),"role":role,"userId":str(user) if user else None,
             "locationId":str(loc) if loc else None,"documentKind":doc,
             "capability":cap,"allowed":allow,
             "notBefore":cutoff.isoformat() if cutoff else None,
             "actorId":str(actor),"createdAt":created.isoformat()}
            for i,role,user,loc,doc,cap,allow,cutoff,actor,created in rows]


def list_policies(conn,token,org,*,limit=100):
    if type(org) is not UUID or type(limit) is not int or not 1<=limit<=100:
        raise DocumentPolicyInvalid("INVALID_INPUT")
    with conn.transaction():
        try:
            _admin(conn,token,org)
        except OrgStaffDenied:
            raise DocumentPolicyDenied("FORBIDDEN") from None
        return {"syntheticOnly":True,"items":_audit(conn,org,limit)}


def require_document_access(conn,token,org,loc,roles,document,cap,*,order=None):
    """Check live staff authorization and all deny/cutoff rules atomically.

    Caller holds a transaction. Existing role/proof/object checks remain
    authoritative, so a policy ALLOW can never broaden any of them.
    Returns the maximum history cutoff for branch list queries.
    """
    if (type(org) is not UUID or type(loc) is not UUID or
        type(token) is not str or not token or
        type(roles) not in (tuple,list) or not roles or
        any(role not in ROLES for role in roles) or
        document not in DOCUMENTS or cap not in CAPABILITIES or
        (order is not None and type(order) is not UUID)):
        raise DocumentPolicyDenied("FORBIDDEN")
    active=[role for role in set(roles) if organization_role_allows(
        conn,token,org,role,loc,for_mutation=True)]
    if not active:
        raise DocumentPolicyDenied("FORBIDDEN")
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None:
        raise DocumentPolicyDenied("FORBIDDEN")
    # ADMIN publication must wait on these shared locks. Do not read only
    # existing rules: a concurrent insert would otherwise escape our scope.
    row=conn.execute("""
      SELECT id FROM auto_organization WHERE id=%s AND disabled_at IS NULL
      FOR SHARE
    """,(org,)).fetchone()
    if row is None:
        raise DocumentPolicyDenied("FORBIDDEN")
    cutoffs=[]
    for role in active:
        rules=conn.execute("""
          SELECT location_id,user_id,allowed,not_before
          FROM auto_document_access_policy
          WHERE organization_id=%s AND role_code=%s AND document_kind=%s
            AND capability=%s
            AND (location_id IS NULL OR location_id=%s)
            AND (user_id IS NULL OR user_id=%s)
          ORDER BY created_at DESC,id DESC LIMIT 201
          FOR SHARE
        """,(org,role,document,cap,loc,actor)).fetchall()
        if len(rules)>200:
            raise DocumentPolicyDenied("ACL_LIMIT")
        latest={}
        for location,user,allowed,not_before in rules:
            scope=(location,user)
            if scope not in latest:
                latest[scope]=(allowed,not_before)
        if any(not allowed for allowed,_ in latest.values()):
            raise DocumentPolicyDenied("POLICY_DENIED")
        cutoffs.extend(cutoff for allowed,cutoff in latest.values()
                       if allowed and cutoff is not None)
    cutoff=max(cutoffs) if cutoffs else None
    if order is not None and cutoff is not None:
        item=conn.execute("""
          SELECT created_at FROM auto_work_order
          WHERE id=%s AND organization_id=%s AND location_id=%s
          FOR SHARE
        """,(order,org,loc)).fetchone()
        if item is None or item[0]<cutoff:
            raise DocumentPolicyDenied("HISTORY_DENIED")
    return cutoff


def has_document_access(conn,token,org,loc,roles,document,cap,*,order=None):
    try:
        require_document_access(conn,token,org,loc,roles,document,cap,
                                order=order)
        return True
    except DocumentPolicyDenied:
        return False
