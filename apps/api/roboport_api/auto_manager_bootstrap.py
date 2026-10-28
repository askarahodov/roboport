"""Закрытая двухсторонняя выдача MANAGER только для synthetic/dev PostgreSQL.

Публичного HTTP API, автоматического первого ADMIN и production bootstrap нет.
Две разные действующие ADMIN-сессии подтверждают ровно одну машину и филиал.
"""
import ipaddress
import os
from uuid import UUID, uuid4

import psycopg

from .session_lookup import session_subject


class ManagerBootstrapRejected(Exception):
    """Условие полномочий не выполнено; не сообщать подробности через HTTP."""


def _dev_only(conn: psycopg.Connection) -> None:
    if any(os.getenv(name) != "1" for name in (
        "ROBOport_SYNTHETIC_AUTO_API",
        "ROBOport_SYNTHETIC_AUTH_API",
        "ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE",
        "ROBOport_SYNTHETIC_AUTO_MANAGER_BOOTSTRAP",
    )):
        raise ManagerBootstrapRejected("dev bootstrap disabled")
    # Скрипты синтетического сценария не должны работать на удалённой БД.
    try:
        host = conn.info.host
        if not (host in ("localhost",) or (
            host and ipaddress.ip_address(host).is_loopback
        )):
            raise ManagerBootstrapRejected("not local PostgreSQL")
    except ValueError as exc:
        raise ManagerBootstrapRejected("not local PostgreSQL") from exc


def _admin(conn: psycopg.Connection, token: str) -> UUID:
    actor = session_subject(conn, token, for_mutation=True)
    if actor is None:
        raise ManagerBootstrapRejected("unauthorized")
    role = conn.execute(
        """SELECT id FROM user_role
           WHERE user_id=%s AND role_code='ADMIN' AND revoked_at IS NULL
             AND (expires_at IS NULL OR expires_at>now())
           LIMIT 1 FOR SHARE""",
        (actor,),
    ).fetchone()
    if role is None:
        raise ManagerBootstrapRejected("no active admin role")
    return actor


def _active_location(conn: psycopg.Connection, vehicle: UUID) -> UUID:
    row = conn.execute(
        """SELECT l.id FROM auto_vehicle_location_binding b
           JOIN auto_service_location l ON l.id=b.location_id
           WHERE b.vehicle_id=%s AND l.disabled_at IS NULL
           FOR SHARE OF b,l""",
        (vehicle,),
    ).fetchone()
    if row is None:
        raise ManagerBootstrapRejected("no active branch")
    return row[0]


def _target(conn: psycopg.Connection, target: UUID, *, lock: bool = False) -> None:
    clause = "FOR UPDATE" if lock else "FOR SHARE"
    row = conn.execute(
        f"SELECT id FROM app_user WHERE id=%s AND status='ACTIVE' {clause}",
        (target,),
    ).fetchone()
    if row is None:
        raise ManagerBootstrapRejected("target inactive")


def _not_assigned(conn: psycopg.Connection, vehicle: UUID, target: UUID) -> None:
    if conn.execute(
        """SELECT 1 FROM auto_vehicle_staff_assignment
           WHERE vehicle_id=%s AND user_id=%s AND role_code='MANAGER'
             AND revoked_at IS NULL""",
        (vehicle, target),
    ).fetchone() is not None:
        raise ManagerBootstrapRejected("manager already assigned")


def _audit(conn: psycopg.Connection, request: UUID, actor: UUID, action: str) -> None:
    conn.execute(
        """INSERT INTO auto_manager_approval_audit(id,request_id,actor_id,action)
           VALUES(%s,%s,%s,%s)""",
        (uuid4(), request, actor, action),
    )


