"""Immutable branch tariff proposals, independent approvals and quote snapshots."""
from uuid import UUID,uuid4
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_document_access import require_document_access,DocumentPolicyDenied


class TariffDenied(ValueError):
    pass


class TariffConflict(ValueError):
    pass


CATEGORIES=("GENERAL","DIAGNOSTIC","MASTER")


def _actor(conn,token,org,loc):
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None or not organization_role_allows(
        conn,token,org,"MANAGER",loc,for_mutation=True):
        raise TariffDenied("FORBIDDEN")
    return actor


def _tariff_policy(conn,token,org,loc,*,write=False,discount=False):
    # Tariff pages return ALL past rates, so an ESTIMATE history cutoff must
    # deny their complete-history endpoint rather than disclose older prices.
    try:
        for cap in ("READ","PRICE_SALES",*(
                ("EDIT",) if write else ()),
                *(("DISCOUNT",) if discount else ())):
            cutoff=require_document_access(conn,token,org,loc,("MANAGER",),
                                           "ESTIMATE",cap)
            if cutoff is not None:
                raise TariffDenied("HISTORY_DENIED")
    except DocumentPolicyDenied:
        raise TariffDenied("FORBIDDEN") from None


def _org_policy_lock(conn,org):
    # Always lock the organization before branch UPDATE. Policy publisher
    # takes organization UPDATE first and must wait for this operation; the
    # branch mutex is exclusive BEFORE role/ACL checks acquire branch SHARE.
    row=conn.execute("""SELECT id FROM auto_organization
      WHERE id=%s AND disabled_at IS NULL FOR SHARE""",(org,)).fetchone()
    if row is None:
        raise TariffDenied("FORBIDDEN")


def _branch_lock(conn,org,loc,lock="FOR UPDATE"):
    row=conn.execute(f"""SELECT id FROM auto_service_location
      WHERE id=%s AND organization_id=%s AND disabled_at IS NULL {lock}""",
      (loc,org)).fetchone()
    if row is None:
        raise TariffDenied("FORBIDDEN")


def _state(conn,org,loc):
    rows=conn.execute("""
      SELECT p.id,p.qualification_code,p.version,p.rate_minor_per_hour,
             p.max_discount_bps,p.actor_id,p.created_at,
             a.approved_by,a.created_at
      FROM auto_work_tariff_revision p
      LEFT JOIN auto_work_tariff_approval a ON a.revision_id=p.id
      WHERE p.organization_id=%s AND p.location_id=%s
      ORDER BY p.qualification_code,p.version LIMIT 91
    """,(org,loc)).fetchall()
    if len(rows)>90:
        raise TariffConflict("LIMIT")
    items=[{"id":str(r[0]),"qualificationCode":r[1],"version":r[2],
            "rateMinorPerHour":r[3],"maxDiscountBps":r[4],
            "proposedBy":str(r[5]),"createdAt":r[6].isoformat(),
            "approvedBy":str(r[7]) if r[7] else None,
            "approvedAt":r[8].isoformat() if r[8] else None}
           for r in rows]
    active={}
    for p in items:
        if p["approvedBy"]:
            active[p["qualificationCode"]]=p
    return {"items":items,"active":active,"syntheticOnly":True}


def read_tariffs(conn,token,org,loc):
    with conn.transaction():
        _actor(conn,token,org,loc)
        _tariff_policy(conn,token,org,loc)
        return _state(conn,org,loc)


