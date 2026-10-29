"""Authorize mechanic receipt of a previously posted lot issue."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_stock_service import AutoStockDenied,AutoStockConflict


def acknowledge_mechanic_handoff(conn: psycopg.Connection,token: str,
    organization_id: UUID, location_id: UUID, issue_id: UUID,
    request_id: UUID) -> UUID:
    if (type(token) is not str or not token or not all(isinstance(v,UUID) for v in
        (organization_id,location_id,issue_id,request_id))):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,organization_id,"MECHANIC",
                location_id,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        row=conn.execute("""SELECT i.id FROM auto_stock_lot_work_order_issue i
          JOIN auto_work_order w ON w.id=i.work_order_id
          JOIN auto_stock_lot l ON l.id=i.lot_id
          WHERE i.id=%s AND w.organization_id=%s AND w.location_id=%s
            AND l.organization_id=w.organization_id AND l.location_id=w.location_id
          FOR SHARE OF i,w,l""",(issue_id,organization_id,location_id)).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.mechanic_handoff_actor',%s,true)",
                             (str(actor),))
                return conn.execute(
                    "SELECT record_auto_stock_mechanic_handoff(%s,%s)",
                    (issue_id,request_id)).fetchone()[0]
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config('roboport.mechanic_handoff_actor','',true)")
