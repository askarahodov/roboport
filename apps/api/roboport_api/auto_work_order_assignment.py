"""Shift-aware mechanic offers for synthetic automotive work orders."""
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject


class AssignmentDenied(ValueError):
    pass


class AssignmentConflict(ValueError):
    pass


def _valid_window(starts, ends):
    return (isinstance(starts, datetime) and starts.tzinfo is not None
            and starts.utcoffset() is not None
            and isinstance(ends, datetime) and ends.tzinfo is not None
            and ends.utcoffset() is not None
            and starts.astimezone(timezone.utc) > datetime.now(timezone.utc)
            and ends.astimezone(timezone.utc) > starts.astimezone(timezone.utc)
            and ends.astimezone(timezone.utc)-starts.astimezone(timezone.utc)
                <= timedelta(hours=24))


def assign_mechanic(conn, token: str, organization_id: UUID, location_id: UUID,
                    work_order_id: UUID, mechanic_id: UUID, starts_at: datetime,
                    ends_at: datetime, request_key: UUID) -> dict:
    if (type(token) is not str or not token
        or any(type(v) is not UUID for v in
               (organization_id,location_id,work_order_id,mechanic_id,request_key))
        or not _valid_window(starts_at,ends_at)):
        raise AssignmentDenied("INVALID_INPUT")
    starts=starts_at.astimezone(timezone.utc)
    ends=ends_at.astimezone(timezone.utc)
    with conn.transaction():
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None or not organization_role_allows(
            conn,token,organization_id,"MANAGER",location_id,for_mutation=True):
            raise AssignmentDenied("FORBIDDEN")
        order=conn.execute("""
          SELECT w.id FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN LATERAL (
            SELECT COALESCE((SELECT rs.new_booking_id FROM auto_appointment_reschedule rs
              WHERE rs.appointment_id=a.id
              ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1),a.booking_id) AS booking_id
          ) current_booking ON TRUE
          JOIN auto_bay_booking b ON b.id=current_booking.booking_id
          JOIN auto_vehicle_customer_link proof
            ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
          WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
            AND b.organization_id=w.organization_id AND b.location_id=w.location_id
            AND b.canceled_at IS NULL AND proof.verified_at IS NOT NULL
            AND proof.revoked_at IS NULL
          FOR UPDATE OF w,a,b,proof
        """,(work_order_id,organization_id,location_id)).fetchone()
        if order is None:
            raise AssignmentDenied("FORBIDDEN")
        prior=conn.execute("""
          SELECT id,mechanic_id,starts_at,ends_at,status
          FROM auto_work_order_assignment WHERE request_key=%s
        """,(request_key,)).fetchone()
        if prior:
            if prior[1:4]!=(mechanic_id,starts,ends):
                raise AssignmentConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,"assignmentId":str(prior[0]),
                    "status":prior[4]}
        grant=conn.execute("""
          SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.organization_id=%s AND g.user_id=%s AND g.role_code='MECHANIC'
            AND g.revoked_at IS NULL
            AND (g.location_id=%s OR g.location_id IS NULL)
          FOR SHARE OF g,u
        """,(organization_id,mechanic_id,location_id)).fetchone()
        if grant is None:
            raise AssignmentDenied("MECHANIC_UNAVAILABLE")
        shift=conn.execute("""
          SELECT id FROM auto_staff_shift
          WHERE organization_id=%s AND location_id=%s AND user_id=%s
            AND canceled_at IS NULL AND starts_at<=%s AND ends_at>=%s
          FOR UPDATE
        """,(organization_id,location_id,mechanic_id,starts,ends)).fetchone()
        if shift is None:
            raise AssignmentDenied("SHIFT_UNAVAILABLE")
        active=conn.execute("""
          SELECT id FROM auto_work_order_assignment
          WHERE work_order_id=%s AND status IN ('OFFERED','ACCEPTED')
        """,(work_order_id,)).fetchone()
        if active:
            raise AssignmentConflict("ORDER_ALREADY_ASSIGNED")
        assignment=uuid4()
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_order_assignment
                    (id,work_order_id,organization_id,location_id,mechanic_id,
                     assigned_by,starts_at,ends_at,request_key)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,(assignment,work_order_id,organization_id,location_id,
                     mechanic_id,actor,starts,ends,request_key))
                conn.execute("""
                  INSERT INTO auto_work_order_assignment_event
                    (id,assignment_id,actor_id,action,request_key)
                  VALUES(%s,%s,%s,'OFFERED',%s)
                """,(uuid4(),assignment,actor,request_key))
        except (psycopg.errors.ExclusionViolation,psycopg.errors.UniqueViolation,
                psycopg.errors.RaiseException):
            raise AssignmentConflict("MECHANIC_ALREADY_ALLOCATED") from None
        return {"ok":True,"replayed":False,"assignmentId":str(assignment),
                "status":"OFFERED"}


