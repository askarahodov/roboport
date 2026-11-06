"""Synthetic automotive intake -> independent QC with immutable, scoped events."""
from uuid import UUID, uuid4
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject


class InspectionDenied(ValueError):
    pass


class InspectionConflict(ValueError):
    pass


def _actor_and_order(conn, token, org, loc, order, roles):
    if (type(token) is not str or not token.strip()
            or any(type(x) is not UUID for x in (org, loc, order))):
        raise InspectionDenied("FORBIDDEN")
    if not any(organization_role_allows(conn, token, org, role, loc,
                                        for_mutation=True) for role in roles):
        raise InspectionDenied("FORBIDDEN")
    actor = session_subject(conn, token, for_mutation=True)
    if actor is None:
        raise InspectionDenied("FORBIDDEN")
    # Lock the business identity before checking state: concurrent QC and
    # duplicate intake writes serialize on the same WorkOrder row.
    found = conn.execute("""
        SELECT w.id FROM auto_work_order w
        JOIN auto_appointment a ON a.id=w.appointment_id
        JOIN LATERAL (
          SELECT COALESCE((
            SELECT r.new_booking_id FROM auto_appointment_reschedule r
            WHERE r.appointment_id=a.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1
          ),a.booking_id) AS current_id
        ) current_booking ON TRUE
        JOIN auto_bay_booking b ON b.id=current_booking.current_id
        JOIN auto_vehicle_customer_link proof
          ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
        JOIN auto_vehicle_location_binding binding
          ON binding.vehicle_id=a.vehicle_id AND binding.location_id=w.location_id
        JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
        JOIN auto_service_location loc ON loc.id=w.location_id
          AND loc.organization_id=o.id AND loc.disabled_at IS NULL
        WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
          AND a.organization_id=w.organization_id AND a.location_id=w.location_id
          AND b.organization_id=w.organization_id AND b.location_id=w.location_id
          AND b.canceled_at IS NULL AND proof.verified_at IS NOT NULL
          AND proof.revoked_at IS NULL
        FOR UPDATE OF w
    """, (order, org, loc)).fetchone()
    if found is None:
        raise InspectionDenied("FORBIDDEN")
    return actor


def _snapshot(conn, order):
    inspection = conn.execute("""
        SELECT id,odometer_km,fuel_percent,tyres_ok,lights_ok,fluid_leak,
               exterior_code,created_at
        FROM auto_work_order_inspection WHERE work_order_id=%s
    """, (order,)).fetchone()
    history = conn.execute("""
        SELECT id,result,finding_code,created_at
        FROM auto_work_order_qc_event WHERE work_order_id=%s
        ORDER BY created_at,id LIMIT 51
    """, (order,)).fetchall()
    if len(history)>50:
        raise InspectionConflict("HISTORY_LIMIT")
    events = [{"id":str(i),"result":result,"findingCode":code,
               "createdAt":time.isoformat()} for i,result,code,time in history]
    status = ("NOT_INSPECTED" if not inspection else
              "PENDING_QC" if not events else
              "QC_PASSED" if events[-1]["result"]=="PASS" else "QC_FAILED")
    return {
        "status":status, "releaseAllowed":False,
        "inspection":None if inspection is None else {
            "id":str(inspection[0]),"odometerKm":inspection[1],
            "fuelPercent":inspection[2],"tyresOk":inspection[3],
            "lightsOk":inspection[4],"fluidLeak":inspection[5],
            "exteriorCode":inspection[6],"createdAt":inspection[7].isoformat(),
        },
        "qcEvents":events,
    }


def read_work_order_inspection(conn, token, org, loc, order):
    with conn.transaction():
        _actor_and_order(conn,token,org,loc,order,("INTAKE","MECHANIC","MANAGER"))
        return _snapshot(conn,order)


