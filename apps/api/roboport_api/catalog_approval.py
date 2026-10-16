"""Synthetic catalog: exact-match lookup and two-role approval without guessed support."""
from uuid import UUID,uuid4
from .session_lookup import session_subject

class CatalogForbidden(Exception):
    pass
class CatalogConflict(Exception):
    pass

def lookup_model(conn, *, brand, model, variant, dock, region, revision):
    if any(not isinstance(v,str) or not v.strip() or len(v)>128
           for v in (brand,model,variant,dock,region,revision)):
        raise ValueError("invalid exact-model parameters")
    row=conn.execute("""SELECT id,status FROM catalog_model
       WHERE brand=%s AND model=%s AND variant=%s AND dock=%s
       AND region=%s AND revision=%s""",
       (brand,model,variant,dock,region,revision)).fetchone()
    if row is None:
        return None
    return {"id":str(row[0]),"status":row[1]}

def approve_capability(conn,token:str,capability_id:UUID,approve:bool):
    if not isinstance(capability_id,UUID) or type(approve) is not bool:
        raise ValueError("invalid approval")
    actor=session_subject(conn,token)
    if actor is None:
        raise CatalogForbidden()
    # Serialize approval/promotion with one capability row lock.
    row=conn.execute("""SELECT c.status,m.status FROM service_capability c
       JOIN catalog_model m ON m.id=c.model_id
       WHERE c.id=%s FOR UPDATE OF c""",(capability_id,)).fetchone()
    if row is None:
        raise CatalogForbidden()
    roles=conn.execute("""SELECT role_code FROM user_role
       WHERE user_id=%s AND role_code IN ('ENGINEER','MANAGER')
       AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())
       ORDER BY role_code""",(actor,)).fetchall()
    # A dual-role user uses ENGINEER role; distinct actor needed for manager.
    role=("ENGINEER" if any(x[0]=="ENGINEER" for x in roles) else
          "MANAGER" if any(x[0]=="MANAGER" for x in roles) else None)
    if role is None:
        raise CatalogForbidden()
    if row[0]!="CANDIDATE" or row[1]!="SUPPORTED":
        raise CatalogConflict("capability/model not eligible")
    if conn.execute("SELECT id FROM capability_approval WHERE capability_id=%s AND actor_id=%s",
                    (capability_id,actor)).fetchone():
        raise CatalogConflict("duplicate reviewer")
    conn.execute("""INSERT INTO capability_approval(id,capability_id,actor_id,role,approved)
                    VALUES(%s,%s,%s,%s,%s)""",(uuid4(),capability_id,actor,role,approve))
    conn.execute("""INSERT INTO catalog_audit(id,capability_id,actor_id,action)
                    VALUES (%s,%s,%s,%s)""",
                 (uuid4(),capability_id,actor,"approve" if approve else "reject"))
    accepted=conn.execute("""SELECT
      EXISTS(SELECT 1 FROM capability_approval a WHERE a.capability_id=%s AND a.role='ENGINEER' AND a.approved),
      EXISTS(SELECT 1 FROM capability_approval a WHERE a.capability_id=%s AND a.role='MANAGER' AND a.approved),
      EXISTS(SELECT 1 FROM capability_approval a WHERE a.capability_id=%s AND NOT a.approved)
    """,(capability_id,capability_id,capability_id)).fetchone()
    if accepted[2]:
        conn.execute("UPDATE service_capability SET status='UNSUPPORTED' WHERE id=%s",
                     (capability_id,))
        status="UNSUPPORTED"
    elif accepted[0] and accepted[1]:
        conn.execute("UPDATE service_capability SET status='SUPPORTED' WHERE id=%s",
                     (capability_id,))
        status="SUPPORTED"
    else:
        status="CANDIDATE"
    return {"id":str(capability_id),"status":status}


