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
