"""Synthetic document posting/closure after exact intake and settlement evidence.

Even CLOSED_TECHNICAL has releaseAllowed=False: no real money, legal signatures,
fiscal paperwork, payment capture or physical handover.
"""
from uuid import UUID,uuid4
import psycopg

from .auto_work_order_execution import _scope,ExecutionDenied
from .auto_work_order_estimate import _client_and_scope,AutoEstimateDenied
from .auto_document_access import require_document_access,DocumentPolicyDenied


class DocumentLifecycleDenied(ValueError):
    pass


class DocumentLifecycleInvalid(ValueError):
    pass


class DocumentLifecycleConflict(ValueError):
    pass


ACTIONS=("POSTED","CLIENT_ACK","CLIENT_DISPUTE","CLOSED_TECHNICAL")


def _state(conn,order):
    rows=conn.execute("""
      SELECT id,action,actor_id,preview_id,preview_digest,intake_act_id,
             intake_digest,created_at
      FROM auto_order_demo_document_event WHERE work_order_id=%s
      ORDER BY created_at,id LIMIT 5
    """,(order,)).fetchall()
    if len(rows)>4:
        raise DocumentLifecycleConflict("EVENT_LIMIT")
    events=[{"id":str(ident),"action":action,"actorId":str(actor),
             "previewId":str(preview),"previewDigest":digest,
             "intakeActId":str(act),"intakeDigest":act_digest,
             "createdAt":stamp.isoformat()}
            for ident,action,actor,preview,digest,act,act_digest,stamp in rows]
    actions={item["action"] for item in events}
    status=("CLOSED_TECHNICAL" if "CLOSED_TECHNICAL" in actions else
            "CLIENT_DISPUTED" if "CLIENT_DISPUTE" in actions else
            "CLIENT_ACKNOWLEDGED" if "CLIENT_ACK" in actions else
            "POSTED" if "POSTED" in actions else "DRAFT")
    latest=conn.execute("""
      SELECT act.id,act.version,act.fingerprint,review.decision
      FROM auto_work_order_intake_act act
      LEFT JOIN auto_work_order_intake_act_review review ON review.act_id=act.id
      WHERE act.work_order_id=%s ORDER BY act.version DESC LIMIT 1
    """,(order,)).fetchone()
    intake=None if latest is None else {
        "id":str(latest[0]),"version":latest[1],"digest":latest[2],
        "clientDecision":latest[3]}
    return {"syntheticOnly":True,"releaseAllowed":False,
            "legalSignatureStatus":"NOT_IMPLEMENTED","status":status,
            "intakeAct":intake,"events":events,
            "canReview":status=="POSTED","canClose":status=="CLIENT_ACKNOWLEDGED"}


def record_document_event(conn,token,org,loc,order,*,request_key,action,
                          preview_id,preview_digest,intake_act_id,intake_digest):
    if (any(type(x) is not UUID for x in
            (org,loc,order,request_key,preview_id,intake_act_id)) or
            action not in ACTIONS or
            any(type(d) is not str or len(d)!=64 or
                any(ch not in "0123456789abcdef" for ch in d)
                for d in (preview_digest,intake_digest))):
        raise DocumentLifecycleInvalid("INVALID_INPUT")
    with conn.transaction():
        if action in ("POSTED","CLOSED_TECHNICAL"):
            try:
                actor=_scope(conn,token,org,loc,order,("MANAGER",))
                require_document_access(conn,token,org,loc,("MANAGER",),
                    "HANDOVER","CREATE" if action=="POSTED" else "CLOSE",
                    order=order)
            except (ExecutionDenied,DocumentPolicyDenied):
                raise DocumentLifecycleDenied("FORBIDDEN") from None
        else:
            try:
                actor=_client_and_scope(conn,token,org,loc,order)
            except AutoEstimateDenied:
                raise DocumentLifecycleDenied("FORBIDDEN") from None
        existing=conn.execute("""
           SELECT work_order_id,actor_id,action,preview_id,preview_digest,
                  intake_act_id,intake_digest
           FROM auto_order_demo_document_event WHERE request_key=%s
        """,(request_key,)).fetchone()
        expected=(order,actor,action,preview_id,preview_digest,
                  intake_act_id,intake_digest)
        if existing is not None:
            if existing!=expected:
                raise DocumentLifecycleConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**_state(conn,order)}
        preview=conn.execute("""
           SELECT id,btrim(fingerprint) FROM auto_order_demo_handover_preview
           WHERE work_order_id=%s
        """,(order,)).fetchone()
        if preview is None:
            raise DocumentLifecycleConflict("SEALED_PREVIEW_REQUIRED")
        if preview!=(preview_id,preview_digest):
            raise DocumentLifecycleConflict("STALE_PREVIEW")
        state=_state(conn,order)
        intake=state["intakeAct"]
        if not intake or (intake["clientDecision"]!="ACKNOWLEDGED" or
                          (intake["id"],intake["digest"])!=
                          (str(intake_act_id),intake_digest)):
            raise DocumentLifecycleConflict("ACKNOWLEDGED_INTAKE_REQUIRED")
        if (action=="POSTED" and state["status"]!="DRAFT") or (
            action in ("CLIENT_ACK","CLIENT_DISPUTE") and state["status"]!="POSTED") or (
            action=="CLOSED_TECHNICAL" and state["status"]!="CLIENT_ACKNOWLEDGED"):
            raise DocumentLifecycleConflict("INVALID_TRANSITION")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_order_demo_document_event
                  (id,work_order_id,preview_id,preview_digest,intake_act_id,
                   intake_digest,actor_id,request_key,action)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,(uuid4(),order,preview_id,preview_digest,intake_act_id,
                     intake_digest,actor,request_key,action))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise DocumentLifecycleConflict("DOCUMENT_CONFLICT") from None
        return {"ok":True,"replayed":False,**_state(conn,order)}