def approve_part(conn,token:str,part_id:UUID,approved:bool):
    """An exact part/model pair needs independent staff decisions."""
    if not isinstance(part_id,UUID) or type(approved) is not bool:
        raise ValueError("invalid part review")
    actor=session_subject(conn,token)
    if actor is None:
        raise CatalogForbidden()
    row=conn.execute("""SELECT p.status,m.status
        FROM part_compatibility p JOIN catalog_model m ON m.id=p.model_id
        WHERE p.id=%s AND p.revoked_at IS NULL FOR UPDATE OF p""",(part_id,)).fetchone()
    if row is None:
        raise CatalogForbidden()
    if row[0]!="CANDIDATE" or row[1]!="SUPPORTED":
        raise CatalogConflict("part/model not eligible")
    roles=conn.execute("""SELECT role_code FROM user_role WHERE user_id=%s
       AND role_code IN ('ENGINEER','MANAGER') AND revoked_at IS NULL
       AND (expires_at IS NULL OR expires_at>now())""",(actor,)).fetchall()
    role=("ENGINEER" if any(x[0]=="ENGINEER" for x in roles) else
          "MANAGER" if any(x[0]=="MANAGER" for x in roles) else None)
    if role is None:
        raise CatalogForbidden()
    if conn.execute("""SELECT 1 FROM part_compatibility_approval
        WHERE compatibility_id=%s AND actor_id=%s""",(part_id,actor)).fetchone():
        raise CatalogConflict("duplicate part reviewer")
    conn.execute("""INSERT INTO part_compatibility_approval
      (id,compatibility_id,actor_id,role,approved) VALUES(%s,%s,%s,%s,%s)""",
      (uuid4(),part_id,actor,role,approved))
    # Part audits remain linked to the exact compatibility record through the ledger.
    flags=conn.execute("""SELECT
      EXISTS(SELECT 1 FROM part_compatibility_approval WHERE compatibility_id=%s
        AND role='ENGINEER' AND approved),
      EXISTS(SELECT 1 FROM part_compatibility_approval WHERE compatibility_id=%s
        AND role='MANAGER' AND approved),
      EXISTS(SELECT 1 FROM part_compatibility_approval WHERE compatibility_id=%s
        AND NOT approved)""",(part_id,part_id,part_id)).fetchone()
    status=("UNSUPPORTED" if flags[2] else "SUPPORTED" if flags[0] and flags[1] else "CANDIDATE")
    if status!="CANDIDATE":
        conn.execute("UPDATE part_compatibility SET status=%s WHERE id=%s",(status,part_id))
    return {"id":str(part_id),"status":status}


def review_idempotent(conn,token:str,target:UUID,kind:str,approved:bool,key:str):
    """Successful reviewer replay in one PostgreSQL transaction, live roles rechecked."""
    import hashlib,json
    if (not isinstance(target,UUID) or kind not in ('capability','part')
            or type(approved) is not bool or not isinstance(key,str)
            or not 1<=len(key)<=128 or not key.isascii() or not key.isprintable()):
        raise ValueError("invalid review or idempotency key")
    actor=session_subject(conn,token)
    if actor is None:
        raise CatalogForbidden()
    # Cross-resource identical keys serialize and conflict by actor, not object.
    conn.execute("SELECT id FROM app_user WHERE id=%s FOR UPDATE",(actor,))
    # Replay does not grant permissions after a role has been revoked.
    live=conn.execute("""SELECT EXISTS(SELECT 1 FROM user_role r
       WHERE r.user_id=%s AND r.role_code IN ('ENGINEER','MANAGER')
        AND r.revoked_at IS NULL AND (r.expires_at IS NULL OR r.expires_at>now()))""",
       (actor,)).fetchone()[0]
    if not live:
        raise CatalogForbidden()
    fingerprint=hashlib.sha256(json.dumps(
        {"kind":kind,"target":str(target),"approved":approved},
        sort_keys=True,separators=(",",":")).encode()).hexdigest()
    old=conn.execute("""SELECT fingerprint,response FROM catalog_review_receipt
      WHERE actor_id=%s AND request_key=%s""",(actor,key)).fetchone()
    if old:
        if old[0]!=fingerprint:
            raise CatalogConflict("idempotency key mismatch")
        # Re-evaluate resource visibility on replay, not only the session.
        table="service_capability" if kind=="capability" else "part_compatibility"
        if conn.execute("SELECT 1 FROM "+table+" WHERE id=%s",(target,)).fetchone() is None:
            raise CatalogForbidden()
        approval_table=("capability_approval" if kind=="capability"
                        else "part_compatibility_approval")
        fk=("capability_id" if kind=="capability" else "compatibility_id")
        prior=conn.execute("SELECT role FROM "+approval_table+
                           " WHERE "+fk+"=%s AND actor_id=%s",(target,actor)).fetchone()
        if prior is None or not conn.execute("""SELECT EXISTS(
          SELECT 1 FROM user_role WHERE user_id=%s AND role_code=%s
            AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())
        )""",(actor,prior[0])).fetchone()[0]:
            raise CatalogForbidden()
        return old[1]
    result=(approve_capability(conn,token,target,approved) if kind=="capability"
            else approve_part(conn,token,target,approved))
    conn.execute("""INSERT INTO catalog_review_receipt(actor_id,request_key,fingerprint,response)
                    VALUES(%s,%s,%s,%s::jsonb)""",
                 (actor,key,fingerprint,json.dumps(result)))
    return result
