"""Historical technical WorkOrder evidence from immutable PG17 sources.

A chronological staff read, not a financial ledger, signature or proof of
physical handover. The same live tenant/branch/vehicle proof as workspace
is required on every cursor page. No customer identifiers, prices or actors.
"""
from __future__ import annotations

import hashlib
from uuid import UUID

import psycopg

from .auto_order_customer_read import require_order_staff_scope
from .auto_stock_service import AutoStockDenied
from .auto_org_authority import organization_role_allows
from .keyset_cursor import decode_cursor, encode_cursor
from .auto_document_access import require_document_access, DocumentPolicyDenied


def read_order_timeline(conn: psycopg.Connection, token: str, org: UUID,
                        loc: UUID, order: UUID, *, limit: int = 25,
                        cursor: str | None = None) -> dict:
    if (type(token) is not str or not token.strip()
        or any(type(x) is not UUID for x in (org, loc, order))):
        raise AutoStockDenied("FORBIDDEN")
    if type(limit) is not int or not 1 <= limit <= 25:
        raise AutoStockDenied("INVALID_INPUT")
    scope = hashlib.sha256(
        f"auto-order-timeline:{org}:{loc}:{order}:{limit}".encode("ascii")
    ).hexdigest()[:32]
    try:
        after = decode_cursor(cursor, scope, token) if cursor is not None else None
    except ValueError as exc:
        raise AutoStockDenied("INVALID_INPUT") from exc
    with conn.transaction():
        require_order_staff_scope(conn, token, org, loc, order)
        # Document ACL and not_before apply to events as well as documents.
        # A generic INTAKE/MANAGER workspace grant must not reveal the
        # existence, timestamps or client decisions of forbidden documents.
        # Evaluate policies BEFORE paginating, preserving keyset integrity.
        document_roles = {
            "INTAKE_ACT": ("INTAKE", "MANAGER", "MECHANIC"),
            "ESTIMATE": ("MANAGER",),
            "HANDOVER": ("MANAGER",),
        }
        visible_documents = set()
        for kind, roles in document_roles.items():
            try:
                require_document_access(
                    conn, token, org, loc, roles, kind, "READ", order=order,
                )
            except DocumentPolicyDenied:
                continue
            visible_documents.add(kind)
        # Новые семейства требуют текущую бронь и исходное доказательство.
        # Не повышаем SHARE до UPDATE при чтении исторического журнала.
        live_booking = conn.execute("""
            SELECT 1 FROM auto_work_order w
            JOIN auto_appointment a ON a.id=w.appointment_id
              AND a.organization_id=w.organization_id AND a.location_id=w.location_id
            JOIN auto_bay_booking b ON b.id=COALESCE((
                SELECT r.new_booking_id FROM auto_appointment_reschedule r
                WHERE r.appointment_id=a.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1
            ),a.booking_id) AND b.organization_id=w.organization_id
              AND b.location_id=w.location_id AND b.canceled_at IS NULL
            JOIN auto_vehicle_customer_link p ON p.id=a.customer_link_id
              AND p.vehicle_id=a.vehicle_id AND p.verified_at IS NOT NULL
              AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
            WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
            FOR SHARE OF b,p
        """, (order,org,loc)).fetchone() is not None
        if live_booking:
            if "INTAKE_ACT" in visible_documents:
                visible_documents.add("LIVE_INTAKE")
                if "ESTIMATE" in visible_documents:
                    try:
                        require_document_access(conn,token,org,loc,("MANAGER",),
                                                "ESTIMATE","PRICE_SALES",order=order)
                    except DocumentPolicyDenied:
                        pass
                    else:
                        visible_documents.add("PRICED_ESTIMATE")
            if organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True):
                visible_documents.add("ASSIGNMENT")
        # Every branch has the *same* order identity parameter. Do not filter
        # after LIMIT: this query orders immutable evidence across all stages.
        # Last field identifies document-scoped event families.
        sources = (
            ("auto_work_order", "id", "'ORDER_CREATED'", None),
            ("auto_work_order_service", "id", "'SERVICE_SELECTED'", None),
            ("auto_work_order_inspection", "id", "'INSPECTION_RECORDED'", "INTAKE_ACT"),
            ("auto_work_order_intake_act", "id", "'INTAKE_ACT_CREATED'", "INTAKE_ACT"),
            ("auto_work_order_intake_act_review", "id",
             "'INTAKE_' || decision", "INTAKE_ACT"),
            ("auto_work_order_estimate_revision", "id", "'ESTIMATE_PUBLISHED'", "ESTIMATE"),
            ("auto_work_order_estimate_decision", "id",
             "'ESTIMATE_' || decision", "ESTIMATE"),
            ("auto_work_order_execution", "id", "'EXECUTION_STARTED'", None),
            ("auto_work_order_labor_entry", "id", "'LABOR_RECORDED'", None),
            ("auto_work_order_execution_finish", "id", "'EXECUTION_FINISHED'", None),
            ("auto_work_order_qc_event", "id", "'QC_' || result", "INTAKE_ACT"),
            ("auto_work_order_diagnostic", "id", "'DIAGNOSTIC_RECORDED'", "LIVE_INTAKE"),
            ("auto_work_order_qc_rework", "id", "'QC_REWORK_RECORDED'", "LIVE_INTAKE"),
            ("auto_work_order_complex_selection", "id", "'COMPLEX_SELECTED'", "PRICED_ESTIMATE"),
            ("auto_work_order_extra_revision", "id", "'EXTRA_PUBLISHED'", "PRICED_ESTIMATE"),
            ("auto_work_order_extra_decision", "id", "'EXTRA_' || decision", "PRICED_ESTIMATE"),
            ("auto_work_order_assignment_event", "id", "'ASSIGNMENT_' || action", "ASSIGNMENT"),
            ("auto_service_recommendation", "id", "'RECOMMENDATION_CREATED'", "LIVE_INTAKE"),
            ("auto_service_recommendation_withdrawal", "id", "'RECOMMENDATION_WITHDRAWN'", "LIVE_INTAKE"),
            ("auto_recommendation_visit", "id", "'RECOMMENDATION_VISIT_BOOKED'", "LIVE_INTAKE"),
            ("auto_order_demo_handover_preview", "id", "'PREVIEW_SEALED'", "HANDOVER"),
            ("auto_order_demo_document_event", "id",
             "'DOCUMENT_' || action", "HANDOVER"),
        )
        selects = []
        args = []
        for table, ident, action, document_kind in sources:
            if document_kind is not None and document_kind not in visible_documents:
                continue
            if table in ("auto_work_order_labor_entry",
                         "auto_work_order_execution_finish"):
                selects.append(
                    f"""SELECT item.{ident} AS evidence_id, item.created_at AS at,
                        {action} AS action
                        FROM {table} item
                        JOIN auto_work_order_execution exec
                          ON exec.id=item.execution_id
                        WHERE exec.work_order_id=%s"""
                )
            elif table == "auto_work_order_assignment_event":
                selects.append(
                    f"""SELECT item.{ident} AS evidence_id, item.created_at AS at,
                        {action} AS action FROM {table} item
                        JOIN auto_work_order_assignment a ON a.id=item.assignment_id
                        WHERE a.work_order_id=%s"""
                )
            elif table in ("auto_service_recommendation_withdrawal",
                           "auto_recommendation_visit"):
                selects.append(
                    f"""SELECT item.{ident} AS evidence_id, item.created_at AS at,
                        {action} AS action FROM {table} item
                        JOIN auto_service_recommendation r ON r.id=item.recommendation_id
                        WHERE r.work_order_id=%s"""
                )
            else:
                key = "id" if table == "auto_work_order" else "work_order_id"
                selects.append(
                    f"""SELECT item.{ident} AS evidence_id, item.created_at AS at,
                        {action} AS action
                        FROM {table} item WHERE item.{key}=%s"""
                )
            args.append(order)
        rows = conn.execute(
            "WITH evidence AS (" + " UNION ALL ".join(selects) + """)
             SELECT evidence_id,at,action FROM evidence
             WHERE (%s::timestamptz IS NULL OR
               (at,evidence_id)<(%s::timestamptz,%s::uuid))
             ORDER BY at DESC,evidence_id DESC LIMIT %s""",
            tuple(args + [after[0] if after else None,
                          after[0] if after else None,
                          after[1] if after else None, limit+1]),
        ).fetchall()
        page = rows[:limit]
        next_cursor = (
            encode_cursor(page[-1][1], page[-1][0], scope, token)
            if len(rows) > limit else None
        )
        return {
            "syntheticOnly": True, "realMoney": False, "releaseAllowed": False,
            "workOrderId": str(order),
            "items": [{"evidenceId": str(ident), "createdAt": at.isoformat(),
                       "action": action}
                      for ident, at, action in page],
            "nextCursor": next_cursor,
        }
