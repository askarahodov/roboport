"""Synthetic structured mechanic diagnostics for selected WorkOrder services.

One immutable finding per service before the first customer quote. The same
ordered findings are captured in every quote revision's signed price digest.
No free text, VIN, contact data or claim of production repair certification.
"""
from uuid import UUID,uuid4
import psycopg

from .auto_workshop_qc import _actor_and_order,InspectionDenied


class DiagnosticDenied(ValueError):
    pass


class DiagnosticConflict(ValueError):
    pass


FINDINGS=frozenset(("NO_FAULT","MAINTENANCE_DUE","FAULT_CONFIRMED","FURTHER_CHECK"))
SEVERITIES=frozenset(("ROUTINE","SOON","URGENT"))


def _scope(conn,token,org,loc,order,roles):
    try:
        return _actor_and_order(conn,token,org,loc,order,roles)
    except InspectionDenied:
        raise DiagnosticDenied("FORBIDDEN") from None


def diagnostic_snapshot(conn,order):
    """Stable canonical order matches the DB insert trigger on quote revision."""
    rows=conn.execute("""
       SELECT d.service_id,d.finding_code,d.severity_code
       FROM auto_work_order_diagnostic d
       JOIN auto_service_catalog s ON s.id=d.service_id
       WHERE d.work_order_id=%s ORDER BY s.code,d.service_id LIMIT 51
    """,(order,)).fetchall()
    if len(rows)>50:
        raise DiagnosticConflict("LIMIT")
    return [{"serviceId":str(service),"findingCode":finding,"severityCode":severity}
            for service,finding,severity in rows]


def _read(conn,order,org,loc):
    services=conn.execute("""
       SELECT s.id,s.code,s.title,s.duration_minutes
       FROM auto_work_order_service chosen
       JOIN auto_service_catalog s ON s.id=chosen.service_id
         AND s.organization_id=chosen.organization_id
         AND s.location_id=chosen.location_id
       WHERE chosen.work_order_id=%s
         AND chosen.organization_id=%s AND chosen.location_id=%s
       ORDER BY s.code,s.id LIMIT 51
    """,(order,org,loc)).fetchall()
    if len(services)>50:
        raise DiagnosticConflict("LIMIT")
    rows=conn.execute("""
       SELECT d.id,d.service_id,d.finding_code,d.severity_code,
              d.actor_id,d.created_at
       FROM auto_work_order_diagnostic d
       WHERE d.work_order_id=%s ORDER BY d.created_at,d.id LIMIT 51
    """,(order,)).fetchall()
    if len(rows)>50:
        raise DiagnosticConflict("LIMIT")
    inspection=conn.execute(
       "SELECT 1 FROM auto_work_order_inspection WHERE work_order_id=%s",
       (order,),
    ).fetchone() is not None
    frozen=conn.execute(
       "SELECT 1 FROM auto_work_order_estimate_revision WHERE work_order_id=%s LIMIT 1",
       (order,),
    ).fetchone() is not None
    return {"syntheticOnly":True,"releaseAllowed":False,
            "inspectionRecorded":inspection,"estimatePublished":frozen,
            "services":[{"serviceId":str(i),"code":code,"title":title,
                         "durationMinutes":minutes}
                        for i,code,title,minutes in services],
            "items":[{"id":str(i),"serviceId":str(service),
                      "findingCode":finding,"severityCode":severity,
                      "actorId":str(actor),"createdAt":stamp.isoformat()}
                     for i,service,finding,severity,actor,stamp in rows]}


def read_diagnostics(conn,token,org,loc,order):
    with conn.transaction():
        _scope(conn,token,org,loc,order,("MECHANIC","INTAKE","MANAGER"))
        return _read(conn,order,org,loc)


def record_diagnostic(conn,token,org,loc,order,*,request_key,service_id,
                      finding_code,severity_code):
    if (type(request_key) is not UUID or type(service_id) is not UUID
            or finding_code not in FINDINGS or severity_code not in SEVERITIES
            or (finding_code=="NO_FAULT" and severity_code!="ROUTINE")):
        raise DiagnosticDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_scope(conn,token,org,loc,order,("MECHANIC",))
        previous=conn.execute("""
          SELECT work_order_id,service_id,actor_id,finding_code,severity_code
          FROM auto_work_order_diagnostic WHERE request_key=%s
        """,(request_key,)).fetchone()
        if previous:
            if previous!=(order,service_id,actor,finding_code,severity_code):
                raise DiagnosticConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**_read(conn,order,org,loc)}
        state=_read(conn,order,org,loc)
        if not state["inspectionRecorded"]:
            raise DiagnosticConflict("INSPECTION_REQUIRED")
        if state["estimatePublished"]:
            raise DiagnosticConflict("ESTIMATE_ALREADY_PUBLISHED")
        if str(service_id) not in [row["serviceId"] for row in state["services"]]:
            raise DiagnosticConflict("SERVICE_NOT_SELECTED")
        if any(row["serviceId"]==str(service_id) for row in state["items"]):
            raise DiagnosticConflict("SERVICE_ALREADY_DIAGNOSED")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_order_diagnostic
                  (id,work_order_id,service_id,actor_id,finding_code,
                   severity_code,request_key)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)
                """,(uuid4(),order,service_id,actor,finding_code,
                      severity_code,request_key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise DiagnosticConflict("DIAGNOSTIC_CONFLICT") from exc
        return {"ok":True,"replayed":False,**_read(conn,order,org,loc)}
