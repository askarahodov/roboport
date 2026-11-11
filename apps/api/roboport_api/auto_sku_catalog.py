"""Synthetic SKU catalog: protected versions, analog graph and logical (non-FK) dedupe.

No OEM fitment authority, no real supplier price, no stock value or production writes.
The branch lock serializes all catalog changes, and each live document ACL is
rechecked before replay as well as a new insert.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import psycopg
from psycopg.types.json import Jsonb

from .auto_document_access import (
    require_document_access, has_document_access, DocumentPolicyDenied,
)
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_catalog_xlsx import (
    clean_row, parse_catalog_xlsx, export_catalog_xlsx,
    CatalogWorkbookInvalid,
)


class CatalogDenied(ValueError):
    pass


class CatalogConflict(ValueError):
    pass


class CatalogInvalid(ValueError):
    pass


def _valid_ids(*values):
    return all(type(x) is UUID for x in values)


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),
                             ensure_ascii=False).encode()).hexdigest()


def _read_scope(conn,token,org,loc):
    if not _valid_ids(org,loc) or type(token) is not str or not token:
        raise CatalogDenied("FORBIDDEN")
    try:
        cutoff=require_document_access(conn,token,org,loc,
            ("WAREHOUSE","MANAGER"),"SUPPLIER_ORDER","READ")
    except DocumentPolicyDenied:
        raise CatalogDenied("FORBIDDEN") from None
    purchase=has_document_access(conn,token,org,loc,
        ("WAREHOUSE","MANAGER"),"SUPPLIER_ORDER","PRICE_PURCHASE")
    # ESTIMATE.READ may carry a stricter independent history cutoff.
    # A boolean price grant alone does not authorize older sale prices.
    sale=False
    sale_cutoff=None
    if has_document_access(conn,token,org,loc,
                           ("MANAGER","WAREHOUSE"),"ESTIMATE","PRICE_SALES"):
        try:
            sale_cutoff=require_document_access(conn,token,org,loc,
                ("MANAGER","WAREHOUSE"),"ESTIMATE","READ")
            sale=True
        except DocumentPolicyDenied:
            sale=False
    return cutoff,purchase,sale,sale_cutoff


def _write_scope(conn,token,org,loc,cap):
    if not _valid_ids(org,loc) or cap not in ("CREATE","EDIT","CLOSE"):
        raise CatalogInvalid("INVALID_INPUT")
    # Lock the organization *before* session/ACL shared locks. Concurrent
    # catalog mutations and ADMIN policy publications cannot deadlock by
    # attempting a SHARE→UPDATE lock promotion on the same organization.
    if conn.execute("""SELECT 1 FROM auto_organization
        WHERE id=%s AND disabled_at IS NULL FOR UPDATE""",(org,)).fetchone() is None:
        raise CatalogDenied("FORBIDDEN")
    try:
        require_document_access(conn,token,org,loc,("WAREHOUSE","MANAGER"),
                                "SUPPLIER_ORDER",cap)
        require_document_access(conn,token,org,loc,("WAREHOUSE","MANAGER"),
                                "SUPPLIER_ORDER","PRICE_PURCHASE")
        require_document_access(conn,token,org,loc,("MANAGER","WAREHOUSE"),
                                "ESTIMATE","EDIT")
        require_document_access(conn,token,org,loc,("MANAGER","WAREHOUSE"),
                                "ESTIMATE","PRICE_SALES")
    except DocumentPolicyDenied:
        raise CatalogDenied("FORBIDDEN") from None
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None:
        raise CatalogDenied("FORBIDDEN")
    conn.execute("SELECT set_config('roboport.catalog_actor',%s,true)",(str(actor),))
    return actor


def _require_catalog_edit(conn,token,org,loc):
    # Import CREATE authority cannot bypass explicit EDIT deny when an
    # existing SKU receives a new price version. Recheck before replays too.
    try:
        require_document_access(conn,token,org,loc,("WAREHOUSE","MANAGER"),
                                "SUPPLIER_ORDER","EDIT")
    except DocumentPolicyDenied:
        raise CatalogDenied("FORBIDDEN") from None


def _sku_info(conn,org,loc,article):
    row=conn.execute("""SELECT s.id,p.category_code,p.uom,p.barcode,p.brand,
        COALESCE((SELECT max(version) FROM auto_catalog_price_event
                  WHERE sku_id=s.id),0)::integer
        FROM auto_stock_sku s LEFT JOIN auto_catalog_profile p ON p.sku_id=s.id
        WHERE s.organization_id=%s AND s.location_id=%s AND s.article=%s
        FOR UPDATE OF s""",(org,loc,article)).fetchone()
    if row and conn.execute(
        "SELECT 1 FROM auto_catalog_merge WHERE source_sku_id=%s",(row[0],)
    ).fetchone():
        raise CatalogConflict("MERGED_SKU")
    return row


def _row_matches(info,row):
    return info[1] is None or (
        info[1:5]==(row["category"],row["uom"],row["barcode"],row["brand"])
    )


def _prepare_snapshots(conn,org,loc,rows):
    snapshots=[]
    for row in rows:
        info=_sku_info(conn,org,loc,row["article"])
        if info and not _row_matches(info,row):
            raise CatalogConflict("IMMUTABLE_PROFILE")
        snapshots.append({"article":row["article"],
             "skuId":str(info[0]) if info else None,
             "version":info[5] if info else 0})
    requested={row["article"]:row for row in rows}
    for row in rows:
        target=row["analogArticle"]
        if not target:
            continue
        if target in requested:
            matching=requested[target]
            if (matching["category"],matching["uom"])!=(
                row["category"],row["uom"]):
                raise CatalogConflict("ANALOG_INCOMPATIBLE")
        else:
            linked=conn.execute("""SELECT p.category_code,p.uom
                FROM auto_stock_sku s JOIN auto_catalog_profile p ON p.sku_id=s.id
                WHERE s.organization_id=%s AND s.location_id=%s AND s.article=%s
                  AND NOT EXISTS(SELECT 1 FROM auto_catalog_merge m
                                 WHERE m.source_sku_id=s.id)
                FOR SHARE OF s,p""",(org,loc,target)).fetchone()
            if not linked or linked!=(row["category"],row["uom"]):
                raise CatalogConflict("ANALOG_UNKNOWN")
    return snapshots


def list_catalog(conn,token,org,loc,limit=30,after=None):
    if (not _valid_ids(org,loc) or type(limit) is not int
        or not 1<=limit<=50 or (after is not None and type(after) is not UUID)):
        raise CatalogInvalid("INVALID_INPUT")
    with conn.transaction():
        cutoff,purchase,sale,sale_cutoff=_read_scope(conn,token,org,loc)
        rows=conn.execute("""
          SELECT s.id,s.article,p.category_code,p.uom,p.barcode,p.brand,
            p.created_at,e.version,e.purchase_minor,e.sale_minor,e.created_at,
            m.target_sku_id
          FROM auto_stock_sku s
          JOIN auto_organization org ON org.id=s.organization_id
          JOIN auto_service_location loc ON loc.id=s.location_id
             AND loc.organization_id=s.organization_id
          LEFT JOIN auto_catalog_profile p ON p.sku_id=s.id
          LEFT JOIN LATERAL (
            SELECT version,purchase_minor,sale_minor,created_at
            FROM auto_catalog_price_event WHERE sku_id=s.id
            ORDER BY version DESC LIMIT 1
          ) e ON TRUE
          LEFT JOIN auto_catalog_merge m ON m.source_sku_id=s.id
          WHERE s.organization_id=%s AND s.location_id=%s
            AND org.disabled_at IS NULL AND loc.disabled_at IS NULL
            AND (%s::uuid IS NULL OR s.id>%s::uuid)
            AND (%s::timestamptz IS NULL OR s.created_at>=%s::timestamptz)
          ORDER BY s.id LIMIT %s
        """,(org,loc,after,after,cutoff,cutoff,limit+1)).fetchall()
        nxt=rows[limit-1][0] if len(rows)>limit else None
        values=[]
        for ident,article,category,uom,barcode,brand,created,version,buy,sell,priced,merged in rows[:limit]:
            current_price=cutoff is None or (priced is not None and priced>=cutoff)
            values.append({"skuId":str(ident),"article":article,
                "category":category,"uom":uom,"barcode":barcode,"brand":brand,
                "version":version or 0,
                "purchaseMinor":buy if purchase and current_price else None,
                "saleMinor":sell if sale and current_price and
                    (sale_cutoff is None or priced is not None and priced>=sale_cutoff)
                    else None,
                "purchaseHidden":not purchase or not current_price,
                "saleHidden":not sale or not current_price or
                    (sale_cutoff is not None and (priced is None or priced<sale_cutoff)),
                "mergedInto":str(merged) if merged else None})
        return {"syntheticOnly":True,"items":values,
                "nextSkuId":str(nxt) if nxt else None}


def _batch_replay(conn,request_key,actor,org,loc,source,digest):
    row=conn.execute("""SELECT id,source,payload_digest,created_by,
        organization_id,location_id
        FROM auto_catalog_price_batch WHERE request_key=%s""",
        (request_key,)).fetchone()
    if row is None:
        return None
    if row[1:]!=(source,digest,actor,org,loc):
        raise CatalogConflict("REPLAY_SCOPE_OR_PAYLOAD_MISMATCH")
    events=conn.execute("""SELECT sku_id,version FROM auto_catalog_price_event
        WHERE batch_id=%s ORDER BY sku_id""",(row[0],)).fetchall()
    return {"batchId":str(row[0]),"items":[
        {"skuId":str(sku),"version":version} for sku,version in events],
        "replayed":True,"syntheticOnly":True}


def _new_batch(conn,request_key,org,loc,actor,source,digest):
    ident=uuid4()
    conn.execute("""INSERT INTO auto_catalog_price_batch
        (id,request_key,organization_id,location_id,payload_digest,source,created_by)
        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (ident,request_key,org,loc,digest,source,actor))
    return ident


