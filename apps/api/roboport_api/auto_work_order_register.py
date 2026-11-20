"""Tenant-scoped, PII-free synthetic staff WorkOrder register.

This is a *read-only document locator*, not real document posting/payment.
The product's full AutoDealer parity and legal handover remain open.
"""
from __future__ import annotations

import hashlib
import csv
from io import StringIO
from datetime import datetime
from uuid import UUID

import psycopg

from .auto_document_access import require_document_access, DocumentPolicyDenied
from .keyset_cursor import decode_cursor, encode_cursor


class RegisterDenied(ValueError):
    pass


class RegisterInvalid(ValueError):
    pass


STAGES = frozenset((
    "ALL", "DRAFT", "IN_WORK", "READY", "POSTED",
    "CLIENT_ACKNOWLEDGED", "CLIENT_DISPUTED", "CLOSED_TECHNICAL", "CANCELED",
))
PAGE_SIZE = 25
EXPORT_LIMIT = 1000


def list_work_orders(conn: psycopg.Connection, token: str, org: UUID, loc: UUID,
                     *, stage: str = "ALL", order_id: UUID | None = None,
                     cursor: str | None = None, limit: int = PAGE_SIZE) -> dict:
    if (type(token) is not str or not token.strip()
        or type(org) is not UUID or type(loc) is not UUID):
        raise RegisterDenied("FORBIDDEN")
    if (stage not in STAGES or type(limit) is not int or
        limit not in (PAGE_SIZE, EXPORT_LIMIT) or
        (order_id is not None and type(order_id) is not UUID)):
        raise RegisterInvalid("INVALID_INPUT")
    scope = hashlib.sha256(
        f"work-order-register:{org}:{loc}:{stage}:{order_id}".encode("ascii")
    ).hexdigest()[:32]
    try:
        after = decode_cursor(cursor, scope, token) if cursor is not None else None
    except ValueError as exc:
        raise RegisterInvalid("INVALID_CURSOR") from exc

    with conn.transaction():
        try:
            # The register is a document read, not merely a role-gated locator.
            # Apply the live INTAKE_ACT policy (including user/branch DENY and
            # historical cutoff) before filtering, counting or paginating rows.
            history_cutoff = require_document_access(
                conn, token, org, loc, ("INTAKE", "MANAGER"),
                "INTAKE_ACT", "READ",
            )
        except DocumentPolicyDenied:
            raise RegisterDenied("FORBIDDEN") from None
        # Proof, org, branch and current booking are applied in SQL *before*
        # the page limit, so revoked customers and cross-tenant rows cannot
        # influence search results, pagination or counts.
        rows = conn.execute("""
            WITH available AS (
              SELECT w.id, w.created_at, a.id AS appointment_id,
                     COALESCE((
                       SELECT r.new_booking_id
                       FROM auto_appointment_reschedule r
                       WHERE r.appointment_id=a.id
                       ORDER BY r.created_at DESC,r.id DESC LIMIT 1
                     ),a.booking_id) AS current_booking
              FROM auto_work_order w
              JOIN auto_appointment a ON a.id=w.appointment_id
                AND a.organization_id=w.organization_id
                AND a.location_id=w.location_id
              JOIN auto_vehicle_customer_link p ON p.id=a.customer_link_id
                AND p.vehicle_id=a.vehicle_id
                AND p.verified_at IS NOT NULL AND p.verified_by IS NOT NULL
                AND p.revoked_at IS NULL
              JOIN auto_vehicle_location_binding binding
                ON binding.vehicle_id=a.vehicle_id
                AND binding.location_id=w.location_id
              JOIN auto_organization o ON o.id=w.organization_id
                AND o.disabled_at IS NULL
              JOIN auto_service_location l ON l.id=w.location_id
                AND l.organization_id=o.id AND l.disabled_at IS NULL
              WHERE w.organization_id=%s AND w.location_id=%s
                AND (%s::timestamptz IS NULL OR w.created_at >= %s)
            ),
            statuses AS (
              SELECT a.id,a.created_at,a.appointment_id,
                     booking.bay_id,booking.starts_at,booking.ends_at,
                     CASE
                       WHEN booking.canceled_at IS NOT NULL THEN 'CANCELED'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_order_demo_document_event event
                         WHERE event.work_order_id=a.id
                           AND event.action='CLOSED_TECHNICAL'
                       ) THEN 'CLOSED_TECHNICAL'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_order_demo_document_event event
                         WHERE event.work_order_id=a.id
                           AND event.action='CLIENT_DISPUTE'
                       ) THEN 'CLIENT_DISPUTED'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_order_demo_document_event event
                         WHERE event.work_order_id=a.id
                           AND event.action='CLIENT_ACK'
                       ) THEN 'CLIENT_ACKNOWLEDGED'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_order_demo_document_event event
                         WHERE event.work_order_id=a.id
                           AND event.action='POSTED'
                       ) THEN 'POSTED'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_work_order_execution_finish finished
                         JOIN auto_work_order_execution exec
                           ON exec.id=finished.execution_id
                         WHERE exec.work_order_id=a.id
                       ) THEN 'READY'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_work_order_execution exec
                         WHERE exec.work_order_id=a.id
                       ) THEN 'IN_WORK'
                       ELSE 'DRAFT'
                     END AS stage
              FROM available a
              JOIN auto_bay_booking booking ON booking.id=a.current_booking
                AND booking.organization_id=%s AND booking.location_id=%s
            )
            SELECT id,created_at,appointment_id,bay_id,starts_at,ends_at,stage
            FROM statuses
            WHERE (%s='ALL' OR stage=%s)
              AND (%s::uuid IS NULL OR id=%s::uuid)
              AND (%s::timestamptz IS NULL OR
                   (created_at,id)<(%s::timestamptz,%s::uuid))
            ORDER BY created_at DESC,id DESC
            LIMIT %s
        """, (org, loc, history_cutoff, history_cutoff, org, loc,
              stage, stage, order_id, order_id,
              after[0] if after else None,
              after[0] if after else None,
              after[1] if after else None, limit + 1)).fetchall()
        page = rows[:limit]
        items = [{
            "workOrderId": str(ident),
            "appointmentId": str(appointment),
            "bayId": str(bay),
            "createdAt": created.isoformat(),
            "startsAt": starts.isoformat(),
            "endsAt": ends.isoformat(),
            "stage": value,
        } for ident,created,appointment,bay,starts,ends,value in page]
        next_cursor = (encode_cursor(page[-1][1], page[-1][0], scope, token)
                       if len(rows)>limit else None)
        return {"syntheticOnly": True, "releaseAllowed": False,
                "items": items, "nextCursor": next_cursor}


