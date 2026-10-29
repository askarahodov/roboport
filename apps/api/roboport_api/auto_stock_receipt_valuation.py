"""Accepted receipt purchase cost and VAT are audited separately from SKU quantity."""
from datetime import datetime
from uuid import UUID

import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_stock_service import AutoStockDenied,AutoStockConflict


def record_receipt_valuation(conn: psycopg.Connection,token: str,
    organization_id: UUID,location_id: UUID,document_id: UUID,
    request_id: UUID,currency: str,unit_net_minor: int,
    vat_total_minor: int,supplier_price_revision: str,
    supplier_quoted_at: datetime) -> UUID:
    if (
      type(token) is not str or not token or
      not all(isinstance(x,UUID) for x in
        (organization_id,location_id,document_id,request_id)) or
      type(currency) is not str or len(currency)!=3 or not currency.isascii()
      or not currency.isupper() or not currency.isalpha() or
      type(unit_net_minor) is not int or not 0<=unit_net_minor<=1_000_000_000 or
      type(vat_total_minor) is not int or not 0<=vat_total_minor<=1_000_000_000_000_000_000 or
      type(supplier_price_revision) is not str or
      not 3<=len(supplier_price_revision.strip())<=160 or
      not isinstance(supplier_quoted_at,datetime) or
      supplier_quoted_at.tzinfo is None or supplier_quoted_at.utcoffset() is None
    ):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,organization_id,"MANAGER",
                                        location_id,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        row=conn.execute("""SELECT d.id FROM auto_stock_receipt_document d
          JOIN auto_stock_receipt_acceptance a ON a.document_id=d.id
          WHERE d.id=%s AND d.organization_id=%s AND d.location_id=%s
          FOR SHARE OF d,a""",
          (document_id,organization_id,location_id)).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.receipt_value_actor',%s,true)",
                             (str(actor),))
                return conn.execute("""SELECT record_auto_stock_receipt_valuation(
                    %s,%s,%s,%s,%s,%s,%s)""",
                    (document_id,request_id,currency,unit_net_minor,
                     vat_total_minor,supplier_price_revision,supplier_quoted_at)).fetchone()[0]
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config('roboport.receipt_value_actor','',true)")