def _event(conn,batch,org,loc,actor,sku,category,version,buy,sell):
    conn.execute("""INSERT INTO auto_catalog_price_event
        (id,batch_id,sku_id,organization_id,location_id,category_code,
         version,purchase_minor,sale_minor,created_by)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (uuid4(),batch,sku,org,loc,category,version,buy,sell,actor))


def _profile(conn,sku,org,loc,actor,row):
    old=conn.execute("""SELECT category_code,uom,barcode,brand
       FROM auto_catalog_profile WHERE sku_id=%s""",(sku,)).fetchone()
    expected=(row["category"],row["uom"],row["barcode"],row["brand"])
    if old is not None:
        if old!=expected:
            raise CatalogConflict("IMMUTABLE_PROFILE")
        return
    conn.execute("""INSERT INTO auto_catalog_profile(
       sku_id,organization_id,location_id,category_code,uom,barcode,brand,created_by)
       VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
       (sku,org,loc,*expected,actor))


def create_catalog_sku(conn,token,org,loc,key,row):
    if not _valid_ids(org,loc,key):
        raise CatalogInvalid("INVALID_INPUT")
    try:
        normalized=clean_row([row[x] or "" if x not in ("purchaseMinor","saleMinor")
                              else str(row[x]) for x in (
            "article","category","uom","barcode","brand",
            "purchaseMinor","saleMinor","analogArticle")])
    except (KeyError,TypeError,ValueError,CatalogWorkbookInvalid):
        raise CatalogInvalid("INVALID_INPUT") from None
    if normalized["analogArticle"]:
        raise CatalogInvalid("CREATE_ANALOG_SEPARATELY")
    digest=_digest(normalized)
    with conn.transaction():
        actor=_write_scope(conn,token,org,loc,"CREATE")
        previous=_batch_replay(conn,key,actor,org,loc,"CREATE",digest)
        if previous:
            return previous
        if _sku_info(conn,org,loc,normalized["article"]) is not None:
            raise CatalogConflict("ARTICLE_EXISTS")
        sku=uuid4()
        conn.execute("""INSERT INTO auto_stock_sku(id,organization_id,location_id,article)
            VALUES(%s,%s,%s,%s)""",(sku,org,loc,normalized["article"]))
        _profile(conn,sku,org,loc,actor,normalized)
        batch=_new_batch(conn,key,org,loc,actor,"CREATE",digest)
        _event(conn,batch,org,loc,actor,sku,normalized["category"],1,
               normalized["purchaseMinor"],normalized["saleMinor"])
        return {"batchId":str(batch),
                "items":[{"skuId":str(sku),"version":1}],
                "replayed":False,"syntheticOnly":True}