def propose_tariff(conn,token,org,loc,*,request_key,expected_version,
                   qualification_code,rate_minor_per_hour,max_discount_bps):
    if (any(type(x) is not UUID for x in (org,loc,request_key))
        or type(expected_version) is not int or not 0<=expected_version<30
        or qualification_code not in CATEGORIES
        or type(rate_minor_per_hour) is not int
        or not 0<=rate_minor_per_hour<=10**10
        or type(max_discount_bps) is not int
        or not 0<=max_discount_bps<=2500):
        raise TariffDenied("INVALID_INPUT")
    with conn.transaction():
        # Acquire the branch mutex before role SHARE locks and approval writes.
        _org_policy_lock(conn,org)
        _branch_lock(conn,org,loc)
        actor=_actor(conn,token,org,loc)
        _tariff_policy(conn,token,org,loc,write=True,discount=max_discount_bps>0)
        prior=conn.execute("""
          SELECT organization_id,location_id,actor_id,qualification_code,version,
            rate_minor_per_hour,max_discount_bps
          FROM auto_work_tariff_revision WHERE request_key=%s
        """,(request_key,)).fetchone()
        if prior:
            if prior!=(org,loc,actor,qualification_code,expected_version+1,
                       rate_minor_per_hour,max_discount_bps):
                raise TariffConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**_state(conn,org,loc)}
        rows=_state(conn,org,loc)["items"]
        matching=[p for p in rows if p["qualificationCode"]==qualification_code]
        if (len(matching)!=expected_version or
            (matching and not matching[-1]["approvedBy"])):
            raise TariffConflict("VERSION_CONFLICT")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_tariff_revision
                    (id,organization_id,location_id,qualification_code,version,
                     rate_minor_per_hour,max_discount_bps,actor_id,request_key)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,(uuid4(),org,loc,qualification_code,expected_version+1,
                    rate_minor_per_hour,max_discount_bps,actor,request_key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise TariffConflict("VERSION_CONFLICT") from None
        return {"ok":True,"replayed":False,**_state(conn,org,loc)}


def approve_tariff(conn,token,org,loc,policy_id,request_key):
    if any(type(v) is not UUID for v in (org,loc,policy_id,request_key)):
        raise TariffDenied("INVALID_INPUT")
    with conn.transaction():
        _org_policy_lock(conn,org)
        _branch_lock(conn,org,loc)
        actor=_actor(conn,token,org,loc)
        _tariff_policy(conn,token,org,loc,write=True,discount=True)
        row=conn.execute("""
          SELECT p.actor_id,p.qualification_code,p.version,
                 a.approved_by,a.request_key
          FROM auto_work_tariff_revision p
          LEFT JOIN auto_work_tariff_approval a ON a.revision_id=p.id
          WHERE p.id=%s AND p.organization_id=%s AND p.location_id=%s
        """,(policy_id,org,loc)).fetchone()
        if row is None:
            raise TariffDenied("FORBIDDEN")
        proposer,category,version,prior_actor,prior_key=row
        if prior_actor is not None:
            if (prior_actor,prior_key)!=(actor,request_key):
                raise TariffConflict("ALREADY_APPROVED")
            return {"ok":True,"replayed":True,**_state(conn,org,loc)}
        if actor==proposer:
            raise TariffDenied("SEPARATION_REQUIRED")
        if conn.execute("""
          SELECT 1 FROM auto_work_tariff_revision p
          WHERE p.location_id=%s AND p.qualification_code=%s AND p.version>%s
        """,(loc,category,version)).fetchone():
            raise TariffConflict("STALE_VERSION")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_tariff_approval
                    (id,revision_id,approved_by,request_key)
                  VALUES(%s,%s,%s,%s)
                """,(uuid4(),policy_id,actor,request_key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation):
            raise TariffConflict("ALREADY_APPROVED") from None
        return {"ok":True,"replayed":False,**_state(conn,org,loc)}


def quote_tariff_snapshot(conn,org,loc,lines):
    """Use exactly the latest independently approved rate per qualification.

    Returns a per-service immutable policy snapshot to include in the quote
    fingerprint. A branch without approved tariffs keeps its legacy dev mode.
    """
    _branch_lock(conn,org,loc,lock="FOR SHARE")
    rows=conn.execute("""
      SELECT p.id,p.version,p.qualification_code,p.rate_minor_per_hour,
             p.max_discount_bps
      FROM auto_work_tariff_revision p
      JOIN auto_work_tariff_approval a ON a.revision_id=p.id
      WHERE p.organization_id=%s AND p.location_id=%s
      ORDER BY p.version DESC FOR SHARE OF p,a
    """,(org,loc)).fetchall()
    policies={}
    for ident,version,category,rate,maximum in rows:
        if category not in policies:
            policies[category]=(ident,version,rate,maximum)
    if not policies:
        return []
    result=[]
    for line in lines:
        category=line.get("qualificationCode")
        approved=policies.get(category)
        if (line.get("pricingMethod")!="NORM_HOUR_FACTORED" or
            approved is None or line.get("rateMinorPerHour")!=approved[2]
            or line.get("discountBps",2501)>approved[3]):
            raise TariffConflict("TARIFF_MISMATCH")
        ident,version,rate,maximum=approved
        result.append({"serviceId":line["serviceId"],"policyId":str(ident),
                       "version":version,"qualificationCode":category,
                       "rateMinorPerHour":rate,"maxDiscountBps":maximum})
    return result
