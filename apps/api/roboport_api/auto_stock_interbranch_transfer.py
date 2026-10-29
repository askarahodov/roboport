"""Internal dual-branch-authorized physical inventory transfer."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied, AutoStockConflict


def transfer_interbranch_stock(conn: psycopg.Connection, token: str,
    organization_id: UUID, source_location_id: UUID,
    destination_location_id: UUID, source_lot_id: UUID,
    destination_lot_id: UUID, request_id: UUID, quantity: int) -> UUID:
    if (type(token) is not str or not token
        or not all(isinstance(x,UUID) for x in
                   (organization_id,source_location_id,destination_location_id,
                    source_lot_id,destination_lot_id,request_id))
        or source_location_id==destination_location_id
        or source_lot_id==destination_lot_id
        or type(quantity) is not int or not 1<=quantity<=1_000_000_000):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        # Two distinct branch grants are mandatory, not merely one source grant.
        for location in (source_location_id,destination_location_id):
            if not organization_role_allows(conn,token,organization_id,
                   "WAREHOUSE",location,for_mutation=True):
                raise AutoStockDenied("FORBIDDEN")
        matches=conn.execute("""
            SELECT id FROM auto_stock_lot
            WHERE organization_id=%s AND
             ((id=%s AND location_id=%s) OR (id=%s AND location_id=%s))
            FOR SHARE
        """,(organization_id,source_lot_id,source_location_id,
             destination_lot_id,destination_location_id)).fetchall()
        if len(matches)!=2:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                return conn.execute(
                    "SELECT record_auto_stock_interbranch_transfer(%s,%s,%s,%s)",
                    (source_lot_id,destination_lot_id,request_id,quantity)
                ).fetchone()[0]
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
