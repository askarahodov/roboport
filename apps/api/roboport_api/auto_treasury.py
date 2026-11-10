"""Separate synthetic branch treasury: not financial posting or legal payment.

Managers create test accounts and inspect reconciliations. Only active CASHIER
grants can write movement and seal a business day. Dev-only and no callbacks.
"""
import re
from datetime import date,timedelta
from uuid import UUID,uuid4
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_workshop_qc import _actor_and_order,InspectionDenied
from .session_lookup import session_subject
from .auto_document_access import require_document_access,DocumentPolicyDenied


class TreasuryDenied(ValueError):
    pass


class TreasuryInvalid(ValueError):
    pass


class TreasuryConflict(ValueError):
    pass


KINDS=("ADVANCE","PAYMENT","APPLY_ADVANCE","REFUND_ADVANCE","REFUND_PAYMENT")
CREDIT=("ADVANCE","PAYMENT")
DEBIT=("REFUND_ADVANCE","REFUND_PAYMENT")


def _valid_uuids(*values):
    if any(type(v) is not UUID for v in values):
        raise TreasuryInvalid("INVALID_INPUT")


def _finance_scope(conn,token,org,loc,cap,*,order=None):
    # A dual MANAGER/CASHIER cannot evade a restrictive policy by switching
    # the operational role. Both active scopes are checked on every read/write.
    try:
        for action in dict.fromkeys(("READ","PRICE_SALES",cap)):
            cutoff=require_document_access(
                conn,token,org,loc,("MANAGER","CASHIER"),
                "FINANCE",action,order=order)
            # Account reports/closings expose aggregate balances from all
            # prior days. With no exact WorkOrder to check against a cutoff,
            # fail closed instead of leaking historical entries.
            if order is None and cutoff is not None:
                raise TreasuryDenied("HISTORY_DENIED")
    except DocumentPolicyDenied:
        raise TreasuryDenied("FORBIDDEN") from None


def _staff(conn,token,org,loc,roles,*,cap="READ"):
    _valid_uuids(org,loc)
    if type(token) is not str or not token:
        raise TreasuryDenied("FORBIDDEN")
    for role in roles:
        if organization_role_allows(conn,token,org,role,loc,for_mutation=True):
            actor=session_subject(conn,token,for_mutation=True)
            if actor is not None:
                _finance_scope(conn,token,org,loc,cap)
                return actor
    raise TreasuryDenied("FORBIDDEN")


def _order(conn,token,org,loc,order,roles,*,cap="READ"):
    _valid_uuids(org,loc,order)
    try:
        # Lock the ACL organization before locking the WorkOrder identity.
        _finance_scope(conn,token,org,loc,cap,order=order)
        actor=_actor_and_order(conn,token,org,loc,order,roles)
        return actor
    except InspectionDenied:
        raise TreasuryDenied("FORBIDDEN") from None


def _account(conn,org,loc,account,*,lock=False):
    _valid_uuids(org,loc,account)
    row=conn.execute("""
        SELECT id,code,label,kind,created_at
        FROM auto_treasury_account
        WHERE id=%s AND organization_id=%s AND location_id=%s
    """+(" FOR UPDATE" if lock else " FOR SHARE"),(account,org,loc)).fetchone()
    if row is None:
        raise TreasuryDenied("FORBIDDEN")
    return row


