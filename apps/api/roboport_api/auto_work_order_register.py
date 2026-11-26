"""Tenant-scoped, PII-free synthetic staff WorkOrder register.

This is a *read-only document locator*, not real document posting/payment.
The product's full AutoDealer parity and legal handover remain open.
"""
from __future__ import annotations

import hashlib
import csv
from io import StringIO
from datetime import date
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
REPAIR_STAGES = frozenset(('ALL','DRAFT','IN_WORK','READY'))
DOCUMENT_STAGES = frozenset(('ALL','RESTRICTED','DRAFT','PREVIEW_SEALED','POSTED',
                             'CLIENT_ACKNOWLEDGED','CLIENT_DISPUTED','CLOSED_TECHNICAL'))
BOOKING_STATES = frozenset(('ALL','SCHEDULED','CANCELED'))


def _day(value):
    if value is None:
        return None
    if type(value) is not str or len(value)!=10:
        raise RegisterInvalid('INVALID_INPUT')
    try:
        parsed=date.fromisoformat(value)
    except ValueError:
        raise RegisterInvalid('INVALID_INPUT') from None
    if parsed.isoformat()!=value:
        raise RegisterInvalid('INVALID_INPUT')
    return parsed


def list_work_orders(conn: psycopg.Connection, token: str, org: UUID, loc: UUID,
                     *, stage: str = "ALL", order_id: UUID | None = None,
                     cursor: str | None = None, limit: int = PAGE_SIZE,
                     view: str = 'legacy', repair_stage: str = 'ALL',
                     document_stage: str = 'ALL', booking_status: str = 'ALL',
                     from_day: str | None = None, to_day: str | None = None) -> dict:
    if (type(token) is not str or not token.strip()
        or type(org) is not UUID or type(loc) is not UUID):
        raise RegisterDenied("FORBIDDEN")
    if (stage not in STAGES or type(limit) is not int or
        limit not in (PAGE_SIZE, EXPORT_LIMIT) or
        (order_id is not None and type(order_id) is not UUID)):
        raise RegisterInvalid("INVALID_INPUT")
    first,last=_day(from_day),_day(to_day)
    if (view not in ('legacy','workflow') or repair_stage not in REPAIR_STAGES
        or document_stage not in DOCUMENT_STAGES or booking_status not in BOOKING_STATES
        or (first is not None and last is not None and first>last)
        or (view=='legacy' and (repair_stage,document_stage,booking_status,first,last)
            !=('ALL','ALL','ALL',None,None))):
        raise RegisterInvalid('INVALID_INPUT')
    key=f"work-order-register:{org}:{loc}:{stage}:{order_id}"
    if view=='workflow':
        key+=f":workflow:{repair_stage}:{document_stage}:{booking_status}:{first}:{last}"
    scope=hashlib.sha256(key.encode("ascii")).hexdigest()[:32]
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
        # Статусы проведения и решения клиента требуют отдельного HANDOVER READ.
        # Cutoff проверяется для каждого заказа до фильтрации и пагинации.
        # При запрете показываем только независимый операционный этап:
        # сам факт существования скрытого документа не меняет результат.
        try:
            handover_cutoff = require_document_access(
                conn, token, org, loc, ("MANAGER", "WAREHOUSE"),
                "HANDOVER", "READ",
            )
            handover_visible = True
        except DocumentPolicyDenied:
            handover_visible, handover_cutoff = False, None
        # Proof, org, branch and current booking are applied in SQL *before*
        # the page limit, so revoked customers and cross-tenant rows cannot
        # influence search results, pagination or counts.
        rows = conn.execute("""
            WITH available AS (
              SELECT w.id, w.created_at, a.id AS appointment_id, l.timezone_name,
                     (%s::boolean AND (%s::timestamptz IS NULL
                        OR w.created_at >= %s::timestamptz)) AS handover_visible,
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
            dimensions AS (
              SELECT a.id,a.created_at,a.appointment_id,
                     booking.bay_id,booking.starts_at,booking.ends_at,
                     (booking.starts_at AT TIME ZONE a.timezone_name)::date AS local_day,
                     CASE WHEN booking.canceled_at IS NULL THEN 'SCHEDULED'
                          ELSE 'CANCELED' END AS booking_status,
                     CASE
                       WHEN EXISTS (
                         SELECT 1 FROM auto_work_order_execution_finish finished
                         JOIN auto_work_order_execution exec ON exec.id=finished.execution_id
                         WHERE exec.work_order_id=a.id
                       ) THEN 'READY'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_work_order_execution exec WHERE exec.work_order_id=a.id
                       ) THEN 'IN_WORK'
                       ELSE 'DRAFT'
                     END AS repair_stage,
                     CASE
                       WHEN NOT a.handover_visible THEN 'RESTRICTED'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_order_demo_document_event event
                         WHERE event.work_order_id=a.id AND event.action='CLOSED_TECHNICAL'
                       ) THEN 'CLOSED_TECHNICAL'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_order_demo_document_event event
                         WHERE event.work_order_id=a.id AND event.action='CLIENT_DISPUTE'
                       ) THEN 'CLIENT_DISPUTED'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_order_demo_document_event event
                         WHERE event.work_order_id=a.id AND event.action='CLIENT_ACK'
                       ) THEN 'CLIENT_ACKNOWLEDGED'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_order_demo_document_event event
                         WHERE event.work_order_id=a.id AND event.action='POSTED'
                       ) THEN 'POSTED'
                       WHEN EXISTS (
                         SELECT 1 FROM auto_order_demo_handover_preview preview
                         WHERE preview.work_order_id=a.id
                       ) THEN 'PREVIEW_SEALED'
                       ELSE 'DRAFT'
                     END AS document_stage
              FROM available a
              JOIN auto_bay_booking booking ON booking.id=a.current_booking
                AND booking.organization_id=%s AND booking.location_id=%s
            ),
            statuses AS (
              SELECT *,CASE
                WHEN booking_status='CANCELED' THEN 'CANCELED'
                WHEN document_stage IN ('POSTED','CLIENT_ACKNOWLEDGED',
                                        'CLIENT_DISPUTED','CLOSED_TECHNICAL')
                  THEN document_stage
                ELSE repair_stage
              END AS stage
              FROM dimensions
            )
            SELECT id,created_at,appointment_id,bay_id,starts_at,ends_at,stage,
                   repair_stage,document_stage,booking_status,local_day
            FROM statuses
            WHERE (%s='ALL' OR stage=%s)
              AND (%s::uuid IS NULL OR id=%s::uuid)
              AND (%s='ALL' OR repair_stage=%s)
              AND (%s='ALL' OR document_stage=%s)
              AND (%s='ALL' OR booking_status=%s)
              AND (%s::date IS NULL OR local_day >= %s::date)
              AND (%s::date IS NULL OR local_day <= %s::date)
              AND (%s::timestamptz IS NULL OR
                   (created_at,id)<(%s::timestamptz,%s::uuid))
            ORDER BY created_at DESC,id DESC
            LIMIT %s
        """, (handover_visible, handover_cutoff, handover_cutoff,
              org, loc, history_cutoff, history_cutoff, org, loc,
              stage, stage, order_id, order_id,
              repair_stage,repair_stage,document_stage,document_stage,
              booking_status,booking_status,first,first,last,last,
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
            **({"repairStage": repair, "documentStage": document,
                "bookingStatus": booking_status_value, "localDay": local_day.isoformat()}
               if view=="workflow" else {}),
        } for ident,created,appointment,bay,starts,ends,value,repair,document,booking_status_value,local_day in page]
        next_cursor = (encode_cursor(page[-1][1], page[-1][0], scope, token)
                       if len(rows)>limit else None)
        return {"syntheticOnly": True, "releaseAllowed": False,
                "items": items, "nextCursor": next_cursor}


