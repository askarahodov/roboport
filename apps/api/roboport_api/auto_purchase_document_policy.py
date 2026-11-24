"""Restrictive synthetic purchase-document authorization, not a new role grant."""
from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_stock_service import AutoStockDenied


def require_purchase_document(conn, token, org, loc, *capabilities, created_at=None):
    """Recheck all active purchase roles; an existing document obeys READ history.

    Caller holds a transaction and separately checks the exact operational
    role and tenant-scoped object. None is reserved for new documents and
    queue callers; a queue with a cutoff must explicitly deny or filter.
    """
    try:
        cutoff = require_document_access(
            conn, token, org, loc, ("MANAGER", "WAREHOUSE"),
            "SUPPLIER_ORDER", "READ",
        )
        if created_at is not None and cutoff is not None and created_at < cutoff:
            raise AutoStockDenied("FORBIDDEN")
        for capability in capabilities:
            require_document_access(
                conn, token, org, loc, ("MANAGER", "WAREHOUSE"),
                "SUPPLIER_ORDER", capability,
            )
        return cutoff
    except DocumentPolicyDenied:
        raise AutoStockDenied("FORBIDDEN") from None


def receipt_history_date(conn, org, loc, receipt):
    """A linked supplier order may be older than its receipt; honor both."""
    row = conn.execute("""
        SELECT least(d.created_at, po.created_at)
        FROM auto_stock_receipt_document d
        LEFT JOIN auto_supplier_purchase_receipt pr ON pr.document_id=d.id
        LEFT JOIN auto_supplier_purchase_order po ON po.id=pr.purchase_id
          AND po.organization_id=d.organization_id AND po.location_id=d.location_id
        WHERE d.id=%s AND d.organization_id=%s AND d.location_id=%s
    """, (receipt, org, loc)).fetchone()
    if row is None:
        raise AutoStockDenied("FORBIDDEN")
    return row[0]