def _amounts(conn,*,order=None,account=None):
    if order is None and account is None:
        raise TreasuryInvalid("INVALID_INPUT")
    parts=[]
    args=[]
    if order is not None:
        parts.append("work_order_id=%s")
        args.append(order)
    if account is not None:
        parts.append("account_id=%s")
        args.append(account)
    row=conn.execute("""
       SELECT COALESCE(sum(amount_minor) FILTER (WHERE kind='ADVANCE'),0)::bigint,
              COALESCE(sum(amount_minor) FILTER (WHERE kind='PAYMENT'),0)::bigint,
              COALESCE(sum(amount_minor) FILTER (WHERE kind='APPLY_ADVANCE'),0)::bigint,
              COALESCE(sum(amount_minor) FILTER (WHERE kind='REFUND_ADVANCE'),0)::bigint,
              COALESCE(sum(amount_minor) FILTER (WHERE kind='REFUND_PAYMENT'),0)::bigint
       FROM auto_treasury_event WHERE """+" AND ".join(parts),tuple(args)).fetchone()
    advanced,paid,applied,adv_refunded,pay_refunded=row
    return {"advancedMinor":advanced,"paymentMinor":paid,
            "appliedMinor":applied,"refundedAdvanceMinor":adv_refunded,
            "refundedPaymentMinor":pay_refunded,
            "unappliedAdvanceMinor":advanced-applied-adv_refunded,
            "settledMinor":paid+applied-pay_refunded,
            "cashNetMinor":advanced+paid-adv_refunded-pay_refunded}


