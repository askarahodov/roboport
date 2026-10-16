"""Exact synthetic catalog with model-scoped two-person approval and evidence."""
import hashlib
import json
from uuid import UUID,uuid4
from .session_lookup import session_subject


class CatalogForbidden(Exception):
    pass


class CatalogConflict(Exception):
    pass


def _role(conn,token,model_id,action):
    actor=session_subject(conn,token)
    if actor is None:
        raise CatalogForbidden()
    row=conn.execute("""SELECT r.role_code FROM user_role r
       JOIN catalog_grant g ON g.user_id=r.user_id AND g.model_id=%s
         AND g.action=%s AND g.revoked_at IS NULL
         AND (g.expires_at IS NULL OR g.expires_at>now())
       WHERE r.user_id=%s AND r.role_code IN ('ENGINEER','MANAGER')
         AND r.revoked_at IS NULL AND (r.expires_at IS NULL OR r.expires_at>now())
       ORDER BY CASE WHEN r.role_code='MANAGER' THEN 0 ELSE 1 END LIMIT 1""",
       (model_id,action,actor)).fetchone()
    if row is None:
        raise CatalogForbidden()
    return actor,row[0]


def _receipt(conn,actor,key,intent,callback):
    if not isinstance(key,str) or not 1<=len(key)<=128 or not key.isascii() or not key.isprintable():
        raise ValueError("invalid idempotency key")
    conn.execute("SELECT id FROM app_user WHERE id=%s FOR UPDATE",(actor,))
    fingerprint=hashlib.sha256(json.dumps(intent,sort_keys=True,
                                          separators=(",",":")).encode()).hexdigest()
    previous=conn.execute("""SELECT fingerprint,response FROM catalog_receipt
                              WHERE actor_id=%s AND request_key=%s""",(actor,key)).fetchone()
    if previous:
        if previous[0]!=fingerprint:
            raise CatalogConflict("idempotency key conflict")
        return previous[1]
    result=callback()
    conn.execute("""INSERT INTO catalog_receipt(actor_id,request_key,fingerprint,response)
                    VALUES (%s,%s,%s,%s::jsonb)""",
                 (actor,key,fingerprint,json.dumps(result)))
    return result


def review_capability(conn,token,variant:UUID,operation:str,evidence:UUID,
                      approved:bool,key:str):
    if (not isinstance(variant,UUID) or not isinstance(evidence,UUID)
            or not isinstance(operation,str) or not 1<=len(operation)<=128
            or type(approved) is not bool):
        raise ValueError("invalid capability review")
    variant_info=conn.execute("SELECT model_id FROM catalog_variant WHERE id=%s",(variant,)).fetchone()
    if variant_info is None:
        raise CatalogForbidden()
    model=variant_info[0]
    actor,role=_role(conn,token,model,"capability.review")

    def apply():
        conn.execute("SELECT id FROM catalog_variant WHERE id=%s FOR UPDATE",(variant,))
        evidence_row=conn.execute("SELECT id FROM catalog_evidence WHERE id=%s",(evidence,)).fetchone()
        if evidence_row is None:
            raise CatalogConflict("verified evidence required")
        row=conn.execute("""SELECT id,evidence_id,status FROM service_capability
          WHERE variant_id=%s AND operation_code=%s FOR UPDATE""",(variant,operation)).fetchone()
        if row is None:
            cid=uuid4()
            conn.execute("""INSERT INTO service_capability
                (id,variant_id,operation_code,evidence_id) VALUES (%s,%s,%s,%s)""",
                         (cid,variant,operation,evidence))
        else:
            cid=row[0]
            if row[1]!=evidence or row[2]!="candidate":
                raise CatalogConflict("capability already decided or evidence differs")
        if conn.execute("""SELECT 1 FROM catalog_capability_review
          WHERE capability_id=%s AND (reviewer_id=%s OR role_code=%s)""",
                        (cid,actor,role)).fetchone():
            raise CatalogConflict("independent reviewer required")
        conn.execute("""INSERT INTO catalog_capability_review
          (id,capability_id,reviewer_id,role_code,approved)
          VALUES (%s,%s,%s,%s,%s)""",(uuid4(),cid,actor,role,approved))
        accepted=conn.execute("""SELECT count(*) FROM catalog_capability_review
                 WHERE capability_id=%s AND approved=true""",(cid,)).fetchone()[0]
        status="candidate"
        if accepted==2:
            conn.execute("UPDATE service_capability SET status='approved' WHERE id=%s",(cid,))
            status="approved"
        conn.execute("INSERT INTO audit_event(id,action) VALUES (%s,%s)",
                     (uuid4(),"catalog.capability.review"))
        return {"id":str(cid),"status":status}
    return _receipt(conn,actor,key,{"kind":"capability","variant":str(variant),
            "operation":operation,"evidence":str(evidence),"approved":approved},apply)