def export_work_orders_csv(conn: psycopg.Connection, token: str, org: UUID,
                           loc: UUID, *, stage: str = "ALL",
                           order_id: UUID | None = None, view: str = 'legacy',
                           repair_stage: str = 'ALL', document_stage: str = 'ALL',
                           booking_status: str = 'ALL', from_day=None,to_day=None) -> str:
    """Complete, bounded, one-statement snapshot of the exact register filter.

    The same SQL, role checks and verified-link predicates power interactive
    pages. Never silently truncate a report or stream half a download.
    Only fixed-schema UUIDs, UTC offsets and enumerated stages leave the API.
    """
    report = list_work_orders(conn, token, org, loc, stage=stage,
                              order_id=order_id, limit=EXPORT_LIMIT,view=view,
                              repair_stage=repair_stage,document_stage=document_stage,
                              booking_status=booking_status,from_day=from_day,to_day=to_day)
    if report["nextCursor"] is not None:
        raise RegisterInvalid("EXPORT_LIMIT")
    out = StringIO(newline="")
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(("Заказ-наряд", "Запись", "Пост", "Создан",
                     "Начало записи", "Окончание записи", "Технический статус")+
                    (("Этап ремонта","Состояние документа","Состояние записи","Локальный день")
                     if view=="workflow" else ()))
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
                         row["stage"])+
                        ((row["repairStage"],row["documentStage"],row["bookingStatus"],row["localDay"])
                         if view=="workflow" else ()))
    return "\ufeff" + out.getvalue()
