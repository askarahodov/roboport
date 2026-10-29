"""Tenant/branch scoped read-only warehouse lot discovery for stocktake."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied


def list_stocktake_lots(conn: psycopg.Connection, token: str,
    organization_id: UUID, location_id: UUID, limit: int = 30,
    after_lot_id: UUID | None = None) -> tuple[list[dict], UUID | None]:
    if (type(token) is not str or not token
        or not isinstance(organization_id,UUID)
        or not isinstance(location_id,UUID)
        or type(limit) is not int or not 1<=limit<=50
        or (after_lot_id is not None and not isinstance(after_lot_id,UUID))):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        permitted=(
            organization_role_allows(conn,token,organization_id,"WAREHOUSE",
                                     location_id,for_mutation=True)
            or organization_role_allows(conn,token,organization_id,"MANAGER",
                                        location_id,for_mutation=True)
        )
        if not permitted:
            raise AutoStockDenied("FORBIDDEN")
        rows=conn.execute("""
            SELECT l.id,s.article,l.batch_code,b.code,
                   lb.allocated, sb.on_hand,sb.reserved
            FROM auto_stock_lot l
            JOIN auto_stock_sku s ON s.id=l.sku_id
            JOIN auto_stock_bin b ON b.id=l.bin_id
            JOIN auto_stock_lot_balance lb ON lb.lot_id=l.id
            JOIN auto_stock_balance sb ON sb.sku_id=l.sku_id
            JOIN auto_organization o ON o.id=l.organization_id
            JOIN auto_service_location loc ON loc.id=l.location_id
              AND loc.organization_id=l.organization_id
            WHERE l.organization_id=%s AND l.location_id=%s
              AND o.disabled_at IS NULL AND loc.disabled_at IS NULL
              AND (%s::uuid IS NULL OR l.id>%s::uuid)
            ORDER BY l.id LIMIT %s
        """,(organization_id,location_id,after_lot_id,after_lot_id,limit+1)).fetchall()
        next_id=rows[limit-1][0] if len(rows)>limit else None
        page=rows[:limit]
        return ([{"lotId":str(lot),"article":article,"batchCode":batch,
                  "binCode":bin_code,"allocated":allocated,"onHand":on_hand,
                  "reserved":reserved}
                 for lot,article,batch,bin_code,allocated,on_hand,reserved in page],
                next_id)
