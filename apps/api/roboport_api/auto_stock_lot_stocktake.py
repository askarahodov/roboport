"""Internal audited stocktake workflow for one lot."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied, AutoStockConflict


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
        try:
            with conn.transaction():
                return conn.execute(sql, args).fetchone()[0]
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None


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
            conn,token,organization_id,"WAREHOUSE",location_id,for_mutation=True
        ):
            raise AutoStockDenied("FORBIDDEN")
        row=conn.execute("""SELECT l.id FROM auto_stock_lot_count c
            JOIN auto_stock_lot l ON l.id=c.lot_id
            WHERE c.id=%s AND l.organization_id=%s AND l.location_id=%s
            FOR SHARE OF c,l""",
            (count_id,organization_id,location_id)).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                return conn.execute(
                    "SELECT approve_auto_stock_lot_count(%s,%s)",
                    (count_id,request_id)).fetchone()[0]
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