def submit_work_order_inspection(conn, token, org, loc, order, *,
                                 odometer_km, fuel_percent, tyres_ok, lights_ok,
                                 fluid_leak, exterior_code):
    if (type(odometer_km) is not int or not 0<=odometer_km<=2000000
            or type(fuel_percent) is not int or not 0<=fuel_percent<=100
            or any(type(x) is not bool for x in (tyres_ok,lights_ok,fluid_leak))
            or exterior_code not in ("CLEAR","DAMAGE","UNCHECKED")):
        raise InspectionDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_actor_and_order(conn,token,org,loc,order,("INTAKE",))
        old=conn.execute("""
          SELECT odometer_km,fuel_percent,tyres_ok,lights_ok,fluid_leak,exterior_code
          FROM auto_work_order_inspection WHERE work_order_id=%s
        """,(order,)).fetchone()
        values=(odometer_km,fuel_percent,tyres_ok,lights_ok,fluid_leak,exterior_code)
        if old is not None:
            if tuple(old)!=values:
                raise InspectionConflict("INSPECTION_IMMUTABLE")
            return {"ok":True,"replayed":True,**_snapshot(conn,order)}
        conn.execute("""
          INSERT INTO auto_work_order_inspection
          (id,work_order_id,organization_id,location_id,actor_id,odometer_km,
           fuel_percent,tyres_ok,lights_ok,fluid_leak,exterior_code)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,(uuid4(),order,org,loc,actor,*values))
        return {"ok":True,"replayed":False,**_snapshot(conn,order)}


def record_work_order_qc(conn, token, org, loc, order, *,
                         request_key, result, finding_code):
    if (type(request_key) is not UUID or result not in ("PASS","FAIL")
            or (result=="PASS" and finding_code!="NONE")
            or (result=="FAIL" and finding_code not in ("DEFECT","REWORK"))):
        raise InspectionDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_actor_and_order(conn,token,org,loc,order,("MECHANIC",))
        inspection=conn.execute("""
          SELECT id,actor_id FROM auto_work_order_inspection
          WHERE work_order_id=%s FOR SHARE
        """,(order,)).fetchone()
        if inspection is None:
            raise InspectionConflict("INSPECTION_REQUIRED")
        if inspection[1]==actor:
            raise InspectionDenied("SEPARATION_REQUIRED")
        execution=conn.execute("""
          SELECT e.id FROM auto_work_order_execution e
          WHERE e.work_order_id=%s
        """,(order,)).fetchone()
        if execution:
            finished=conn.execute("""
              SELECT id FROM auto_work_order_execution_finish
              WHERE execution_id=%s
            """,(execution[0],)).fetchone()
            if finished is None:
                raise InspectionConflict("EXECUTION_INCOMPLETE")
            performed=conn.execute("""
              SELECT 1 FROM auto_work_order_labor_entry
              WHERE execution_id=%s AND actor_id=%s LIMIT 1
            """,(execution[0],actor)).fetchone()
            if performed:
                raise InspectionDenied("SEPARATION_REQUIRED")
        existing=conn.execute("""
          SELECT result,finding_code,actor_id FROM auto_work_order_qc_event
          WHERE work_order_id=%s AND request_key=%s
        """,(order,request_key)).fetchone()
        if existing is not None:
            if tuple(existing)!=(result,finding_code,actor):
                raise InspectionConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**_snapshot(conn,order)}
        snapshot=_snapshot(conn,order)
        if snapshot["status"]=="QC_PASSED":
            raise InspectionConflict("QC_ALREADY_PASSED")
        conn.execute("""
          INSERT INTO auto_work_order_qc_event
          (id,work_order_id,inspection_id,organization_id,location_id,
           actor_id,request_key,result,finding_code)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,(uuid4(),order,inspection[0],org,loc,actor,request_key,result,finding_code))
        return {"ok":True,"replayed":False,**_snapshot(conn,order)}



def pending_work_order_qc(conn, token, org, loc, limit=20, after_order_id=None):
    """Bounded live MECHANIC queue: inspected, eligible, not yet QC-passed.

    Selection is advisory. QC POST independently locks the work order and
    revalidates maker-checker, work completion and current proof.
    """
    if type(token) is not str or not token.strip():
        raise InspectionDenied("FORBIDDEN")
    if (type(org) is not UUID or type(loc) is not UUID
            or type(limit) is not int or not 1<=limit<=50
            or (after_order_id is not None and type(after_order_id) is not UUID)):
        raise InspectionDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,org,"MECHANIC",loc,
                                        for_mutation=True):
            raise InspectionDenied("FORBIDDEN")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None:
            raise InspectionDenied("FORBIDDEN")
        rows=conn.execute("""
            SELECT w.id,COALESCE(latest.result,'PENDING_QC') AS current_state,
                   COALESCE(latest.finding_code,'NONE') AS finding
            FROM auto_work_order w
            JOIN auto_appointment a ON a.id=w.appointment_id
              AND a.organization_id=w.organization_id
              AND a.location_id=w.location_id
            JOIN LATERAL (
              SELECT COALESCE((
                SELECT r.new_booking_id FROM auto_appointment_reschedule r
                WHERE r.appointment_id=a.id
                ORDER BY r.created_at DESC,r.id DESC LIMIT 1
              ),a.booking_id) AS current_id
            ) booking ON TRUE
            JOIN auto_bay_booking b ON b.id=booking.current_id
              AND b.organization_id=w.organization_id
              AND b.location_id=w.location_id AND b.canceled_at IS NULL
            JOIN auto_vehicle_customer_link proof
              ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
              AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
            JOIN auto_vehicle_location_binding binding
              ON binding.vehicle_id=a.vehicle_id AND binding.location_id=w.location_id
            JOIN auto_organization o ON o.id=w.organization_id
              AND o.disabled_at IS NULL
            JOIN auto_service_location loc ON loc.id=w.location_id
              AND loc.organization_id=o.id AND loc.disabled_at IS NULL
            JOIN auto_work_order_inspection inspection
              ON inspection.work_order_id=w.id AND inspection.actor_id<>%s
            LEFT JOIN LATERAL (
              SELECT q.result,q.finding_code
              FROM auto_work_order_qc_event q WHERE q.work_order_id=w.id
              ORDER BY q.created_at DESC,q.id DESC LIMIT 1
            ) latest ON TRUE
            LEFT JOIN auto_work_order_execution execution
              ON execution.work_order_id=w.id
            WHERE w.organization_id=%s AND w.location_id=%s
              AND (latest.result IS NULL OR latest.result='FAIL')
              AND (execution.id IS NULL OR EXISTS(
                SELECT 1 FROM auto_work_order_execution_finish finish
                WHERE finish.execution_id=execution.id
              ))
              AND (execution.id IS NULL OR NOT EXISTS(
                SELECT 1 FROM auto_work_order_labor_entry labor
                WHERE labor.execution_id=execution.id AND labor.actor_id=%s
              ))
              AND (%s::uuid IS NULL OR w.id>%s::uuid)
            ORDER BY w.id LIMIT %s
        """,(actor,org,loc,actor,after_order_id,after_order_id,limit+1)).fetchall()
        next_id=rows[limit-1][0] if len(rows)>limit else None
        return ([{"orderId":str(order_id),"status":state,
                  "lastFindingCode":finding}
                 for order_id,state,finding in rows[:limit]],next_id)
