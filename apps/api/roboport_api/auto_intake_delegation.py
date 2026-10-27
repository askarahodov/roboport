"""Делегирование только INTAKE, только на конкретную машину, только действующим MANAGER.

Начальный MANAGER object scope остаётся исключительно в синтетических fixture.
Никакого ADMIN override, полномочий MANAGER и истории чужих заказов.
"""
from uuid import UUID, uuid4

import psycopg

from .auto_vehicle_claim import VehicleClaimRejected
from .session_lookup import session_subject
from .auto_create_idempotency import existing, remember, AutoIdempotencyConflict


class AssignmentConflict(Exception):
    """Действующее назначение уже существует; не создавать дубли или второй audit."""


def _manager(
    conn: psycopg.Connection, token: str, vehicle_id: UUID,
    *, for_mutation: bool = False,
) -> UUID:
    actor = session_subject(conn, token, for_mutation=for_mutation)
    if actor is None:
        raise VehicleClaimRejected("not authenticated")
    # FOR SHARE сериализует отзыв роли/объектного назначения относительно мутации.
    granted = conn.execute(
        """SELECT a.id FROM auto_vehicle_staff_assignment a
           JOIN user_role ur ON ur.user_id=a.user_id AND ur.role_code='MANAGER'
             AND ur.revoked_at IS NULL
             AND (ur.expires_at IS NULL OR ur.expires_at>now())
           WHERE a.vehicle_id=%s AND a.user_id=%s
             AND a.role_code='MANAGER' AND a.revoked_at IS NULL
           ORDER BY a.created_at DESC LIMIT 1
           FOR SHARE OF a,ur""",
        (vehicle_id, actor),
    ).fetchone()
    if granted is None:
        raise VehicleClaimRejected("no active manager assignment")
    return actor


def _record(conn: psycopg.Connection, ident: UUID, vehicle: UUID,
            actor: UUID, target: UUID, action: str) -> None:
    conn.execute(
        """INSERT INTO auto_vehicle_staff_assignment_audit(
             id,assignment_id,vehicle_id,actor_id,target_user_id,action)
           VALUES(%s,%s,%s,%s,%s,%s)""",
        (uuid4(), ident, vehicle, actor, target, action),
    )


def grant_intake(conn: psycopg.Connection, token: str,
                 vehicle_id: UUID, target_id: UUID,
                 idempotency_key: str | None = None) -> UUID:
    """Сериалицовать создание для vehicle и не позволить эскалацию до MANAGER."""
    if not isinstance(vehicle_id, UUID) or not isinstance(target_id, UUID):
        raise VehicleClaimRejected("bad UUID")
    with conn.transaction():
        # Сериализация назначения на одну машину и отсутствие cross-vehicle race.
        row = conn.execute(
            "SELECT id FROM auto_vehicle WHERE id=%s FOR UPDATE", (vehicle_id,),
        ).fetchone()
        if row is None:
            raise VehicleClaimRejected("unknown vehicle")
        actor = _manager(conn, token, vehicle_id, for_mutation=True)
        if actor == target_id:
            raise VehicleClaimRejected("self assignment forbidden")
        eligible = conn.execute(
            """SELECT u.id FROM app_user u
               JOIN user_role ur ON ur.user_id=u.id AND ur.role_code='INTAKE'
                 AND ur.revoked_at IS NULL
                 AND (ur.expires_at IS NULL OR ur.expires_at>now())
               WHERE u.id=%s AND u.status='ACTIVE'
               LIMIT 1 FOR SHARE OF u,ur""",
            (target_id,),
        ).fetchone()
        if eligible is None:
            raise VehicleClaimRejected("target not active INTAKE")
        payload = {"objectId": str(vehicle_id), "userId": str(target_id)}
        previous = existing(
            conn, actor, "vehicle.intake.grant", idempotency_key, payload,
        )
        if previous is not None:
            granted = conn.execute(
                """SELECT 1 FROM auto_vehicle_staff_assignment
                   WHERE id=%s AND vehicle_id=%s AND user_id=%s
                     AND role_code='INTAKE' AND revoked_at IS NULL""",
                (previous, vehicle_id, target_id),
            ).fetchone()
            if granted is None:
                raise AutoIdempotencyConflict("staff grant revoked")
            return previous
        active = conn.execute(
            """SELECT id FROM auto_vehicle_staff_assignment
               WHERE vehicle_id=%s AND user_id=%s AND role_code='INTAKE'
                 AND revoked_at IS NULL""", (vehicle_id, target_id),
        ).fetchone()
        if active is not None:
            raise AssignmentConflict()
        ident = uuid4()
        conn.execute(
            """INSERT INTO auto_vehicle_staff_assignment(id,vehicle_id,user_id,role_code)
               VALUES(%s,%s,%s,'INTAKE')""", (ident, vehicle_id, target_id),
        )
        _record(conn, ident, vehicle_id, actor, target_id, "INTAKE_GRANTED")
        remember(conn, actor, "vehicle.intake.grant", idempotency_key, payload, ident)
        return ident


def revoke_intake(conn: psycopg.Connection, token: str,
                  vehicle_id: UUID, assignment_id: UUID) -> str:
    if not isinstance(vehicle_id, UUID) or not isinstance(assignment_id, UUID):
        raise VehicleClaimRejected("bad UUID")
    with conn.transaction():
        row = conn.execute(
            "SELECT id FROM auto_vehicle WHERE id=%s FOR UPDATE", (vehicle_id,),
        ).fetchone()
        if row is None:
            raise VehicleClaimRejected("unknown vehicle")
        actor = _manager(conn, token, vehicle_id, for_mutation=True)
        assignment = conn.execute(
            """SELECT user_id,revoked_at FROM auto_vehicle_staff_assignment
               WHERE id=%s AND vehicle_id=%s AND role_code='INTAKE'
               FOR UPDATE""", (assignment_id, vehicle_id),
        ).fetchone()
        if assignment is None:
            raise VehicleClaimRejected("not assigned to this vehicle")
        if assignment[1] is not None:
            return "ALREADY_REVOKED"
        conn.execute(
            """UPDATE auto_vehicle_staff_assignment SET revoked_at=now()
               WHERE id=%s AND revoked_at IS NULL""", (assignment_id,),
        )
        _record(conn, assignment_id, vehicle_id, actor, assignment[0], "INTAKE_REVOKED")
        return "REVOKED"


def list_intake(conn: psycopg.Connection, token: str,
                vehicle_id: UUID) -> list[dict[str, str]]:
    if not isinstance(vehicle_id, UUID):
        raise VehicleClaimRejected("bad UUID")
    with conn.transaction():
        if conn.execute("SELECT 1 FROM auto_vehicle WHERE id=%s", (vehicle_id,)).fetchone() is None:
            raise VehicleClaimRejected("unknown vehicle")
        _manager(conn, token, vehicle_id)
        rows = conn.execute(
            """SELECT a.id,a.user_id FROM auto_vehicle_staff_assignment a
               JOIN app_user u ON u.id=a.user_id AND u.status='ACTIVE'
               JOIN user_role ur ON ur.user_id=a.user_id AND ur.role_code='INTAKE'
                  AND ur.revoked_at IS NULL
                  AND (ur.expires_at IS NULL OR ur.expires_at>now())
               WHERE a.vehicle_id=%s AND a.role_code='INTAKE'
                  AND a.revoked_at IS NULL
               ORDER BY a.id LIMIT 50""", (vehicle_id,),
        ).fetchall()
        # UUID видны только явно назначенному MANAGER, без email и ФИО.
        return [{"assignmentId": str(ident), "userId": str(uid)} for ident, uid in rows]