def bulk_prices(conn,token,org,loc,key,entries):
    if (not _valid_ids(org,loc,key) or type(entries) is not list or
        not 1<=len(entries)<=100 or any(
          type(row) is not dict or set(row)!={"skuId","expectedVersion",
                "purchaseMinor","saleMinor"} or
          type(row["skuId"]) is not UUID or
          type(row["expectedVersion"]) is not int or
          not 1<=row["expectedVersion"]<=1_000_000_000 or
          any(type(row[k]) is not int or not 0<=row[k]<=1_000_000_000
              for k in ("purchaseMinor","saleMinor"))
          for row in entries)):
        raise CatalogInvalid("INVALID_INPUT")
    ids=[row["skuId"] for row in entries]
    if len(set(ids))!=len(ids):
        raise CatalogInvalid("DUPLICATE_SKU")
    ordered=sorted(entries,key=lambda x:str(x["skuId"]))
    digest=_digest([{**x,"skuId":str(x["skuId"])} for x in ordered])
    with conn.transaction():
        actor=_write_scope(conn,token,org,loc,"EDIT")
        old=_batch_replay(conn,key,actor,org,loc,"MANUAL",digest)
        if old:
            return old
        for row in ordered:
            sku=row["skuId"]
            info=conn.execute("""SELECT p.category_code,
                COALESCE((SELECT max(e.version) FROM auto_catalog_price_event e
                          WHERE e.sku_id=p.sku_id),0)
                FROM auto_stock_sku s
                JOIN auto_catalog_profile p ON p.sku_id=s.id
                WHERE s.id=%s AND s.organization_id=%s AND s.location_id=%s
                  AND NOT EXISTS(SELECT 1 FROM auto_catalog_merge m
                                 WHERE m.source_sku_id=s.id)
                FOR UPDATE OF s""",(sku,org,loc)).fetchone()
            if info is None:
                raise CatalogDenied("FORBIDDEN")
            if info[1]!=row["expectedVersion"]:
                raise CatalogConflict("STALE_VERSION")
        batch=_new_batch(conn,key,org,loc,actor,"MANUAL",digest)
        for row in ordered:
            sku=row["skuId"]
            category=conn.execute(
                "SELECT category_code FROM auto_catalog_profile WHERE sku_id=%s",
                (sku,)).fetchone()[0]
            _event(conn,batch,org,loc,actor,sku,category,
                   row["expectedVersion"]+1,row["purchaseMinor"],row["saleMinor"])
        return {"batchId":str(batch),"items":[
            {"skuId":str(x["skuId"]),"version":x["expectedVersion"]+1}
            for x in ordered],"replayed":False,"syntheticOnly":True}


