"""Сверка складского ledger и детализация до исходных физических движений."""
import hashlib
from datetime import datetime,timezone
from uuid import UUID

from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied
from .keyset_cursor import encode_cursor,decode_cursor

EPOCH=datetime(1970,1,1,tzinfo=timezone.utc)


def _authority(db,token,org,loc):
    if not any(organization_role_allows(db,token,org,role,loc,for_mutation=True)
               for role in ("WAREHOUSE","MANAGER")):
        raise AutoStockDenied("FORBIDDEN")


def _scope(*parts):
    return hashlib.sha256(":".join(map(str,parts)).encode()).hexdigest()[:32]


def _position(cursor,scope,token):
    try:
        return decode_cursor(cursor,scope,token) if cursor is not None else None
    except ValueError:
        raise AutoStockDenied("INVALID_INPUT") from None


def _balance(row):
    ident,article,on_hand,reserved,allocated,unallocated,available,discrepancy=row
    return {"skuId":str(ident),"article":article,"onHand":str(on_hand),
            "reserved":str(reserved),"allocated":str(allocated),"unallocated":str(unallocated),
            "available":str(available),"discrepancy":discrepancy}


def stock_report(db,token,org,loc,query="",status="ALL",cursor=None):
    if (type(query) is not str or len(query)>80 or any(ord(c)<32 for c in query)
        or status not in ("ALL","DISCREPANCY","UNALLOCATED","RESERVED","OUT_OF_STOCK")):
        raise AutoStockDenied("INVALID_INPUT")
    query=query.strip()
    with db.transaction():
        _authority(db,token,org,loc)
        scope=_scope("stock-reconciliation",org,loc,query,status)
        after=_position(cursor,scope,token)
        if after and after[0]!=EPOCH:
            raise AutoStockDenied("INVALID_INPUT")
        # Сводка и страница вычисляются одним SQL statement, на одном MVCC snapshot.
        rows=db.execute("""
          WITH scoped AS MATERIALIZED (
            SELECT sku_id,article,on_hand,reserved,lot_allocated,unallocated,available,discrepancy
            FROM auto_stock_reconciliation
            WHERE organization_id=%s AND location_id=%s
              AND (%s='' OR strpos(lower(article),lower(%s))>0)
          ), summary AS (
            SELECT count(*) AS total,count(*) FILTER(WHERE discrepancy) AS discrepancies,
                   count(*) FILTER(WHERE unallocated>0) AS unplaced,
                   count(*) FILTER(WHERE reserved>0) AS reservations,
                   count(*) FILTER(WHERE available=0) AS unavailable
            FROM scoped
          ), filtered AS (
            SELECT * FROM scoped WHERE %s='ALL'
              OR (%s='DISCREPANCY' AND discrepancy)
              OR (%s='UNALLOCATED' AND unallocated>0)
              OR (%s='RESERVED' AND reserved>0)
              OR (%s='OUT_OF_STOCK' AND available=0)
          ), page AS (
            SELECT * FROM filtered WHERE (%s::uuid IS NULL OR sku_id>%s::uuid)
            ORDER BY sku_id LIMIT 51
          )
          SELECT summary.*, (SELECT count(*) FROM filtered),page.*
          FROM summary LEFT JOIN page ON true ORDER BY page.sku_id
        """,(org,loc,query,query,*([status]*5),after[1] if after else None,
              after[1] if after else None)).fetchall()
        first=rows[0]
        page=[r[6:] for r in rows if r[6] is not None]
        return {"syntheticOnly":True,"organizationId":str(org),"locationId":str(loc),
                "summary":dict(zip(("total","discrepancies","unallocated","reserved","outOfStock","filtered"),
                                   first[:6])),
                "items":[_balance(row) for row in page[:50]],
                "nextCursor":encode_cursor(EPOCH,page[49][0],scope,token) if len(page)>50 else None}


def stock_detail(db,token,org,loc,sku,kind="lots",cursor=None):
    if kind not in ("lots","events"):
        raise AutoStockDenied("INVALID_INPUT")
    with db.transaction():
        _authority(db,token,org,loc)
        if not db.execute("""
          SELECT 1 FROM auto_stock_sku WHERE id=%s AND organization_id=%s AND location_id=%s
          FOR SHARE
        """,(sku,org,loc)).fetchone():
            raise AutoStockDenied("FORBIDDEN")
        scope=_scope("stock-detail",org,loc,sku,kind)
        after=_position(cursor,scope,token)
        pos=(after[0],after[0],after[1]) if after else (None,None,None)
        if kind=="lots":
            rows=db.execute("""
              SELECT l.id,l.created_at,l.batch_code,b.code,lb.allocated
              FROM auto_stock_lot l JOIN auto_stock_bin b ON b.id=l.bin_id
              JOIN auto_stock_lot_balance lb ON lb.lot_id=l.id
              WHERE l.sku_id=%s AND l.organization_id=%s AND l.location_id=%s
              AND (%s::timestamptz IS NULL OR (l.created_at,l.id)>(%s::timestamptz,%s::uuid))
              ORDER BY l.created_at,l.id LIMIT 51
            """,(sku,org,loc,*pos)).fetchall()
            items=[{"lotId":str(i),"createdAt":t.isoformat(),"batchCode":batch,
                    "binCode":b,"allocated":str(q)} for i,t,batch,b,q in rows[:50]]
        else:
            rows=db.execute("""
              SELECT id,created_at,action,quantity FROM auto_stock_event
              WHERE sku_id=%s AND organization_id=%s AND location_id=%s
              AND (%s::timestamptz IS NULL OR (created_at,id)<(%s::timestamptz,%s::uuid))
              ORDER BY created_at DESC,id DESC LIMIT 51
            """,(sku,org,loc,*pos)).fetchall()
            items=[{"eventId":str(i),"createdAt":t.isoformat(),"action":a,"quantity":str(q)}
                   for i,t,a,q in rows[:50]]
        return {"syntheticOnly":True,"organizationId":str(org),"locationId":str(loc),
                "skuId":str(sku),"kind":kind,"items":items,
                "nextCursor":encode_cursor(rows[49][1],rows[49][0],scope,token) if len(rows)>50 else None}