def export_work_orders_csv(conn: psycopg.Connection, token: str, org: UUID,
                           loc: UUID, *, stage: str = "ALL",
                           order_id: UUID | None = None) -> str:
    """Complete, bounded, one-statement snapshot of the exact register filter.

    The same SQL, role checks and verified-link predicates power interactive
    pages. Never silently truncate a report or stream half a download.
    Only fixed-schema UUIDs, UTC offsets and enumerated stages leave the API.
    """
    report = list_work_orders(conn, token, org, loc, stage=stage,
                              order_id=order_id, limit=EXPORT_LIMIT)
    if report["nextCursor"] is not None:
        raise RegisterInvalid("EXPORT_LIMIT")
    out = StringIO(newline="")
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(("Заказ-наряд", "Запись", "Пост", "Создан",
                     "Начало записи", "Окончание записи", "Технический статус"))
    for row in report["items"]:
        # No free-text fields, formulas, prices, PII, or customer identifiers.
        # Assert the finite enum before CSV to keep every cell inert.
        if (row["stage"] not in STAGES or row["stage"] == "ALL"
                or any(not isinstance(row[key], str) or
                       not row[key] or row[key][0] in "=+-@\t\r\n"
                       for key in ("workOrderId", "appointmentId", "bayId",
                                   "createdAt", "startsAt", "endsAt"))):
            raise RegisterInvalid("EXPORT_INVALID")
        writer.writerow((row["workOrderId"], row["appointmentId"], row["bayId"],
                         row["createdAt"], row["startsAt"], row["endsAt"],
                         row["stage"]))
    return "\ufeff" + out.getvalue()