def create_account(conn,token,org,loc,*,request_key,code,label,kind):
    _valid_uuids(org,loc,request_key)
    if (type(code) is not str or not re.fullmatch(r"[A-Z0-9_-]{2,32}",code)
        or type(label) is not str or not 3<=len(label.strip())<=80
        or kind not in ("CASH_SIM","BANK_SIM")):
        raise TreasuryInvalid("INVALID_INPUT")
    with conn.transaction():
        actor=_staff(conn,token,org,loc,("MANAGER",),cap="CREATE")
        row=conn.execute("""SELECT id,organization_id,location_id,code,label,kind,actor_id
           FROM auto_treasury_account WHERE request_key=%s""",(request_key,)).fetchone()
        if row is not None:
            if row[1:]!=(org,loc,code,label,kind,actor):
                raise TreasuryConflict("KEY_REUSED")
            return {"accountId":str(row[0]),"replayed":True,"syntheticOnly":True}
        try:
            with conn.transaction():
                ident=uuid4()
                conn.execute("""INSERT INTO auto_treasury_account
                    (id,organization_id,location_id,code,label,kind,actor_id,request_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (ident,org,loc,code,label,kind,actor,request_key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise TreasuryConflict("ACCOUNT_CONFLICT") from None
        return {"accountId":str(ident),"replayed":False,"syntheticOnly":True}


def list_accounts(conn,token,org,loc):
    with conn.transaction():
        _staff(conn,token,org,loc,("MANAGER","CASHIER"))
        rows=conn.execute("""
          SELECT a.id,a.code,a.label,a.kind,a.currency,a.created_at,
            COALESCE(sum(CASE WHEN e.kind IN ('ADVANCE','PAYMENT') THEN e.amount_minor
                    WHEN e.kind IN ('REFUND_ADVANCE','REFUND_PAYMENT')
                    THEN -e.amount_minor ELSE 0 END),0)::bigint,
            (SELECT max(c.business_day) FROM auto_treasury_day_close c
                WHERE c.account_id=a.id)
          FROM auto_treasury_account a LEFT JOIN auto_treasury_event e
             ON e.account_id=a.id
          WHERE a.organization_id=%s AND a.location_id=%s
          GROUP BY a.id ORDER BY a.code,a.id LIMIT 51
        """,(org,loc)).fetchall()
        if len(rows)>50:
            raise TreasuryConflict("ACCOUNT_LIMIT")
        return {"syntheticOnly":True,"realMoney":False,"currency":"RUB",
            "items":[{"id":str(ident),"code":code,"label":label,"kind":kind,
                 "currency":currency,"createdAt":created.isoformat(),
                 "balanceMinor":balance,
                 "lastClosedDay":last.isoformat() if last else None}
                 for ident,code,label,kind,currency,created,balance,last in rows]}


def _price(conn,order):
    row=conn.execute("""
      SELECT r.id,auto_demo_billable_total(r.work_order_id,r.id)
      FROM auto_work_order_estimate_revision r
      JOIN auto_work_order_estimate_decision d ON d.revision_id=r.id
        AND d.decision='APPROVED'
      WHERE r.work_order_id=%s AND
        r.version=(SELECT max(x.version) FROM auto_work_order_estimate_revision x
                   WHERE x.work_order_id=r.work_order_id)
    """,(order,)).fetchone()
    return (row if row else (None,None))


def read_order(conn,token,org,loc,order):
    with conn.transaction():
        _order(conn,token,org,loc,order,("MANAGER","CASHIER"))
        revision,total=_price(conn,order)
        amounts=_amounts(conn,order=order)
        rows=conn.execute("""
          SELECT e.id,e.account_id,e.revision_id,e.kind,e.amount_minor,
                 e.reason,e.business_day,e.created_at
          FROM auto_treasury_event e
          JOIN auto_treasury_account a ON a.id=e.account_id
            AND a.organization_id=%s AND a.location_id=%s
          WHERE e.work_order_id=%s ORDER BY e.created_at,e.id LIMIT 101
        """,(org,loc,order)).fetchall()
        if len(rows)>100:
            raise TreasuryConflict("EVENT_LIMIT")
        return {"syntheticOnly":True,"realMoney":False,"releaseAllowed":False,
            "orderId":str(order),"currency":"RUB",
            "approvedRevisionId":str(revision) if revision else None,
            "approvedTotalMinor":total,"dueMinor":(
                total-amounts["settledMinor"] if total is not None else None),
            **amounts,"events":[{
                "id":str(i),"accountId":str(a),"revisionId":str(v),
                "kind":k,"amountMinor":amount,"reason":reason,
                "businessDay":day.isoformat(),"createdAt":at.isoformat()
            } for i,a,v,k,amount,reason,day,at in rows]}


def record_event(conn,token,org,loc,order,*,account_id,request_key,
                 kind,amount_minor,reason):
    _valid_uuids(org,loc,order,account_id,request_key)
    if (kind not in KINDS or type(amount_minor) is not int
        or not 1<=amount_minor<=10**12 or type(reason) is not str
        or not 3<=len(reason.strip())<=160):
        raise TreasuryInvalid("INVALID_INPUT")
    with conn.transaction():
        actor=_order(conn,token,org,loc,order,("CASHIER",),cap="EDIT")
        _account(conn,org,loc,account_id,lock=True)
        existing=conn.execute("""
          SELECT id,account_id,work_order_id,actor_id,kind,amount_minor,reason
          FROM auto_treasury_event WHERE request_key=%s
        """,(request_key,)).fetchone()
        if existing:
            if existing[1:]!=(account_id,order,actor,kind,amount_minor,reason):
                raise TreasuryConflict("KEY_REUSED")
            return {"eventId":str(existing[0]),"syntheticOnly":True,
                    "replayed":True,**read_order(conn,token,org,loc,order)}
        revision,total=_price(conn,order)
        if revision is None or total is None:
            raise TreasuryConflict("APPROVED_PRICE_REQUIRED")
        try:
            with conn.transaction():
                ident=uuid4()
                conn.execute("""
                  INSERT INTO auto_treasury_event
                    (id,account_id,work_order_id,revision_id,actor_id,request_key,
                     kind,amount_minor,reason,business_day)
                  SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,
                         (clock_timestamp() AT TIME ZONE loc.timezone_name)::date
                  FROM auto_service_location loc
                  WHERE loc.id=%s AND loc.organization_id=%s AND loc.disabled_at IS NULL
                """,(ident,account_id,order,revision,actor,request_key,
                     kind,amount_minor,reason,loc,org))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation,
                psycopg.errors.NumericValueOutOfRange):
            raise TreasuryConflict("EVENT_CONFLICT") from None
        return {"eventId":str(ident),"syntheticOnly":True,
                "replayed":False,**read_order(conn,token,org,loc,order)}


def _day(raw):
    if type(raw) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",raw):
        raise TreasuryInvalid("INVALID_DAY")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise TreasuryInvalid("INVALID_DAY") from None


def _day_numbers(conn,account,day):
    row=conn.execute("""
      SELECT
        COALESCE(sum(CASE WHEN business_day<%s AND kind IN ('ADVANCE','PAYMENT')
            THEN amount_minor WHEN business_day<%s
            AND kind IN ('REFUND_ADVANCE','REFUND_PAYMENT')
            THEN -amount_minor ELSE 0 END),0)::bigint,
        COALESCE(sum(amount_minor) FILTER(WHERE business_day=%s AND
            kind IN ('ADVANCE','PAYMENT')),0)::bigint,
        COALESCE(sum(amount_minor) FILTER(WHERE business_day=%s AND
            kind IN ('REFUND_ADVANCE','REFUND_PAYMENT')),0)::bigint
      FROM auto_treasury_event WHERE account_id=%s
    """,(day,day,day,day,account)).fetchone()
    return row


def close_day(conn,token,org,loc,account,*,request_key,day):
    _valid_uuids(org,loc,account,request_key)
    date_value=_day(day)
    with conn.transaction():
        actor=_staff(conn,token,org,loc,("CASHIER",),cap="CLOSE")
        _account(conn,org,loc,account,lock=True)
        prior=conn.execute("""
          SELECT id,account_id,actor_id,business_day FROM auto_treasury_day_close
          WHERE request_key=%s
        """,(request_key,)).fetchone()
        if prior:
            if prior[1:]!=(account,actor,date_value):
                raise TreasuryConflict("KEY_REUSED")
            return {"closeId":str(prior[0]),"replayed":True,
                    "syntheticOnly":True,"realMoney":False,
                    "businessDay":day}
        opening,incoming,outgoing=_day_numbers(conn,account,date_value)
        closing=opening+incoming-outgoing
        if closing<0 or closing>10**15:
            raise TreasuryConflict("BALANCE_LIMIT")
        try:
            with conn.transaction():
                ident=uuid4()
                conn.execute("""
                  INSERT INTO auto_treasury_day_close
                  (id,account_id,business_day,actor_id,request_key,
                   opening_minor,incoming_minor,outgoing_minor,closing_minor)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,(ident,account,date_value,actor,request_key,
                     opening,incoming,outgoing,closing))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise TreasuryConflict("CLOSE_CONFLICT") from None
        return {"closeId":str(ident),"replayed":False,
            "syntheticOnly":True,"realMoney":False,"businessDay":day,
            "openingMinor":opening,"incomingMinor":incoming,
            "outgoingMinor":outgoing,"closingMinor":closing}


