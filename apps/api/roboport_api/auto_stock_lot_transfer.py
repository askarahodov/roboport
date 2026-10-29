"""Authorized internal same-branch automotive stock lot transfer."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied, AutoStockConflict


def transfer_stock_lot(conn: psycopg.Connection, token: str,
    organization_id: UUID, location_id: UUID, source_lot_id: UUID,
    destination_lot_id: UUID, request_id: UUID, quantity: int) -> UUID:
    if (type(token) is not str or not token
        or not all(isinstance(x,UUID) for x in
                   (organization_id,location_id,source_lot_id,destination_lot_id,request_id))
        or source_lot_id==destination_lot_id
        or type(quantity) is not int or not 1<=quantity<=1_000_000_000):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,organization_id,
                "WAREHOUSE",location_id,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        rows=conn.execute("""SELECT id FROM auto_stock_lot
            WHERE id IN (%s,%s) AND organization_id=%s AND location_id=%s
            ORDER BY id FOR SHARE""",
            (source_lot_id,destination_lot_id,organization_id,location_id)).fetchall()
        if len(rows)!=2:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                row=conn.execute(
                    "SELECT record_auto_stock_lot_transfer(%s,%s,%s,%s)",
                    (source_lot_id,destination_lot_id,request_id,quantity)).fetchone()
            return row[0]
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