def link_analog(conn,token,org,loc,key,left,right,note):
    if (not _valid_ids(org,loc,key,left,right) or left==right or
        type(note) is not str or not 3<=len(note)<=160 or note!=note.strip()):
        raise CatalogInvalid("INVALID_INPUT")
    left,right=sorted((left,right))
    with conn.transaction():
        actor=_write_scope(conn,token,org,loc,"CREATE")
        prior=conn.execute("""SELECT id,organization_id,location_id,
             left_sku_id,right_sku_id,evidence_note,created_by
          FROM auto_catalog_analog WHERE request_key=%s""",(key,)).fetchone()
        if prior:
            if prior[1:]!=(org,loc,left,right,note,actor):
                raise CatalogConflict("REPLAY_MISMATCH")
            return {"analogId":str(prior[0]),"replayed":True,"syntheticOnly":True}
        ident=uuid4()
        try:
            with conn.transaction():
                conn.execute("""INSERT INTO auto_catalog_analog(
                    id,request_key,organization_id,location_id,left_sku_id,
                    right_sku_id,evidence_note,created_by)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (ident,key,org,loc,left,right,note,actor))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise CatalogConflict("ANALOG_INCOMPATIBLE") from None
        return {"analogId":str(ident),"replayed":False,"syntheticOnly":True}


def logical_merge(conn,token,org,loc,key,source,target):
    if not _valid_ids(org,loc,key,source,target) or source==target:
        raise CatalogInvalid("INVALID_INPUT")
    with conn.transaction():
        actor=_write_scope(conn,token,org,loc,"CLOSE")
        prior=conn.execute("""SELECT id,organization_id,location_id,
            source_sku_id,target_sku_id,created_by FROM auto_catalog_merge
            WHERE request_key=%s""",(key,)).fetchone()
        if prior:
            if prior[1:]!=(org,loc,source,target,actor):
                raise CatalogConflict("REPLAY_MISMATCH")
            return {"mergeId":str(prior[0]),"replayed":True,"syntheticOnly":True}
        ident=uuid4()
        try:
            with conn.transaction():
                conn.execute("""INSERT INTO auto_catalog_merge(
                   id,request_key,organization_id,location_id,
                   source_sku_id,target_sku_id,created_by)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (ident,key,org,loc,source,target,actor))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise CatalogConflict("UNSAFE_MERGE") from None
        return {"mergeId":str(ident),"replayed":False,
                "historicalFksRewritten":False,"syntheticOnly":True}