def review_part_compatibility(conn,token,part:UUID,variant:UUID,evidence:UUID,
                              desired:str,approved:bool,key:str):
    if (not isinstance(part,UUID) or not isinstance(variant,UUID)
            or not isinstance(evidence,UUID) or type(approved) is not bool
            or desired not in ("manufacturer_documented","technician_tested")):
        raise ValueError("invalid compatibility intent")
    variant_info=conn.execute("SELECT model_id FROM catalog_variant WHERE id=%s",(variant,)).fetchone()
    if variant_info is None:
        raise CatalogForbidden()
    actor,role=_role(conn,token,variant_info[0],"compatibility.review")
    def apply():
        conn.execute("SELECT id FROM catalog_variant WHERE id=%s FOR UPDATE",(variant,))
        part_row=conn.execute("SELECT id FROM catalog_part WHERE id=%s",(part,)).fetchone()
        evidence_row=conn.execute("SELECT id FROM catalog_evidence WHERE id=%s",(evidence,)).fetchone()
        if not part_row or not evidence_row:
            raise CatalogConflict("unknown part or evidence")
        row=conn.execute("""SELECT id,evidence_id,status,requested_status FROM part_compatibility
             WHERE part_id=%s AND variant_id=%s FOR UPDATE""",(part,variant)).fetchone()
        if row is None:
            cid=uuid4()
            conn.execute("""INSERT INTO part_compatibility
                 (id,part_id,variant_id,evidence_id,requested_status)
                 VALUES (%s,%s,%s,%s,%s)""",(cid,part,variant,evidence,desired))
        else:
            cid=row[0]
            if row[1]!=evidence or row[2]!="unverified" or row[3]!=desired:
                raise CatalogConflict("compatibility already decided or evidence differs")
        if conn.execute("""SELECT 1 FROM catalog_compatibility_review
           WHERE compatibility_id=%s AND (reviewer_id=%s OR role_code=%s)""",
                        (cid,actor,role)).fetchone():
            raise CatalogConflict("independent reviewer required")
        conn.execute("""INSERT INTO catalog_compatibility_review
          (id,compatibility_id,reviewer_id,role_code,approved)
          VALUES (%s,%s,%s,%s,%s)""",(uuid4(),cid,actor,role,approved))
        approvals=conn.execute("""SELECT count(*) FROM catalog_compatibility_review
             WHERE compatibility_id=%s AND approved=true""",(cid,)).fetchone()[0]
        status="unverified"
        if approvals==2:
            conn.execute("UPDATE part_compatibility SET status=%s WHERE id=%s",(desired,cid))
            status=desired
        conn.execute("INSERT INTO audit_event(id,action) VALUES (%s,%s)",
                     (uuid4(),"catalog.compatibility.review"))
        return {"id":str(cid),"status":status}
    return _receipt(conn,actor,key,{"kind":"compatibility","part":str(part),
            "variant":str(variant),"evidence":str(evidence),
            "desired":desired,"approved":approved},apply)


def list_brands(conn):
    rows=conn.execute("SELECT id,name FROM catalog_brand ORDER BY name,id").fetchall()
    return {"items":[{"id":str(uid),"name":name} for uid,name in rows]}


def list_models(conn):
    rows=conn.execute("""SELECT m.id,m.brand_id,m.name,m.category,
       EXISTS (SELECT 1 FROM catalog_variant v JOIN service_capability s
         ON s.variant_id=v.id AND s.status='approved' WHERE v.model_id=m.id)
       FROM catalog_model m ORDER BY m.name,m.id""").fetchall()
    return {"items":[{"id":str(uid),"brandId":str(brand),"name":name,
           "category":category,"supportStatus":"supported" if enabled else "candidate"}
           for uid,brand,name,category,enabled in rows]}


def get_model(conn,model:UUID):
    row=conn.execute("""SELECT m.id,m.brand_id,m.name,m.category,
       EXISTS(SELECT 1 FROM catalog_variant v JOIN service_capability s
         ON s.variant_id=v.id AND s.status='approved' WHERE v.model_id=m.id)
       FROM catalog_model m WHERE m.id=%s""",(model,)).fetchone()
    if not row:
        return None
    uid,brand,name,category,enabled=row
    return {"id":str(uid),"brandId":str(brand),"name":name,"category":category,
            "supportStatus":"supported" if enabled else "candidate"}
