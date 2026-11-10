"""Atomic synthetic service complexes and branch stock reservations for WorkOrders.

One manager request selects the immutable service composition and reserves every
PART/CONSUMABLE SKU. No partial order or stock receipts survive a failed request.
"""
import re
from uuid import UUID,uuid4

import psycopg

from .auto_work_tariffs import _actor,_branch_lock,_org_policy_lock,TariffDenied
from .auto_work_order_estimate import _staff_and_scope,AutoEstimateDenied
from .auto_document_access import require_document_access,DocumentPolicyDenied


class ComplexDenied(ValueError):
    pass


class ComplexConflict(ValueError):
    pass


def _priced_scope(conn,token,org,loc,cap,*,order=None):
    # Work complexes contain sales prices. They are an ESTIMATE input and
    # cannot bypass price or write restrictions of the estimate document.
    try:
        for action in dict.fromkeys(("READ","PRICE_SALES",cap)):
            cutoff=require_document_access(conn,token,org,loc,("MANAGER",),
                                           "ESTIMATE",action,order=order)
            if cutoff is not None and order is None:
                # List/catalog returns all immutable historical templates.
                raise ComplexDenied("HISTORY_DENIED")
    except DocumentPolicyDenied:
        raise ComplexDenied("FORBIDDEN") from None


def _validate_catalog(request_key,code,title,services,materials):
    if (type(request_key) is not UUID or type(code) is not str
        or re.fullmatch(r"[A-Z0-9_.-]{3,64}",code) is None
        or type(title) is not str or not 1<=len(title)<=120
        or type(services) is not list or not 1<=len(services)<=50
        or any(type(s) is not UUID for s in services)
        or len(set(services))!=len(services)
        or type(materials) is not list or len(materials)>50):
        raise ComplexDenied("INVALID_INPUT")
    seen=set()
    for item in materials:
        if (type(item) is not dict
            or set(item)!={"skuId","itemKind","quantity","unitPriceMinor"}
            or type(item["skuId"]) is not UUID
            or item["skuId"] in seen
            or item["itemKind"] not in ("PART","CONSUMABLE")
            or type(item["quantity"]) is not int
            or not 1<=item["quantity"]<=1_000_000
            or type(item["unitPriceMinor"]) is not int
            or not 0<=item["unitPriceMinor"]<=1_000_000_000
            or (item["itemKind"]=="CONSUMABLE" and item["unitPriceMinor"]!=0)):
            raise ComplexDenied("INVALID_INPUT")
        seen.add(item["skuId"])
    if sum(x["quantity"]*x["unitPriceMinor"] for x in materials)>10**12:
        raise ComplexDenied("INVALID_INPUT")


def _detail(conn,ident):
    header=conn.execute("""
      SELECT id,code,title,organization_id,location_id,created_by,request_key
      FROM auto_work_complex WHERE id=%s
        AND EXISTS(SELECT 1 FROM auto_work_complex_activation active
          WHERE active.complex_id=auto_work_complex.id)
    """,(ident,)).fetchone()
    if header is None:
        return None
    services=conn.execute("""
      SELECT s.id,s.code,s.title,s.duration_minutes
      FROM auto_work_complex_service component
      JOIN auto_service_catalog s ON s.id=component.service_id
      WHERE component.complex_id=%s ORDER BY s.code,s.id LIMIT 51
    """,(ident,)).fetchall()
    materials=conn.execute("""
      SELECT m.id,sku.id,sku.article,m.item_kind,m.quantity,m.unit_price_minor
      FROM auto_work_complex_material m
      JOIN auto_stock_sku sku ON sku.id=m.sku_id
      WHERE m.complex_id=%s ORDER BY sku.article,sku.id LIMIT 51
    """,(ident,)).fetchall()
    if len(services)>50 or len(materials)>50:
        raise ComplexConflict("LIMIT")
    return {
        "id":str(header[0]),"code":header[1],"title":header[2],
        "organizationId":str(header[3]),"locationId":str(header[4]),
        "createdBy":str(header[5]),"requestKey":str(header[6]),
        "services":[{"id":str(s),"code":code,"title":title,"durationMinutes":minutes}
                    for s,code,title,minutes in services],
        "materials":[{"componentId":str(i),"skuId":str(sku),"article":article,
                      "itemKind":kind,"quantity":qty,"unitPriceMinor":unit,
                      "amountMinor":qty*unit}
                     for i,sku,article,kind,qty,unit in materials],
    }


