"""Read-only stage checklist for a single synthetic WorkOrder.

Never returns prices, customer identity, financial transactions or release authority.
It is navigation guidance, not a substitute for each stage's document ACL.
"""
from uuid import UUID

import psycopg

from .auto_order_customer_read import get_order_customer_summary
from .auto_stock_service import AutoStockDenied


def read_order_workspace(conn: psycopg.Connection, token: str,
                         org: UUID, loc: UUID, order: UUID) -> dict:
    if (type(token) is not str or not token.strip()
            or any(type(x) is not UUID for x in (org, loc, order))):
        raise AutoStockDenied("FORBIDDEN")
    with conn.transaction():
        # Locks session, role, organization, branch, original proof and identity
        # for the duration of the complete state snapshot.
        get_order_customer_summary(conn, token, org, loc, order)
        row = conn.execute("""
            SELECT
              (SELECT count(*) FROM auto_work_order_service s
               WHERE s.work_order_id=w.id)::integer AS services,
              EXISTS(SELECT 1 FROM auto_work_order_inspection inspection
                     WHERE inspection.work_order_id=w.id) AS inspected,
              (SELECT act.version FROM auto_work_order_intake_act act
               WHERE act.work_order_id=w.id ORDER BY act.version DESC LIMIT 1)
                 AS intake_version,
              (SELECT review.decision FROM auto_work_order_intake_act act
               LEFT JOIN auto_work_order_intake_act_review review ON review.act_id=act.id
               WHERE act.work_order_id=w.id ORDER BY act.version DESC LIMIT 1)
                 AS intake_decision,
              (SELECT rev.version FROM auto_work_order_estimate_revision rev
               WHERE rev.work_order_id=w.id ORDER BY rev.version DESC LIMIT 1)
                 AS estimate_version,
              (SELECT decision.decision FROM auto_work_order_estimate_revision rev
               LEFT JOIN auto_work_order_estimate_decision decision
                 ON decision.revision_id=rev.id
               WHERE rev.work_order_id=w.id ORDER BY rev.version DESC LIMIT 1)
                 AS estimate_decision,
              EXISTS(SELECT 1 FROM auto_work_order_execution execution
                     WHERE execution.work_order_id=w.id) AS started,
              EXISTS(SELECT 1 FROM auto_work_order_execution_finish finish
                     JOIN auto_work_order_execution execution
                       ON execution.id=finish.execution_id
                     WHERE execution.work_order_id=w.id) AS finished,
              (SELECT qc.result FROM auto_work_order_qc_event qc
               WHERE qc.work_order_id=w.id ORDER BY qc.created_at DESC,qc.id DESC LIMIT 1)
                 AS qc_result,
              EXISTS(SELECT 1 FROM auto_order_demo_handover_preview preview
                     WHERE preview.work_order_id=w.id) AS sealed_preview,
              EXISTS(SELECT 1 FROM auto_order_demo_document_event doc
                     WHERE doc.work_order_id=w.id AND doc.action='POSTED') AS posted,
              EXISTS(SELECT 1 FROM auto_order_demo_document_event doc
                     WHERE doc.work_order_id=w.id AND doc.action='CLIENT_ACK') AS client_ack,
              EXISTS(SELECT 1 FROM auto_order_demo_document_event doc
                     WHERE doc.work_order_id=w.id AND doc.action='CLIENT_DISPUTE') AS disputed,
              EXISTS(SELECT 1 FROM auto_order_demo_document_event doc
                     WHERE doc.work_order_id=w.id AND doc.action='CLOSED_TECHNICAL') AS closed,
              booking.canceled_at IS NOT NULL AS canceled
            FROM auto_work_order w
            JOIN auto_appointment a ON a.id=w.appointment_id
              AND a.organization_id=w.organization_id AND a.location_id=w.location_id
            JOIN LATERAL (
              SELECT COALESCE((
                SELECT moved.new_booking_id FROM auto_appointment_reschedule moved
                WHERE moved.appointment_id=a.id
                ORDER BY moved.created_at DESC,moved.id DESC LIMIT 1
              ),a.booking_id) AS current_id
            ) current_booking ON TRUE
            JOIN auto_bay_booking booking ON booking.id=current_booking.current_id
              AND booking.organization_id=w.organization_id
              AND booking.location_id=w.location_id
            WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
        """, (order, org, loc)).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        (services,inspected,intake_version,intake_decision,estimate_version,
         estimate_decision,started,finished,qc_result,preview,posted,
         client_ack,disputed,closed,canceled)=row
        if closed:
            document="CLOSED_TECHNICAL"
        elif disputed:
            document="CLIENT_DISPUTED"
        elif client_ack:
            document="CLIENT_ACKNOWLEDGED"
        elif posted:
            document="POSTED"
        elif preview:
            document="PREVIEW_SEALED"
        else:
            document="DRAFT"
        return {
            "workOrderId": str(order),
            "syntheticOnly": True,
            "realMoney": False,
            "releaseAllowed": False,
            "bookingCanceled": bool(canceled),
            "steps": {
                "services": "SELECTED" if services else "EMPTY",
                "inspection": "RECORDED" if inspected else "MISSING",
                "intakeAct": (intake_decision or "AWAITING_CLIENT")
                             if intake_version is not None else "MISSING",
                "estimate": (estimate_decision or "AWAITING_CLIENT")
                            if estimate_version is not None else "MISSING",
                "execution": ("FINISHED" if finished else
                              "IN_PROGRESS" if started else "NOT_STARTED"),
                "quality": qc_result or "NOT_CHECKED",
                "technicalDocuments": document,
            },
        }
