"""Изоляция автомобильного MANAGER от глобальных ролей legacy-заявок.

До миграции 0032 синтетические fixture используют user_role MANAGER и
назначение на машину. После независимого dev-одобрения новая роль остаётся
только автомобильной: approved request + назначение + действующий филиал.
"""
from uuid import UUID

import psycopg


def manager_assignment(
    conn: psycopg.Connection, actor: UUID, vehicle: UUID,
) -> bool:
    """Проверить активный MANAGER именно для автомобиля и удержать grants."""
    legacy = conn.execute(
        """SELECT a.id FROM auto_vehicle_staff_assignment a
           JOIN user_role ur ON ur.user_id=a.user_id AND ur.role_code='MANAGER'
             AND ur.revoked_at IS NULL
             AND (ur.expires_at IS NULL OR ur.expires_at>now())
           WHERE a.vehicle_id=%s AND a.user_id=%s
             AND a.role_code='MANAGER' AND a.revoked_at IS NULL
           ORDER BY a.created_at DESC LIMIT 1
           FOR SHARE OF a,ur""",
        (vehicle, actor),
    ).fetchone()
    if legacy is not None:
        return True

    # Только автомобильная ветка, *без* создания user_role MANAGER.
    # Даже если опциональная глобальная проверка филиалов выключена,
    # вновь выданное полномочие требует живого grant и активного филиала.
    approved = conn.execute(
        """SELECT r.id FROM auto_manager_approval_request r
           JOIN auto_vehicle_staff_assignment a
             ON a.vehicle_id=r.vehicle_id AND a.user_id=r.target_id
             AND a.role_code='MANAGER' AND a.revoked_at IS NULL
           JOIN auto_vehicle_location_binding b
             ON b.vehicle_id=r.vehicle_id AND b.location_id=r.location_id
           JOIN auto_service_location l
             ON l.id=r.location_id AND l.disabled_at IS NULL
           JOIN auto_location_role_grant g
             ON g.location_id=l.id AND g.user_id=r.target_id
             AND g.role_code='MANAGER' AND g.revoked_at IS NULL
           WHERE r.vehicle_id=%s AND r.target_id=%s
             AND r.approved_at IS NOT NULL
           ORDER BY r.approved_at DESC LIMIT 1
           FOR SHARE OF r,a,b,l,g""",
        (vehicle, actor),
    ).fetchone()
    return approved is not None
