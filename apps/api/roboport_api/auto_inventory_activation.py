"""Authorized, atomic synthetic SKU/STOCK activation and read-only reconciliation.

This is an INTERNAL PG17 entrypoint only: it cannot import owner/repair
history, cannot accept real goods and does not expose a public HTTP API.
"""
from collections import defaultdict
from uuid import UUID, uuid4

import psycopg

from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_stock_service import mutate_auto_stock


class InventoryActivationDenied(ValueError):
    """Stable error code without source identifiers, article or stock records."""


def _scoped_batch(conn: psycopg.Connection, batch_id: UUID,
                  org: UUID, loc: UUID, *, lock: bool) -> list[tuple]:
    suffix = " FOR UPDATE" if lock else ""
    batch = conn.execute(
        """SELECT organization_id,row_count,sku_count,stock_count
           FROM auto_migration_staging_batch
           WHERE id=%s AND organization_id=%s""" + suffix,
        (batch_id, org),
    ).fetchone()
    if batch is None:
        raise InventoryActivationDenied("BATCH_UNAVAILABLE")
    lines = conn.execute(
        """SELECT line_number,organization_id,location_id,
                  record_type,article,quantity
           FROM auto_migration_staging_line
           WHERE batch_id=%s ORDER BY line_number""",
        (batch_id,),
    ).fetchall()
    if (len(lines) != batch[1]
        or sum(line[3]=="SKU" for line in lines) != batch[2]
        or sum(line[3]=="STOCK" for line in lines) != batch[3]
        or any((line[1],line[2])!=(org,loc) for line in lines)):
        raise InventoryActivationDenied("BATCH_SCOPE_MISMATCH")
    item_lines = {}
    quantities: dict[str, int] = defaultdict(int)
    for line in lines:
        number,_,_,kind,article,amount = line
        if kind=="SKU":
            if article in item_lines or amount != 0:
                raise InventoryActivationDenied("BATCH_INCONSISTENT")
            item_lines[article]=number
        elif kind=="STOCK":
            if amount < 0 or amount > 1_000_000_000:
                raise InventoryActivationDenied("BATCH_INCONSISTENT")
            quantities[article]+=amount
        else:
            raise InventoryActivationDenied("BATCH_INCONSISTENT")
    if (not item_lines or any(a not in item_lines or total>1_000_000_000
                             for a,total in quantities.items())):
        raise InventoryActivationDenied("BATCH_INCONSISTENT")
    return lines


def _reconcile(conn: psycopg.Connection, batch_id: UUID,
               lines: list[tuple]) -> tuple[int,int]:
    rows=conn.execute(
        """SELECT a.line_number,a.record_type,a.sku_id,a.stock_event_id,
                  s.organization_id,s.location_id,s.article,
                  e.sku_id,e.organization_id,e.location_id,e.action,e.quantity
           FROM auto_migration_stock_activation a
           JOIN auto_stock_sku s ON s.id=a.sku_id
           LEFT JOIN auto_stock_event e ON e.id=a.stock_event_id
           WHERE a.batch_id=%s ORDER BY a.line_number""",
        (batch_id,),
    ).fetchall()
    if len(rows)!=len(lines):
        raise InventoryActivationDenied("ACTIVATION_INCOMPLETE")
    by_article: dict[str,UUID]={}
    actual_events:set[UUID]=set()
    for line,row in zip(lines,rows):
        number,org,loc,kind,article,quantity=line
        (rownum,rowkind,sku_id,event_id,sku_org,sku_loc,sku_article,
         event_sku,event_org,event_loc,action,event_qty)=row
        if ((number,kind,org,loc,article)!=(rownum,rowkind,sku_org,sku_loc,sku_article)
            or (kind=="SKU" and (event_id is not None or quantity!=0))
            or (kind=="STOCK" and quantity==0 and event_id is not None)
            or (kind=="STOCK" and quantity>0 and
                (event_id is None or
                 (event_sku,event_org,event_loc,action,event_qty)!=
                 (sku_id,org,loc,"RECEIPT",quantity)))):
            raise InventoryActivationDenied("ACTIVATION_MISMATCH")
        if kind=="SKU":
            if article in by_article:
                raise InventoryActivationDenied("ACTIVATION_MISMATCH")
            by_article[article]=sku_id
        # STOCK may precede SKU in the source; the final pass below
        # checks all references against the completed SKU mapping.
        if event_id is not None:
            if event_id in actual_events:
                raise InventoryActivationDenied("ACTIVATION_MISMATCH")
            actual_events.add(event_id)
    for line,row in zip(lines,rows):
        if line[3]=="STOCK" and by_article.get(line[4])!=row[2]:
            raise InventoryActivationDenied("ACTIVATION_MISMATCH")
    return len(by_article),len(actual_events)


