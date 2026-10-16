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
