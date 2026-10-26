"""Приватные чтения автомобилей: только подтверждённая связь и активный клиент.

Этот модуль не изменяет право доступа к историческим заявкам роботов.
"""
from uuid import UUID

import psycopg

from .session_tokens import token_digest


def _vehicle(row) -> dict:
    return {
        "id": str(row[0]),
        "make": row[1],
        "model": row[2],
        "variant": row[3],
        "modelYear": row[4],
        "vinLast4": row[5][-4:] if row[5] else None,
    }


def owned_vehicles(
    conn: psycopg.Connection,
    token: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict] | None:
    """None — нет действующей сессии, пустой список — нет доступных автомобилей."""
    if not isinstance(token, str) or len(token) != 43:
        return None
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    if type(offset) is not int or not 0 <= offset <= 10000:
        raise ValueError("offset must be 0..10000")
    # Авторизация, отзыв связей и выборка машин в одном SQL snapshot.
    rows = conn.execute(
        """SELECT page.id,page.make,page.model,page.variant,page.model_year,page.vin
          FROM user_session s
          JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
          LEFT JOIN LATERAL (
            SELECT v.id,v.make,v.model,v.variant,v.model_year,v.vin,v.created_at
            FROM auto_vehicle v
            WHERE EXISTS (
              SELECT 1 FROM account_customer_link acl
              JOIN auto_vehicle_customer_link avcl
                ON avcl.customer_id=acl.customer_id AND avcl.vehicle_id=v.id
              WHERE acl.user_id=u.id AND acl.revoked_at IS NULL
                AND avcl.verified_at IS NOT NULL AND avcl.verified_by IS NOT NULL
                AND avcl.revoked_at IS NULL
                AND EXISTS (
                  SELECT 1 FROM user_role role
                  WHERE role.user_id=u.id AND role.role_code='CLIENT'
                    AND role.revoked_at IS NULL
                    AND (role.expires_at IS NULL OR role.expires_at>now())
                )
            )
            ORDER BY v.created_at DESC, v.id DESC
            LIMIT %s OFFSET %s
          ) page ON TRUE
          WHERE s.token_hash=%s AND s.revoked_at IS NULL
            AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
          ORDER BY page.created_at DESC NULLS LAST,page.id DESC NULLS LAST""",
        (limit, offset, token_digest(token)),
    ).fetchall()
    if not rows:
        return None
    return [_vehicle(row) for row in rows if row[0] is not None]


def owned_vehicle(
    conn: psycopg.Connection, token: str, vehicle_id: UUID,
) -> tuple[bool, dict | None]:
    """(authenticated, vehicle): чужая/неподтверждённая машина → 404."""
    if not isinstance(token, str) or len(token) != 43:
        return False, None
    if not isinstance(vehicle_id, UUID):
        return False, None
    rows = conn.execute(
        """SELECT v.id,v.make,v.model,v.variant,v.model_year,v.vin
          FROM user_session s
          JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
          LEFT JOIN LATERAL (
            SELECT v.id,v.make,v.model,v.variant,v.model_year,v.vin
            FROM auto_vehicle v
            WHERE v.id=%s
              AND EXISTS (
                SELECT 1 FROM account_customer_link acl
                JOIN auto_vehicle_customer_link avcl
                  ON avcl.customer_id=acl.customer_id AND avcl.vehicle_id=v.id
                WHERE acl.user_id=u.id AND acl.revoked_at IS NULL
                  AND avcl.verified_at IS NOT NULL AND avcl.verified_by IS NOT NULL
                  AND avcl.revoked_at IS NULL
                  AND EXISTS (
                    SELECT 1 FROM user_role role
                    WHERE role.user_id=u.id AND role.role_code='CLIENT'
                      AND role.revoked_at IS NULL
                      AND (role.expires_at IS NULL OR role.expires_at>now())
                  )
              )
            LIMIT 1
          ) v ON TRUE
          WHERE s.token_hash=%s AND s.revoked_at IS NULL
            AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
          LIMIT 1""",
        (vehicle_id, token_digest(token)),
    ).fetchone()
    if rows is None:
        return False, None
    return True, _vehicle(rows) if rows[0] is not None else None
