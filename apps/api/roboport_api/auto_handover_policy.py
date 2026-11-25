"""Restrictive handover policy shared by read, settlement and lifecycle writes."""
from .auto_document_access import require_document_access, DocumentPolicyDenied


def require_handover_access(conn,token,org,loc,order,*,cap="READ"):
    """Caller separately establishes the exact operational role and object ACL."""
    for capability in dict.fromkeys(("READ","PRICE_SALES",cap)):
        cutoff=require_document_access(conn,token,org,loc,("MANAGER","WAREHOUSE"),
                                "HANDOVER",capability,order=order)
        if order is None and cutoff is not None:
            raise DocumentPolicyDenied("HISTORY_DENIED")


def require_handover_projection(conn,token,org,loc,order,*,cap="READ"):
    """A staff handover snapshot contains multiple protected document families.

    Caller already holds exact operational role and object scope.
    These restrictions never grant access to a full source document.
    """
    require_handover_access(conn,token,org,loc,order,cap=cap)
    roles=("MANAGER","WAREHOUSE","INTAKE","MECHANIC","CASHIER")
    for document,capabilities in (
        ("INTAKE_ACT",("READ",)),
        ("ESTIMATE",("READ","PRICE_SALES")),
        ("SUPPLIER_ORDER",("READ",)),
        ("FINANCE",("READ","PRICE_SALES")),
    ):
        for capability in capabilities:
            cutoff=require_document_access(conn,token,org,loc,roles,document,
                                    capability,order=order)
            # A branch aggregate is not an individual document. Its entire
            # history must be visible, or the total would be misleading.
            if order is None and cutoff is not None:
                raise DocumentPolicyDenied("HISTORY_DENIED")