def activate_synthetic_inventory(conn: psycopg.Connection,token: str,*,
                                 organization_id: UUID,location_id: UUID,
                                 batch_id: UUID,synthetic_only: bool=False) -> dict:
    if (synthetic_only is not True or type(token) is not str or not token
        or any(type(v) is not UUID for v in
               (organization_id,location_id,batch_id))):
        raise InventoryActivationDenied("FORBIDDEN")
    with conn.transaction():
        # Both independent grants are required; neither the legacy user_role
        # nor an unscoped MANAGER grant implicitly conveys WAREHOUSE.
        if not (organization_role_allows(
            conn,token,organization_id,"MANAGER",location_id,for_mutation=True)
            and organization_role_allows(
                conn,token,organization_id,"WAREHOUSE",location_id,for_mutation=True)
        ):
            raise InventoryActivationDenied("FORBIDDEN")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None:
            raise InventoryActivationDenied("FORBIDDEN")
        lines=_scoped_batch(conn,batch_id,organization_id,location_id,lock=True)
        previous=conn.execute(
            """SELECT count(*) FROM auto_migration_stock_activation
               WHERE batch_id=%s""",(batch_id,),
        ).fetchone()[0]
        if previous:
            item_count,receipt_count=_reconcile(conn,batch_id,lines)
            return {"ok":True,"replayed":True,"rows":len(lines),
                    "skus":item_count,"receipts":receipt_count,"writes":0,
                    "mode":"SYNTHETIC_INVENTORY_ACTIVATION"}
        items=[line for line in lines if line[3]=="SKU"]
        stock=[line for line in lines if line[3]=="STOCK"]
        sku_by_article={}
        # A different batch with the same article must not overwrite or
        # attach to stock already assigned to a branch.
        try:
            with conn.transaction():
                for number,_,_,_,article,_ in items:
                    if conn.execute(
                        """SELECT 1 FROM auto_stock_sku
                           WHERE organization_id=%s AND location_id=%s
                             AND article=%s""",
                        (organization_id,location_id,article),
                    ).fetchone():
                        raise InventoryActivationDenied("ARTICLE_CONFLICT")
                    item_id=uuid4()
                    conn.execute(
                        """INSERT INTO auto_stock_sku
                           (id,organization_id,location_id,article)
                           VALUES(%s,%s,%s,%s)""",
                        (item_id,organization_id,location_id,article),
                    )
                    conn.execute(
                        """INSERT INTO auto_migration_stock_activation
                           (batch_id,line_number,record_type,sku_id,created_by)
                           VALUES(%s,%s,'SKU',%s,%s)""",
                        (batch_id,number,item_id,actor),
                    )
                    sku_by_article[article]=item_id
                receipts=0
                for number,_,_,_,article,amount in stock:
                    item_id=sku_by_article[article]
                    event_id=None
                    if amount>0:
                        event_id=mutate_auto_stock(
                            conn,token,organization_id,location_id,item_id,
                            uuid4(),"RECEIPT",amount,
                        )
                        receipts+=1
                    conn.execute(
                        """INSERT INTO auto_migration_stock_activation
                           (batch_id,line_number,record_type,sku_id,
                            stock_event_id,created_by)
                           VALUES(%s,%s,'STOCK',%s,%s,%s)""",
                        (batch_id,number,item_id,event_id,actor),
                    )
        except (psycopg.errors.UniqueViolation,psycopg.errors.ForeignKeyViolation,
                psycopg.errors.CheckViolation) as exc:
            raise InventoryActivationDenied("ACTIVATION_CONFLICT") from None
        item_count,receipt_count=_reconcile(conn,batch_id,lines)
        if item_count!=len(items) or receipt_count!=receipts:
            raise InventoryActivationDenied("ACTIVATION_INCOMPLETE")
        return {"ok":True,"replayed":False,"rows":len(lines),
                "skus":item_count,"receipts":receipt_count,
                "writes":len(lines)+len(items)+receipts,
                "mode":"SYNTHETIC_INVENTORY_ACTIVATION"}


def reconcile_synthetic_inventory(conn: psycopg.Connection,token: str,*,
                                  organization_id: UUID,location_id: UUID,
                                  batch_id: UUID,synthetic_only: bool=False) -> dict:
    """Read-only evidence of exact activated batch and unchanged receipt provenance."""
    if (synthetic_only is not True or type(token) is not str or not token
        or any(type(v) is not UUID for v in
               (organization_id,location_id,batch_id))):
        raise InventoryActivationDenied("FORBIDDEN")
    with conn.transaction():
        if not (organization_role_allows(
            conn,token,organization_id,"MANAGER",location_id,for_mutation=True)
            and organization_role_allows(
                conn,token,organization_id,"WAREHOUSE",location_id,for_mutation=True)
        ):
            raise InventoryActivationDenied("FORBIDDEN")
        lines=_scoped_batch(conn,batch_id,organization_id,location_id,lock=False)
        sku_count,receipt_count=_reconcile(conn,batch_id,lines)
        return {"ok":True,"rows":len(lines),"skus":sku_count,
                "receipts":receipt_count,"writes":0,
                "mode":"SYNTHETIC_INVENTORY_RECONCILIATION"}
