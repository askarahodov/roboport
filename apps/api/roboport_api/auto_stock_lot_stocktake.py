"""Internal audited stocktake workflow for one lot."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied, AutoStockConflict
from .session_lookup import session_subject


def _run(conn, token, organization_id, location_id, lot_id, sql, args):
    with conn.transaction():
        if not organization_role_allows(
            conn, token, organization_id, "WAREHOUSE", location_id,
            for_mutation=True,
        ):
            raise AutoStockDenied("FORBIDDEN")
        if conn.execute(
            """SELECT id FROM auto_stock_lot WHERE id=%s
               AND organization_id=%s AND location_id=%s FOR SHARE""",
            (lot_id, organization_id, location_id),
        ).fetchone() is None:
            raise AutoStockDenied("FORBIDDEN")
        actor = session_subject(conn, token, for_mutation=True)
        if actor is None:
            raise AutoStockDenied('FORBIDDEN')
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.stocktake_actor',%s,true)", (str(actor),))
                return conn.execute(sql, args).fetchone()[0]
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config(\'roboport.stocktake_actor\',\'\',true)")


def count_stock_lot(conn, token: str, organization_id: UUID,
    location_id: UUID, lot_id: UUID, request_id: UUID,
    actual: int, reason: str) -> UUID:
    if (type(token) is not str or not token or
        not all(isinstance(x,UUID) for x in
                (organization_id,location_id,lot_id,request_id)) or
        type(actual) is not int or not 0<=actual<=1_000_000_000 or
        type(reason) is not str or not 3<=len(reason.strip())<=240):
        raise AutoStockDenied("INVALID_INPUT")
    return _run(conn,token,organization_id,location_id,lot_id,
                "SELECT record_auto_stock_lot_count(%s,%s,%s,%s)",
                (lot_id,request_id,actual,reason))


def approve_stock_lot_count(conn, token: str, organization_id: UUID,
    location_id: UUID, count_id: UUID, request_id: UUID) -> UUID:
    if (type(token) is not str or not token or
        not all(isinstance(x,UUID) for x in
                (organization_id,location_id,count_id,request_id))):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(
            conn,token,organization_id,"MANAGER",location_id,for_mutation=True
        ):
            raise AutoStockDenied("FORBIDDEN")
        row=conn.execute("""SELECT l.id FROM auto_stock_lot_count c
            JOIN auto_stock_lot l ON l.id=c.lot_id
            WHERE c.id=%s AND l.organization_id=%s AND l.location_id=%s
            FOR SHARE OF c,l""",
            (count_id,organization_id,location_id)).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        actor = session_subject(conn, token, for_mutation=True)
        if actor is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                conn.execute("SELECT set_config(\'roboport.stocktake_actor\',%s,true)", (str(actor),))
                result=conn.execute(
                    "SELECT approve_auto_stock_lot_count(%s,%s)",
                    (count_id,request_id)).fetchone()[0]
                conn.execute("SET CONSTRAINTS auto_stock_serial_allocation_guard IMMEDIATE")
                conn.execute("SET CONSTRAINTS auto_stock_serial_allocation_guard DEFERRED")
                return result
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config(\'roboport.stocktake_actor\',\'\',true)")



def pending_stock_lot_counts(conn, token: str, organization_id: UUID,
                             location_id: UUID, limit: int = 20,
                             after_count_id: UUID | None = None
                             ) -> tuple[list[dict], UUID | None]:
    """Current unapproved counts in the manager's active branch, bounded page.

    Only fixed stock identifiers and operational quantities are exposed;
    maker identity, customer/vehicle data and idempotency keys are withheld.
    A stale flag is advisory: approval independently rechecks current stock.
    """
    if (type(token) is not str or not token or
        not isinstance(organization_id, UUID) or
        not isinstance(location_id, UUID) or
        type(limit) is not int or not 1 <= limit <= 50 or
        (after_count_id is not None and not isinstance(after_count_id, UUID))):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(
            conn, token, organization_id, "MANAGER", location_id,
            for_mutation=True,
        ):
            raise AutoStockDenied("FORBIDDEN")
        rows = conn.execute("""
            SELECT c.id,l.id,s.article,l.batch_code,bin.code,
                   c.expected,c.actual,lb.allocated,c.reason
            FROM auto_stock_lot_count c
            JOIN auto_stock_lot l ON l.id=c.lot_id
            JOIN auto_stock_sku s ON s.id=c.sku_id
              AND s.organization_id=l.organization_id
              AND s.location_id=l.location_id
            JOIN auto_stock_bin bin ON bin.id=l.bin_id
            JOIN auto_stock_lot_balance lb ON lb.lot_id=l.id
            JOIN auto_organization o ON o.id=l.organization_id
            JOIN auto_service_location loc ON loc.id=l.location_id
              AND loc.organization_id=l.organization_id
            WHERE l.organization_id=%s AND l.location_id=%s
              AND o.disabled_at IS NULL AND loc.disabled_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM auto_stock_lot_count_approval approval
                WHERE approval.count_id=c.id
              )
              AND (%s::uuid IS NULL OR c.id>%s::uuid)
            ORDER BY c.id LIMIT %s
        """,(organization_id,location_id,after_count_id,after_count_id,limit+1)
        ).fetchall()
        cursor=rows[limit-1][0] if len(rows)>limit else None
        items=[{
            "countId":str(count_id),"lotId":str(lot_id),
            "article":article,"batchCode":batch,"binCode":bin_code,
            "expected":expected,"actual":actual,"currentAllocated":current,
            "stale":current != expected,"reason":reason,
        } for count_id,lot_id,article,batch,bin_code,expected,actual,current,reason
          in rows[:limit]]
        return items,cursor