def catalog_preview(conn,token,org,loc,key,raw):
    if not _valid_ids(org,loc,key):
        raise CatalogInvalid("INVALID_INPUT")
    try:
        rows=parse_catalog_xlsx(raw)
    except CatalogWorkbookInvalid:
        raise CatalogInvalid("WORKBOOK") from None
    digest=sha256(raw).hexdigest()
    with conn.transaction():
        actor=_write_scope(conn,token,org,loc,"CREATE")
        old=conn.execute("""SELECT id,source_sha256,staged_rows,snapshots,created_by,
             expires_at,organization_id,location_id
             FROM auto_catalog_import_preview WHERE request_key=%s""",
             (key,)).fetchone()
        if old:
            if (old[1]!=digest or old[2]!=rows or old[4]!=actor or
                old[5]<=datetime.now(timezone.utc) or
                old[6:]!=(org,loc)):
                raise CatalogConflict("PREVIEW_REPLAY_MISMATCH")
            if any(item["skuId"] is not None for item in old[3]):
                _require_catalog_edit(conn,token,org,loc)
            return {"previewId":str(old[0]),"sha256":digest,
                    "items":old[3],"replayed":True,"syntheticOnly":True}
        snapshots=_prepare_snapshots(conn,org,loc,rows)
        if any(item["skuId"] is not None for item in snapshots):
            _require_catalog_edit(conn,token,org,loc)
        ident=uuid4()
        conn.execute("""INSERT INTO auto_catalog_import_preview(
            id,request_key,organization_id,location_id,source_sha256,
            staged_rows,snapshots,created_by,expires_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+interval '15 minutes')""",
            (ident,key,org,loc,digest,Jsonb(rows),Jsonb(snapshots),actor))
        return {"previewId":str(ident),"sha256":digest,"items":snapshots,
                "replayed":False,"syntheticOnly":True}


