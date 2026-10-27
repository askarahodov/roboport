"""Создание pending-привязки автомобиля только назначенным приёмщиком.

Этот dev-сервис не подтверждает владение и не выдаёт доступ к истории нарядов.
"""
from uuid import UUID, uuid4

import psycopg

from .auto_vehicle_claim import VehicleClaimRejected, _role_scoped


class VehicleLinkConflict(Exception):
    """Активная связь для данной пары уже есть."""


def create_pending_vehicle_link(
    conn: psycopg.Connection,
    staff_token: str,
    vehicle_id: UUID,
    customer_id: UUID,
) -> UUID:
    """В одной транзакции проверить scope, клиента, уникальность и записать audit."""
    if not isinstance(vehicle_id, UUID) or not isinstance(customer_id, UUID):
        raise VehicleClaimRejected("invalid input")
    with conn.transaction():
        # Сериализация создания одной и той же связи между конкурентными запросами.
        existing_vehicle = conn.execute(
            "SELECT id FROM auto_vehicle WHERE id=%s FOR UPDATE", (vehicle_id,),
        ).fetchone()
        if existing_vehicle is None:
            raise VehicleClaimRejected("unavailable")
        actor = _role_scoped(conn, staff_token, "INTAKE", vehicle_id)
        # Предлагать связь разрешено только с существующим активным клиентом.
        # Знание customer UUID само по себе не даёт прав на автомобиль.
        eligible = conn.execute(
            """SELECT 1 FROM account_customer_link acl
               JOIN app_user u ON u.id=acl.user_id AND u.status='ACTIVE'
               JOIN user_role ur ON ur.user_id=u.id AND ur.role_code='CLIENT'
                 AND ur.revoked_at IS NULL
                 AND (ur.expires_at IS NULL OR ur.expires_at>now())
               WHERE acl.customer_id=%s AND acl.revoked_at IS NULL
               LIMIT 1""",
            (customer_id,),
        ).fetchone()
        if eligible is None:
            raise VehicleClaimRejected("unavailable")
        active = conn.execute(
            """SELECT 1 FROM auto_vehicle_customer_link
               WHERE vehicle_id=%s AND customer_id=%s AND revoked_at IS NULL""",
            (vehicle_id, customer_id),
        ).fetchone()
        if active is not None:
            raise VehicleLinkConflict()
        link_id = uuid4()
        conn.execute(
            """INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id)
               VALUES(%s,%s,%s)""",
            (link_id, vehicle_id, customer_id),
        )
        conn.execute(
            """INSERT INTO auto_vehicle_link_intake_audit(
                 id,link_id,actor_id,action
               ) VALUES(%s,%s,%s,'LINK_CREATED')""",
            (uuid4(), link_id, actor),
        )
        return link_id
