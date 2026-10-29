"""Two-person, branch-scoped correction of stock classified under wrong SKU.

All mutation remains synthetic/loopback-only until release/legal gates pass.
"""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_stock_service import AutoStockDenied, AutoStockConflict


def _args(token, *ids):
    if type(token) is not str or not token or not all(isinstance(x, UUID) for x in ids):
        raise AutoStockDenied("INVALID_INPUT")


def _run(conn, token, organization_id, location_id, role, lot_ids, sql, args):
    with conn.transaction():
        if not organization_role_allows(conn, token, organization_id, role,
                 location_id, for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        scoped = conn.execute(
            """SELECT id FROM auto_stock_lot
               WHERE id=ANY(%s::uuid[]) AND organization_id=%s AND location_id=%s
               ORDER BY id FOR SHARE""",
            (list(lot_ids), organization_id, location_id)).fetchall()
        if len(scoped) != len(lot_ids):
            raise AutoStockDenied("FORBIDDEN")
        actor = session_subject(conn, token, for_mutation=True)
        if actor is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                conn.execute(
                    "SELECT set_config('roboport.reclassification_actor',%s,true)",
                    (str(actor),))
                return conn.execute(sql, args).fetchone()[0]
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation):
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config('roboport.reclassification_actor','',true)")


def propose_reclassification(conn: psycopg.Connection, token: str,
        organization_id: UUID, location_id: UUID, source_lot_id: UUID,
        destination_lot_id: UUID, request_id: UUID, quantity: int, reason: str) -> UUID:
    _args(token, organization_id, location_id, source_lot_id, destination_lot_id, request_id)
    if (source_lot_id == destination_lot_id
       or type(quantity) is not int or not 1 <= quantity <= 1_000_000_000
       or type(reason) is not str or not 3 <= len(reason.strip()) <= 240
       or reason != reason.strip()):
        raise AutoStockDenied("INVALID_INPUT")
    return _run(conn, token, organization_id, location_id, "WAREHOUSE",
        (source_lot_id, destination_lot_id),
        "SELECT record_auto_stock_reclassification_document(%s,%s,%s,%s,%s)",
        (source_lot_id, destination_lot_id, request_id, quantity, reason))


def approve_reclassification(conn: psycopg.Connection, token: str,
        organization_id: UUID, location_id: UUID,
        document_id: UUID, request_id: UUID) -> UUID:
    _args(token, organization_id, location_id, document_id, request_id)
    with conn.transaction():
        if not organization_role_allows(conn, token, organization_id, "MANAGER",
                 location_id, for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        doc = conn.execute(
            """SELECT source_lot_id,destination_lot_id
               FROM auto_stock_reclassification_document
               WHERE id=%s AND organization_id=%s AND location_id=%s FOR SHARE""",
            (document_id, organization_id, location_id)).fetchone()
        if doc is None:
            raise AutoStockDenied("FORBIDDEN")
        return _run(conn, token, organization_id, location_id, "MANAGER",
            doc, "SELECT accept_auto_stock_reclassification_document(%s,%s)",
            (document_id, request_id))