def catalog_commit(conn,token,org,loc,key,preview_id,digest):
    if (not _valid_ids(org,loc,key,preview_id) or
        type(digest) is not str or len(digest)!=64 or
        any(ch not in "0123456789abcdef" for ch in digest)):
        raise CatalogInvalid("INVALID_INPUT")
    with conn.transaction():
        actor=_write_scope(conn,token,org,loc,"CREATE")
        # This must precede idempotent replay: an EDIT revoke also blocks
        # already-committed XLSX price updates from being acknowledged.
        referenced=conn.execute("""SELECT snapshots FROM auto_catalog_import_preview
             WHERE id=%s AND organization_id=%s AND location_id=%s
               AND created_by=%s FOR SHARE""",(preview_id,org,loc,actor)).fetchone()
        if referenced is None:
            raise CatalogDenied("FORBIDDEN")
        if any(item["skuId"] is not None for item in referenced[0]):
            _require_catalog_edit(conn,token,org,loc)
        prior=conn.execute("""SELECT id,preview_id,source_sha256,created_by,
            organization_id,location_id
            FROM auto_catalog_import_commit WHERE request_key=%s""",
            (key,)).fetchone()
        if prior:
            if prior[1:]!=(preview_id,digest,actor,org,loc):
                raise CatalogConflict("REPLAY_MISMATCH")
            return {"commitId":str(prior[0]),"replayed":True,
                    "syntheticOnly":True}
        preview=conn.execute("""SELECT source_sha256,staged_rows,snapshots,
             expires_at FROM auto_catalog_import_preview
             WHERE id=%s AND organization_id=%s AND location_id=%s
               AND created_by=%s FOR SHARE""",
             (preview_id,org,loc,actor)).fetchone()
        if not preview:
            raise CatalogDenied("FORBIDDEN")
        if preview[0]!=digest or preview[3]<=datetime.now(timezone.utc):
            raise CatalogConflict("EXPIRED_OR_CHANGED_PREVIEW")
        if conn.execute(
            "SELECT 1 FROM auto_catalog_import_commit WHERE preview_id=%s",
            (preview_id,)).fetchone():
            raise CatalogConflict("PREVIEW_ALREADY_COMMITTED")
        rows=preview[1]
        if _prepare_snapshots(conn,org,loc,rows)!=preview[2]:
            raise CatalogConflict("STALE_PREVIEW")
        batch=_new_batch(conn,key,org,loc,actor,"XLSX",_digest(rows))
        sku_map={}
        # _prepare_snapshots locked existing SKUs. Stable article order
        # avoids deadlocks with manual batches and retains historical IDs.
        for row in sorted(rows,key=lambda x:x["article"]):
            old=_sku_info(conn,org,loc,row["article"])
            if old:
                sku,version=old[0],old[5]
            else:
                sku,version=uuid4(),0
                conn.execute("""INSERT INTO auto_stock_sku
                    (id,organization_id,location_id,article)
                    VALUES(%s,%s,%s,%s)""",(sku,org,loc,row["article"]))
            _profile(conn,sku,org,loc,actor,row)
            _event(conn,batch,org,loc,actor,sku,row["category"],version+1,
                row["purchaseMinor"],row["saleMinor"])
            sku_map[row["article"]]=sku
        for row in rows:
            other=row["analogArticle"]
            if not other:
                continue
            target=sku_map.get(other)
            if target is None:
                existing=conn.execute("""SELECT id FROM auto_stock_sku
                    WHERE organization_id=%s AND location_id=%s AND article=%s""",
                    (org,loc,other)).fetchone()
                if not existing:
                    raise CatalogConflict("MISSING_ANALOG")
                target=existing[0]
            left,right=sorted((sku_map[row["article"]],target))
            if conn.execute("""SELECT 1 FROM auto_catalog_analog
                WHERE organization_id=%s AND location_id=%s
                  AND left_sku_id=%s AND right_sku_id=%s""",
                (org,loc,left,right)).fetchone():
                continue
            conn.execute("""INSERT INTO auto_catalog_analog(
                id,request_key,organization_id,location_id,
                left_sku_id,right_sku_id,evidence_note,created_by)
                VALUES(%s,%s,%s,%s,%s,%s,'OFFLINE_XLSX_UNVERIFIED',%s)""",
                (uuid4(),uuid5(NAMESPACE_URL,f"{preview_id}:{left}:{right}"),
                 org,loc,left,right,actor))
        commit=uuid4()
        conn.execute("""INSERT INTO auto_catalog_import_commit(
            id,request_key,preview_id,organization_id,location_id,
            source_sha256,created_by)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (commit,key,preview_id,org,loc,digest,actor))
        return {"commitId":str(commit),"batchId":str(batch),
                "rowsWritten":len(rows),"replayed":False,"syntheticOnly":True}


def catalog_xlsx_export(conn,token,org,loc):
    with conn.transaction():
        cutoff,purchase,sale,sale_cutoff=_read_scope(conn,token,org,loc)
        # No partial export that silently leaks denied historical/price data.
        if cutoff is not None or sale_cutoff is not None or not purchase or not sale:
            raise CatalogDenied("EXPORT_NEEDS_FULL_PRICE_RIGHTS")
        rows=conn.execute("""SELECT s.article,p.category_code,p.uom,
                p.barcode,p.brand,e.purchase_minor,e.sale_minor,
                (SELECT other.article
                  FROM auto_catalog_analog a
                  JOIN auto_stock_sku other ON other.id=
                     CASE WHEN a.left_sku_id=s.id THEN a.right_sku_id ELSE a.left_sku_id END
                  WHERE (a.left_sku_id=s.id OR a.right_sku_id=s.id)
                    AND NOT EXISTS(SELECT 1 FROM auto_catalog_merge merged
                                   WHERE merged.source_sku_id=other.id)
                  ORDER BY other.article LIMIT 1) AS analog
            FROM auto_stock_sku s
            JOIN auto_catalog_profile p ON p.sku_id=s.id
            JOIN LATERAL(
              SELECT purchase_minor,sale_minor FROM auto_catalog_price_event
              WHERE sku_id=s.id ORDER BY version DESC LIMIT 1
            ) e ON TRUE
            JOIN auto_service_location l ON l.id=s.location_id
              AND l.organization_id=s.organization_id
            JOIN auto_organization o ON o.id=s.organization_id
            WHERE s.organization_id=%s AND s.location_id=%s
              AND l.disabled_at IS NULL AND o.disabled_at IS NULL
              AND NOT EXISTS(SELECT 1 FROM auto_catalog_merge m
                             WHERE m.source_sku_id=s.id)
            ORDER BY s.article LIMIT 10001""",(org,loc)).fetchall()
        if len(rows)>10000:
            raise CatalogConflict("EXPORT_CAP")
        return export_catalog_xlsx([{"article":a,"category":c,"uom":u,
          "barcode":b,"brand":brand,"purchaseMinor":buy,"saleMinor":sell,
          "analogArticle":analog} for a,c,u,b,brand,buy,sell,analog in rows])