def _scope(conn,token,org,loc):
    try:
        return _actor(conn,token,org,loc)
    except TariffDenied:
        raise ComplexDenied("FORBIDDEN") from None


def list_complexes(conn,token,org,loc):
    with conn.transaction():
        _scope(conn,token,org,loc)
        _priced_scope(conn,token,org,loc,"READ")
        ids=conn.execute("""
          SELECT c.id FROM auto_work_complex c
          JOIN auto_work_complex_activation active ON active.complex_id=c.id
          WHERE c.organization_id=%s AND c.location_id=%s
          ORDER BY c.code,c.id LIMIT 51
        """,(org,loc)).fetchall()
        if len(ids)>50:
            raise ComplexConflict("LIMIT")
        return {"items":[_detail(conn,r[0]) for r in ids],"syntheticOnly":True}


def create_complex(conn,token,org,loc,*,request_key,code,title,services,materials):
    _validate_catalog(request_key,code,title,services,materials)
    with conn.transaction():
        # Acquire org SHARE then branch UPDATE before any role/ACL helpers
        # take branch SHARE, avoiding two concurrent SHARE→UPDATE upgrades.
        try:
            _org_policy_lock(conn,org)
        except TariffDenied:
            raise ComplexDenied("FORBIDDEN") from None
        _branch_lock(conn,org,loc)
        actor=_scope(conn,token,org,loc)
        _priced_scope(conn,token,org,loc,"CREATE")
        prior=conn.execute("""
          SELECT id,organization_id,location_id FROM auto_work_complex
          WHERE request_key=%s
        """,(request_key,)).fetchone()
        if prior:
            detail=_detail(conn,prior[0])
            if (detail is None or prior[1:]!=(org,loc)
                or detail["code"]!=code or detail["title"]!=title
                or detail["createdBy"]!=str(actor)
                or set(s["id"] for s in detail["services"])
                    !=set(str(s) for s in services)
                or {m["skuId"]:(m["itemKind"],m["quantity"],m["unitPriceMinor"])
                    for m in detail["materials"]}
                    !={str(m["skuId"]):(m["itemKind"],m["quantity"],m["unitPriceMinor"])
                       for m in materials}):
                raise ComplexConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,"complex":detail}
        if conn.execute("""
          SELECT 1 FROM auto_work_complex WHERE location_id=%s AND code=%s
        """,(loc,code)).fetchone():
            raise ComplexConflict("CODE_EXISTS")
        if conn.execute("""
          SELECT count(*) FROM auto_service_catalog
          WHERE organization_id=%s AND location_id=%s AND id=ANY(%s)
        """,(org,loc,services)).fetchone()[0]!=len(services):
            raise ComplexDenied("SERVICE_UNAVAILABLE")
        sku_ids=[m["skuId"] for m in materials]
        if sku_ids and conn.execute("""
          SELECT count(*) FROM auto_stock_sku
          WHERE organization_id=%s AND location_id=%s AND id=ANY(%s)
        """,(org,loc,sku_ids)).fetchone()[0]!=len(sku_ids):
            raise ComplexDenied("MATERIAL_UNAVAILABLE")
        ident=uuid4()
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_complex
                    (id,organization_id,location_id,code,title,created_by,request_key)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)
                """,(ident,org,loc,code,title,actor,request_key))
                for service in sorted(services):
                    conn.execute("""
                      INSERT INTO auto_work_complex_service(complex_id,service_id)
                      VALUES(%s,%s)
                    """,(ident,service))
                for item in sorted(materials,key=lambda m:m["skuId"]):
                    conn.execute("""
                      INSERT INTO auto_work_complex_material
                        (id,complex_id,sku_id,item_kind,quantity,unit_price_minor)
                      VALUES(%s,%s,%s,%s,%s,%s)
                    """,(uuid4(),ident,item["skuId"],item["itemKind"],
                         item["quantity"],item["unitPriceMinor"]))
                conn.execute("""
                  INSERT INTO auto_work_complex_activation(complex_id,actor_id)
                  VALUES(%s,%s)
                """,(ident,actor))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise ComplexConflict("CATALOG_CONFLICT") from None
        return {"ok":True,"replayed":False,"complex":_detail(conn,ident)}


def _selected(conn,order):
    rows=conn.execute("""
      SELECT selection.id,selection.complex_id,selection.request_key
      FROM auto_work_order_complex_selection selection
      WHERE selection.work_order_id=%s ORDER BY selection.created_at,selection.id LIMIT 11
    """,(order,)).fetchall()
    if len(rows)>10:
        raise ComplexConflict("LIMIT")
    return {"items":[{
        "selectionId":str(selection),"requestKey":str(key),
        "complex":_detail(conn,complex_id),
        "materialReceipts":[{
          "componentId":str(component),"stockEventId":str(event)
        } for component,event in conn.execute("""
          SELECT component_id,stock_event_id FROM auto_work_complex_material_reserved
          WHERE selection_id=%s ORDER BY component_id
        """,(selection,)).fetchall()]
    } for selection,complex_id,key in rows],"syntheticOnly":True}


def selected_complexes(conn,token,org,loc,order):
    with conn.transaction():
        try:
            _staff_and_scope(conn,token,org,loc,order)
        except AutoEstimateDenied:
            raise ComplexDenied("FORBIDDEN") from None
        _priced_scope(conn,token,org,loc,"READ",order=order)
        return _selected(conn,order)


def select_complex(conn,token,org,loc,order,*,request_key,complex_id):
    if any(type(x) is not UUID for x in (org,loc,order,request_key,complex_id)):
        raise ComplexDenied("INVALID_INPUT")
    with conn.transaction():
        try:
            actor=_staff_and_scope(conn,token,org,loc,order)
        except AutoEstimateDenied:
            raise ComplexDenied("FORBIDDEN") from None
        _priced_scope(conn,token,org,loc,"CREATE",order=order)
        prior=conn.execute("""
          SELECT work_order_id,complex_id,actor_id FROM auto_work_order_complex_selection
          WHERE request_key=%s
        """,(request_key,)).fetchone()
        if prior:
            if prior!=(order,complex_id,actor):
                raise ComplexConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**_selected(conn,order)}
        if conn.execute("""
          SELECT 1 FROM auto_work_order_estimate_revision WHERE work_order_id=%s
        """,(order,)).fetchone():
            raise ComplexConflict("ALREADY_ESTIMATED")
        detail=_detail(conn,complex_id)
        if detail is None or (detail["organizationId"],detail["locationId"])!=(
                str(org),str(loc)):
            raise ComplexDenied("FORBIDDEN")
        if conn.execute("""
          SELECT 1 FROM auto_work_order_complex_selection
          WHERE work_order_id=%s AND complex_id=%s
        """,(order,complex_id)).fetchone():
            raise ComplexConflict("COMPLEX_ALREADY_SELECTED")
        if conn.execute("""
          SELECT 1 FROM auto_work_order_service chosen
          WHERE chosen.work_order_id=%s AND chosen.service_id=ANY(%s)
        """,(order,[UUID(s["id"]) for s in detail["services"]])).fetchone():
            raise ComplexConflict("SERVICE_ALREADY_SELECTED")
        if conn.execute("""
          SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s
        """,(order,)).fetchone()[0]+len(detail["services"])>50:
            raise ComplexConflict("SERVICE_LIMIT")
        selection=uuid4()
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_order_complex_selection
                    (id,work_order_id,complex_id,organization_id,
                     location_id,actor_id,request_key)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)
                """,(selection,order,complex_id,org,loc,actor,request_key))
                for svc in detail["services"]:
                    conn.execute("""
                      INSERT INTO auto_work_order_service
                        (id,work_order_id,service_id,organization_id,location_id)
                      VALUES(%s,%s,%s,%s,%s)
                    """,(uuid4(),order,UUID(svc["id"]),org,loc))
                # A fixed UUID order avoids deadlocks between overlapping
                # complexes reserving different SKUs concurrently.
                for m in sorted(detail["materials"],key=lambda m:m["skuId"]):
                    sku,qty=UUID(m["skuId"]),m["quantity"]
                    reserved=conn.execute("""
                      SELECT record_auto_work_order_stock(%s,%s,%s,'RESERVE',%s)
                    """,(order,sku,uuid4(),qty)).fetchone()[0]
                    conn.execute("""
                      INSERT INTO auto_work_complex_material_reserved
                        (id,selection_id,component_id,stock_event_id)
                      VALUES(%s,%s,%s,%s)
                    """,(uuid4(),selection,UUID(m["componentId"]),reserved))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise ComplexConflict("STOCK_OR_SERVICE_UNAVAILABLE") from None
        return {"ok":True,"replayed":False,**_selected(conn,order)}
