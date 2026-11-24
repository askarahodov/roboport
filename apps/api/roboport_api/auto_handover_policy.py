"""Restrictive handover policy shared by read, settlement and lifecycle writes."""
from .auto_document_access import require_document_access


def require_handover_access(conn,token,org,loc,order,*,cap="READ"):
    """Caller separately establishes the exact operational role and object ACL."""
    for capability in dict.fromkeys(("READ","PRICE_SALES",cap)):
        require_document_access(conn,token,org,loc,("MANAGER","WAREHOUSE"),
                                "HANDOVER",capability,order=order)
