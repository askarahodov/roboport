"""Управление филиалами, постами и сменами под полномочием ADMIN организации.

Только synthetic/dev. Создание реальной организации и проверка её владельца
остаются отдельным внешним процессом; этот модуль не создаёт ADMIN.
"""
from datetime import datetime, timezone, timedelta
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from .session_lookup import session_subject
from .auto_create_idempotency import (
    AutoIdempotencyConflict, existing as existing_receipt,
    remember as remember_receipt, validated_key,
)


_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,31}$")
_STAFF_ROLES = ("MANAGER", "INTAKE", "MECHANIC", "WAREHOUSE", "CASHIER")


class WorkshopAdminDenied(ValueError):
    pass


class WorkshopAdminInvalid(ValueError):
    pass


class WorkshopAdminConflict(ValueError):
    pass


def _admin(conn: psycopg.Connection, token: str, organization: UUID) -> UUID:
    if type(organization) is not UUID or type(token) is not str or not token:
        raise WorkshopAdminDenied("invalid organization or session")
    actor = session_subject(conn, token, for_mutation=True)
    if actor is None:
        raise WorkshopAdminDenied("session unavailable")
    if conn.execute(
        """SELECT id FROM auto_organization
           WHERE id=%s AND disabled_at IS NULL FOR SHARE""",
        (organization,),
    ).fetchone() is None:
        raise WorkshopAdminDenied("organization unavailable")
    if conn.execute(
        """SELECT id FROM auto_organization_role_grant
           WHERE organization_id=%s AND user_id=%s
             AND role_code='ADMIN' AND location_id IS NULL
             AND revoked_at IS NULL FOR SHARE""",
        (organization, actor),
    ).fetchone() is None:
        raise WorkshopAdminDenied("organization admin required")
    return actor



def _required_key(raw: str | None) -> str:
    try:
        key = validated_key(raw)
    except ValueError as exc:
        raise WorkshopAdminInvalid("invalid Idempotency-Key") from exc
    if key is None:
        raise WorkshopAdminInvalid("Idempotency-Key is required")
    return key