def propose_manager(
    conn: psycopg.Connection, admin_token: str,
    vehicle: UUID, target: UUID,
) -> UUID:
    """Первый ADMIN создаёт короткоживущую заявку, не выдавая прав."""
    _dev_only(conn)
    if not isinstance(vehicle, UUID) or not isinstance(target, UUID):
        raise ManagerBootstrapRejected("bad identifier")
    with conn.transaction():
        # Та же объектная блокировка для начала и завершения назначения:
        # две параллельные заявки не обходят проверку pending/active grant.
        if conn.execute(
            "SELECT 1 FROM auto_vehicle WHERE id=%s FOR UPDATE", (vehicle,),
        ).fetchone() is None:
            raise ManagerBootstrapRejected("unknown vehicle")
        actor = _admin(conn, admin_token)
        if actor == target:
            raise ManagerBootstrapRejected("self grant forbidden")
        location = _active_location(conn, vehicle)
        _target(conn, target)
        _not_assigned(conn, vehicle, target)
        if conn.execute(
            """SELECT 1 FROM auto_manager_approval_request
               WHERE vehicle_id=%s AND target_id=%s
                 AND approved_at IS NULL AND expires_at>now()""",
            (vehicle, target),
        ).fetchone() is not None:
            raise ManagerBootstrapRejected("active request exists")
        request = uuid4()
        conn.execute(
            """INSERT INTO auto_manager_approval_request
               (id,vehicle_id,location_id,target_id,initiated_by)
               VALUES(%s,%s,%s,%s,%s)""",
            (request, vehicle, location, target, actor),
        )
        _audit(conn, request, actor, "PROPOSED")
        return request


def approve_manager(
    conn: psycopg.Connection, admin_token: str, request: UUID,
) -> UUID:
    """Второй независимый ADMIN выдаёт три scope-гранта одной транзакцией."""
    _dev_only(conn)
    if not isinstance(request, UUID):
        raise ManagerBootstrapRejected("bad identifier")
    with conn.transaction():
        pointer = conn.execute(
            "SELECT vehicle_id FROM auto_manager_approval_request WHERE id=%s",
            (request,),
        ).fetchone()
        if pointer is None:
            raise ManagerBootstrapRejected("unknown request")
        vehicle = pointer[0]
        if conn.execute(
            "SELECT 1 FROM auto_vehicle WHERE id=%s FOR UPDATE", (vehicle,),
        ).fetchone() is None:
            raise ManagerBootstrapRejected("vehicle unavailable")
        row = conn.execute(
            """SELECT location_id,target_id,initiated_by
               FROM auto_manager_approval_request
               WHERE id=%s AND approved_at IS NULL AND expires_at>now()
               FOR UPDATE""",
            (request,),
        ).fetchone()
        if row is None:
            raise ManagerBootstrapRejected("request expired or approved")
        location, target, initiator = row
        approver = _admin(conn, admin_token)
        if len({initiator, target, approver}) != 3:
            raise ManagerBootstrapRejected("independent approval required")
        # Инициатор обязан сохранить полномочия до финального подтверждения.
        initiator_live = conn.execute(
            """SELECT u.id FROM app_user u
               JOIN user_role ur ON ur.user_id=u.id
                 AND ur.role_code='ADMIN' AND ur.revoked_at IS NULL
                 AND (ur.expires_at IS NULL OR ur.expires_at>now())
               WHERE u.id=%s AND u.status='ACTIVE'
               LIMIT 1 FOR SHARE OF u,ur""",
            (initiator,),
        ).fetchone()
        if initiator_live is None:
            raise ManagerBootstrapRejected("initiator revoked")
        if _active_location(conn, vehicle) != location:
            raise ManagerBootstrapRejected("branch changed")
        # Эксклюзивный lock target не позволяет двум параллельным выдачам
        # на разных автомобилях создать две активные глобальные роли.
        _target(conn, target, lock=True)
        _not_assigned(conn, vehicle, target)
        if conn.execute(
            """SELECT 1 FROM user_role WHERE user_id=%s
               AND role_code='MANAGER' AND revoked_at IS NULL
               AND (expires_at IS NULL OR expires_at>now())""",
            (target,),
        ).fetchone() is None:
            conn.execute(
                """INSERT INTO user_role(id,user_id,role_code)
                   VALUES(%s,%s,'MANAGER')""",
                (uuid4(), target),
            )
        assignment = uuid4()
        conn.execute(
            """INSERT INTO auto_vehicle_staff_assignment
               (id,vehicle_id,user_id,role_code)
               VALUES(%s,%s,%s,'MANAGER')""",
            (assignment, vehicle, target),
        )
        conn.execute(
            """INSERT INTO auto_location_role_grant
               (id,location_id,user_id,role_code)
               VALUES(%s,%s,%s,'MANAGER')
               ON CONFLICT (location_id,user_id,role_code)
               WHERE revoked_at IS NULL DO NOTHING""",
            (uuid4(), location, target),
        )
        conn.execute(
            """UPDATE auto_manager_approval_request
               SET approved_by=%s,approved_at=now()
               WHERE id=%s AND approved_at IS NULL""",
            (approver, request),
        )
        _audit(conn, request, approver, "APPROVED")
        return assignment
