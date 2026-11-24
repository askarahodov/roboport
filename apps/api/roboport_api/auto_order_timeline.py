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
            "INTAKE_ACT": ("INTAKE", "MANAGER"),
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