def report(conn,token,org,loc,*,from_day,to_day):
    first,last=_day(from_day),_day(to_day)
    count=(last-first).days+1
    if not 1<=count<=31:
        raise TreasuryInvalid("INVALID_RANGE")
    with conn.transaction():
        _staff(conn,token,org,loc,("MANAGER","CASHIER"))
        accounts=list_accounts(conn,token,org,loc)["items"]
        rows=[]
        for account in accounts:
            aid=UUID(account["id"])
            for i in range(count):
                day=first+timedelta(days=i)
                opening,incoming,outgoing=_day_numbers(conn,aid,day)
                closed=conn.execute("""
                   SELECT id,actor_id,closing_minor,created_at
                   FROM auto_treasury_day_close
                   WHERE account_id=%s AND business_day=%s
                """,(aid,day)).fetchone()
                if closed and closed[2]!=opening+incoming-outgoing:
                    raise TreasuryConflict("CLOSE_INTEGRITY")
                rows.append({
                    "accountId":account["id"],"accountCode":account["code"],
                    "kind":account["kind"],"localDay":day.isoformat(),
                    "openingMinor":opening,"incomingMinor":incoming,
                    "outgoingMinor":outgoing,
                    "closingMinor":opening+incoming-outgoing,
                    "closeId":str(closed[0]) if closed else None,
                    "closedAt":closed[3].isoformat() if closed else None,
                })
        return {"syntheticOnly":True,"realMoney":False,
                "currency":"RUB","basis":"INDEPENDENT_TREASURY_EVENT_DAYS",
                "accounts":accounts,"items":rows}