def respond_assignment(conn, token: str, organization_id: UUID, location_id: UUID,
                       assignment_id: UUID, request_key: UUID, accept: bool) -> dict:
    if (type(token) is not str or not token
        or type(organization_id) is not UUID or type(location_id) is not UUID
        or type(assignment_id) is not UUID or type(request_key) is not UUID
        or type(accept) is not bool):
        raise AssignmentDenied("INVALID_INPUT")
    with conn.transaction():
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None or not organization_role_allows(
            conn,token,organization_id,"MECHANIC",location_id,for_mutation=True):
            raise AssignmentDenied("FORBIDDEN")
        prior=conn.execute("""
          SELECT assignment_id,actor_id,action FROM auto_work_order_assignment_event
          WHERE request_key=%s
        """,(request_key,)).fetchone()
        action="ACCEPTED" if accept else "DECLINED"
        if prior:
            if prior!=(assignment_id,actor,action):
                raise AssignmentConflict("KEY_REUSED")
            row=conn.execute("""
              SELECT status FROM auto_work_order_assignment WHERE id=%s
                AND organization_id=%s AND location_id=%s
            """,(assignment_id,organization_id,location_id)).fetchone()
            if row is None:
                raise AssignmentDenied("FORBIDDEN")
            return {"ok":True,"replayed":True,"status":row[0]}
        row=conn.execute("""
          SELECT mechanic_id,status FROM auto_work_order_assignment
          WHERE id=%s AND organization_id=%s AND location_id=%s FOR UPDATE
        """,(assignment_id,organization_id,location_id)).fetchone()
        if row is None or row[0]!=actor:
            raise AssignmentDenied("FORBIDDEN")
        if row[1]!="OFFERED":
            raise AssignmentConflict("ASSIGNMENT_NOT_OFFERED")
        accepted= datetime.now(timezone.utc) if accept else None
        try:
            with conn.transaction():
                conn.execute("""
                  UPDATE auto_work_order_assignment SET status=%s,
                    status_actor_id=%s,accepted_at=%s WHERE id=%s
                """,(action,actor,accepted,assignment_id))
                conn.execute("""
                  INSERT INTO auto_work_order_assignment_event
                    (id,assignment_id,actor_id,action,request_key)
                  VALUES(%s,%s,%s,%s,%s)
                """,(uuid4(),assignment_id,actor,action,request_key))
        except (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException):
            raise AssignmentConflict("ASSIGNMENT_NOT_OFFERED") from None
        return {"ok":True,"replayed":False,"status":action}


def list_assignments(conn, token: str, organization_id: UUID,
                     location_id: UUID) -> list[dict]:
    if type(token) is not str or not token:
        raise AssignmentDenied("FORBIDDEN")
    with conn.transaction():
        actor=session_subject(conn,token)
        if actor is None:
            raise AssignmentDenied("FORBIDDEN")
        manager=organization_role_allows(conn,token,organization_id,"MANAGER",location_id)
        mechanic=organization_role_allows(conn,token,organization_id,"MECHANIC",location_id)
        if not manager and not mechanic:
            raise AssignmentDenied("FORBIDDEN")
        rows=conn.execute("""
          SELECT a.id,a.work_order_id,a.mechanic_id,a.starts_at,a.ends_at,
                 a.status,a.accepted_at
          FROM auto_work_order_assignment a
          WHERE a.organization_id=%s AND a.location_id=%s
            AND a.status IN ('OFFERED','ACCEPTED')
            AND (%s OR a.mechanic_id=%s)
          ORDER BY a.starts_at,a.id LIMIT 101
        """,(organization_id,location_id,manager,actor)).fetchall()
        if len(rows)>100:
            raise AssignmentConflict("LIMIT")
        return [{"assignmentId":str(i),"workOrderId":str(o),
                 "mechanicId":str(m),"startsAt":s.isoformat(),"endsAt":e.isoformat(),
                 "status":st,"acceptedAt":a.isoformat() if a else None}
                for i,o,m,s,e,st,a in rows]