def _record(conn: psycopg.Connection, organization: UUID, location: UUID,
            actor: UUID, entity_type: str, entity: UUID, action: str) -> None:
    conn.execute(
        """INSERT INTO auto_workshop_admin_action
           (id,organization_id,location_id,actor_id,entity_type,entity_id,action)
           VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (uuid4(), organization, location, actor, entity_type, entity, action),
    )


def list_workshop(conn: psycopg.Connection, token: str,
                  organization: UUID) -> dict:
    """Bounded branch, bay and shift registry; identifiers only, no PII."""
    with conn.transaction():
        _admin(conn, token, organization)
        locations = conn.execute(
            """SELECT id,code,timezone_name,disabled_at,created_at
               FROM auto_service_location
               WHERE organization_id=%s
               ORDER BY code,id LIMIT 101""",
            (organization,),
        ).fetchall()
        bays = conn.execute(
            """SELECT id,location_id,code,disabled_at,created_at
               FROM auto_work_bay WHERE organization_id=%s
               ORDER BY location_id,code,id LIMIT 101""",
            (organization,),
        ).fetchall()
        shifts = conn.execute(
            """SELECT id,location_id,user_id,starts_at,ends_at,canceled_at,created_at
               FROM auto_staff_shift WHERE organization_id=%s
               ORDER BY starts_at DESC,id DESC LIMIT 101""",
            (organization,),
        ).fetchall()
        return {
            "syntheticOnly": True,
            "locations": [
                {"locationId": str(row[0]), "code": row[1], "timeZone": row[2],
                 "disabled": row[3] is not None, "createdAt": row[4].isoformat()}
                for row in locations[:100]
            ],
            "locationsTruncated": len(locations) > 100,
            "bays": [
                {"bayId": str(row[0]), "locationId": str(row[1]), "code": row[2],
                 "disabled": row[3] is not None, "createdAt": row[4].isoformat()}
                for row in bays[:100]
            ],
            "baysTruncated": len(bays) > 100,
            "shifts": [
                {"shiftId": str(row[0]), "locationId": str(row[1]),
                 "userId": str(row[2]), "startsAt": row[3].isoformat(),
                 "endsAt": row[4].isoformat(), "canceled": row[5] is not None,
                 "createdAt": row[6].isoformat()}
                for row in shifts[:100]
            ],
            "shiftsTruncated": len(shifts) > 100,
        }


def list_workshop_audit(conn: psycopg.Connection, token: str,
                        organization: UUID, limit: int = 100) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise WorkshopAdminInvalid("invalid audit limit")
    with conn.transaction():
        _admin(conn, token, organization)
        rows = conn.execute(
            """SELECT id,location_id,actor_id,entity_type,entity_id,action,created_at
               FROM auto_workshop_admin_action
               WHERE organization_id=%s
               ORDER BY created_at DESC,id DESC LIMIT %s""",
            (organization, limit),
        ).fetchall()
        return [
            {"eventId": str(row[0]), "locationId": str(row[1]),
             "actorId": str(row[2]), "entityType": row[3],
             "entityId": str(row[4]), "action": row[5],
             "createdAt": row[6].isoformat()}
            for row in rows
        ]


def create_location(conn: psycopg.Connection, token: str, organization: UUID,
                    code: str, time_zone: str,
                    idempotency_key: str | None) -> UUID:
    if (type(code) is not str or _CODE.fullmatch(code) is None
            or type(time_zone) is not str or not 3 <= len(time_zone) <= 64):
        raise WorkshopAdminInvalid("invalid branch code or time zone")
    if time_zone != "UTC" and re.fullmatch(
        r"^[A-Za-z][A-Za-z0-9_+-]*/[A-Za-z0-9_+/-]+$", time_zone
    ) is None:
        raise WorkshopAdminInvalid("time zone must be UTC or an IANA area/name")
    try:
        ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise WorkshopAdminInvalid("unknown IANA time zone") from exc
    key = _required_key(idempotency_key)
    with conn.transaction():
        actor = _admin(conn, token, organization)
        if conn.execute(
            "SELECT 1 FROM pg_timezone_names WHERE name=%s", (time_zone,)
        ).fetchone() is None:
            raise WorkshopAdminInvalid("unknown PostgreSQL time zone")
        payload = {"objectId": str(organization), "code": code, "timeZone": time_zone}
        try:
            replay = existing_receipt(
                conn, actor, "workshop.location.create", key, payload
            )
        except AutoIdempotencyConflict as exc:
            raise WorkshopAdminConflict("Idempotency-Key reused with another payload") from exc
        if replay is not None:
            row = conn.execute(
                """SELECT id FROM auto_service_location
                   WHERE id=%s AND organization_id=%s AND code=%s
                     AND timezone_name=%s AND disabled_at IS NULL""",
                (replay, organization, code, time_zone),
            ).fetchone()
            if row is None:
                raise WorkshopAdminConflict("previous branch result is no longer active")
            return replay
        location = uuid4()
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_service_location
                       (id,code,organization_id,timezone_name)
                       VALUES(%s,%s,%s,%s)""",
                    (location, code, organization, time_zone),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise WorkshopAdminConflict("branch code already exists") from exc
        _record(conn, organization, location, actor,
                "LOCATION", location, "LOCATION_CREATED")
        remember_receipt(
            conn, actor, "workshop.location.create", key, payload, location
        )
        return location


def disable_location(conn: psycopg.Connection, token: str,
                     organization: UUID, location: UUID) -> str:
    if type(location) is not UUID:
        raise WorkshopAdminInvalid("invalid branch")
    with conn.transaction():
        actor = _admin(conn, token, organization)
        row = conn.execute(
            """SELECT id,disabled_at FROM auto_service_location
               WHERE id=%s AND organization_id=%s FOR UPDATE""",
            (location, organization),
        ).fetchone()
        if row is None:
            raise WorkshopAdminDenied("branch unavailable")
        if row[1] is not None:
            return "ALREADY_DISABLED"
        # This FOR UPDATE lock serializes retirement with branch-scoped
        # INSERT guards taking FOR SHARE; after waiting, the checks below see
        # every create that committed first, while later creates see disabled_at.
        active = conn.execute(
            """SELECT
              EXISTS(SELECT 1 FROM auto_work_bay
                     WHERE location_id=%s AND disabled_at IS NULL)
              OR EXISTS(SELECT 1 FROM auto_staff_shift
                        WHERE location_id=%s AND canceled_at IS NULL)
              OR EXISTS(SELECT 1 FROM auto_bay_booking
                        WHERE location_id=%s AND canceled_at IS NULL
                          AND ends_at>clock_timestamp())
              OR EXISTS(SELECT 1 FROM auto_organization_role_grant
                        WHERE location_id=%s AND revoked_at IS NULL)
              OR EXISTS(SELECT 1 FROM auto_location_role_grant
                        WHERE location_id=%s AND revoked_at IS NULL)
              OR EXISTS(SELECT 1 FROM auto_vehicle_location_binding
                        WHERE location_id=%s)""",
            (location, location, location, location, location, location),
        ).fetchone()[0]
        if active:
            raise WorkshopAdminConflict(
                "disable bays, cancel shifts, revoke branch grants and transfer vehicles first"
            )
        try:
            with conn.transaction():
                conn.execute(
                    """UPDATE auto_service_location
                       SET disabled_at=GREATEST(clock_timestamp(),created_at)
                       WHERE id=%s AND organization_id=%s AND disabled_at IS NULL""",
                    (location, organization),
                )
        except psycopg.errors.RaiseException as exc:
            raise WorkshopAdminConflict("branch still has active resources") from exc
        _record(conn, organization, location, actor,
                "LOCATION", location, "LOCATION_DISABLED")
        return "DISABLED"


def create_bay(conn: psycopg.Connection, token: str, organization: UUID,
               location: UUID, code: str,
               idempotency_key: str | None) -> UUID:
    if (type(location) is not UUID or type(code) is not str
            or _CODE.fullmatch(code) is None):
        raise WorkshopAdminInvalid("invalid bay code or branch")
    key = _required_key(idempotency_key)
    with conn.transaction():
        actor = _admin(conn, token, organization)
        if conn.execute(
            """SELECT id FROM auto_service_location
               WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
               FOR SHARE""",
            (location, organization),
        ).fetchone() is None:
            raise WorkshopAdminDenied("branch unavailable")
        payload = {"objectId": str(location), "code": code}
        try:
            replay = existing_receipt(
                conn, actor, "workshop.bay.create", key, payload
            )
        except AutoIdempotencyConflict as exc:
            raise WorkshopAdminConflict("Idempotency-Key reused with another payload") from exc
        if replay is not None:
            row = conn.execute(
                """SELECT id FROM auto_work_bay
                   WHERE id=%s AND organization_id=%s AND location_id=%s
                     AND code=%s AND disabled_at IS NULL""",
                (replay, organization, location, code),
            ).fetchone()
            if row is None:
                raise WorkshopAdminConflict("previous bay result is no longer active")
            return replay
        bay = uuid4()
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_work_bay
                       (id,organization_id,location_id,code)
                       VALUES(%s,%s,%s,%s)""",
                    (bay, organization, location, code),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise WorkshopAdminConflict("bay code already exists in this branch") from exc
        _record(conn, organization, location, actor, "BAY", bay, "BAY_CREATED")
        remember_receipt(conn, actor, "workshop.bay.create", key, payload, bay)
        return bay


def disable_bay(conn: psycopg.Connection, token: str, organization: UUID,
                location: UUID, bay: UUID) -> str:
    if type(location) is not UUID or type(bay) is not UUID:
        raise WorkshopAdminInvalid("invalid bay")
    with conn.transaction():
        actor = _admin(conn, token, organization)
        row = conn.execute(
            """SELECT disabled_at,created_at FROM auto_work_bay
               WHERE id=%s AND organization_id=%s AND location_id=%s
               FOR UPDATE""",
            (bay, organization, location),
        ).fetchone()
        if row is None:
            raise WorkshopAdminDenied("bay unavailable")
        if row[0] is not None:
            return "ALREADY_DISABLED"
        if conn.execute(
            """SELECT 1 FROM auto_bay_booking
               WHERE bay_id=%s AND organization_id=%s AND location_id=%s
                 AND canceled_at IS NULL AND ends_at>clock_timestamp()
               LIMIT 1""",
            (bay, organization, location),
        ).fetchone() is not None:
            raise WorkshopAdminConflict("bay has active future bookings")
        try:
            with conn.transaction():
                conn.execute(
                    """UPDATE auto_work_bay
                       SET disabled_at=GREATEST(clock_timestamp(),created_at)
                       WHERE id=%s AND organization_id=%s AND location_id=%s
                         AND disabled_at IS NULL""",
                    (bay, organization, location),
                )
        except psycopg.errors.RaiseException as exc:
            raise WorkshopAdminConflict("bay has active future bookings") from exc
        _record(conn, organization, location, actor, "BAY", bay, "BAY_DISABLED")
        return "DISABLED"


def create_shift(conn: psycopg.Connection, token: str, organization: UUID,
                 location: UUID, employee: UUID,
                 starts: datetime, ends: datetime,
                 idempotency_key: str | None) -> UUID:
    if (type(location) is not UUID or type(employee) is not UUID
            or not all(isinstance(value, datetime) and value.tzinfo is not None
                       and value.utcoffset() is not None for value in (starts, ends))
            or ends.astimezone(timezone.utc) <= starts.astimezone(timezone.utc)
            or ends.astimezone(timezone.utc)-starts.astimezone(timezone.utc) > timedelta(hours=24)):
        raise WorkshopAdminInvalid("shift requires a positive timezone-aware interval up to 24 hours")
    key = _required_key(idempotency_key)
    with conn.transaction():
        actor = _admin(conn, token, organization)
        if conn.execute(
            """SELECT id FROM auto_service_location
               WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
               FOR SHARE""",
            (location, organization),
        ).fetchone() is None:
            raise WorkshopAdminDenied("branch unavailable")
        if conn.execute(
            "SELECT id FROM app_user WHERE id=%s AND status='ACTIVE' FOR SHARE",
            (employee,),
        ).fetchone() is None:
            raise WorkshopAdminDenied("active employee account required")
        if conn.execute(
            """SELECT id FROM auto_organization_role_grant
               WHERE organization_id=%s AND user_id=%s
                 AND role_code=ANY(%s::text[]) AND revoked_at IS NULL
                 AND (location_id IS NULL OR location_id=%s)
               ORDER BY id LIMIT 1 FOR SHARE""",
            (organization, employee, list(_STAFF_ROLES), location),
        ).fetchone() is None:
            raise WorkshopAdminDenied("employee needs an active organization or branch role")
        payload = {
            "objectId": str(location), "employeeId": str(employee),
            "startsAt": starts.isoformat(), "endsAt": ends.isoformat(),
        }
        try:
            replay = existing_receipt(
                conn, actor, "workshop.shift.create", key, payload
            )
        except AutoIdempotencyConflict as exc:
            raise WorkshopAdminConflict("Idempotency-Key reused with another payload") from exc
        if replay is not None:
            row = conn.execute(
                """SELECT user_id,starts_at,ends_at,canceled_at FROM auto_staff_shift
                   WHERE id=%s AND organization_id=%s AND location_id=%s""",
                (replay, organization, location),
            ).fetchone()
            if (row is None or row[3] is not None or row[0] != employee
                    or row[1] != starts or row[2] != ends):
                raise WorkshopAdminConflict("previous shift result is no longer active")
            return replay
        shift = uuid4()
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_staff_shift
                       (id,organization_id,location_id,user_id,starts_at,ends_at)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    (shift, organization, location, employee, starts, ends),
                )
        except psycopg.errors.ExclusionViolation as exc:
            raise WorkshopAdminConflict("employee already has an overlapping shift") from exc
        except psycopg.errors.CheckViolation as exc:
            raise WorkshopAdminInvalid("shift interval violates database constraints") from exc
        _record(conn, organization, location, actor, "SHIFT", shift, "SHIFT_CREATED")
        remember_receipt(conn, actor, "workshop.shift.create", key, payload, shift)
        return shift


def cancel_shift(conn: psycopg.Connection, token: str, organization: UUID,
                 location: UUID, shift: UUID) -> str:
    if type(location) is not UUID or type(shift) is not UUID:
        raise WorkshopAdminInvalid("invalid shift")
    with conn.transaction():
        actor = _admin(conn, token, organization)
        row = conn.execute(
            """SELECT user_id,starts_at,ends_at,canceled_at,created_at
               FROM auto_staff_shift
               WHERE id=%s AND organization_id=%s AND location_id=%s
               FOR UPDATE""",
            (shift, organization, location),
        ).fetchone()
        if row is None:
            raise WorkshopAdminDenied("shift unavailable")
        if row[3] is not None:
            return "ALREADY_CANCELED"
        if conn.execute(
            """SELECT 1 FROM auto_employee_reservation
               WHERE organization_id=%s AND location_id=%s AND employee_id=%s
                 AND canceled_at IS NULL AND starts_at<%s AND ends_at>%s
               LIMIT 1""",
            (organization, location, row[0], row[2], row[1]),
        ).fetchone() is not None:
            raise WorkshopAdminConflict("shift has active employee reservations")
        try:
            with conn.transaction():
                conn.execute(
                    """UPDATE auto_staff_shift
                       SET canceled_at=GREATEST(clock_timestamp(),created_at)
                       WHERE id=%s AND canceled_at IS NULL""",
                    (shift,),
                )
        except psycopg.errors.RaiseException as exc:
            raise WorkshopAdminConflict("shift has active employee reservations") from exc
        _record(conn, organization, location, actor, "SHIFT", shift, "SHIFT_CANCELED")
        return "CANCELED"
