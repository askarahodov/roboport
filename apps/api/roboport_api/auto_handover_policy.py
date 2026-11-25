"""Restrictive handover policy shared by read, settlement and lifecycle writes."""
from .auto_document_access import require_document_access


def require_handover_access(conn,token,org,loc,order,*,cap="READ"):
    """Caller separately establishes the exact operational role and object ACL."""
    for capability in dict.fromkeys(("READ","PRICE_SALES",cap)):
        require_document_access(conn,token,org,loc,("MANAGER","WAREHOUSE"),
                                "HANDOVER",capability,order=order)


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
            require_document_access(conn,token,org,loc,roles,document,
                                    capability,order=order)
