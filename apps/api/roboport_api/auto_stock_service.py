"""Authorized internal automotive stock mutations (synthetic PG17 only).

No public HTTP routes. Authority and ledger write share one transaction.
"""
from uuid import UUID
import psycopg

from .auto_org_authority import organization_role_allows


class AutoStockDenied(ValueError):
    """Invalid input, revoked or insufficiently scoped authority."""


class AutoStockConflict(ValueError):
    """Insufficient reserved/available stock or a conflicting idempotency key."""


def mutate_auto_stock(
    conn: psycopg.Connection,
    token: str,
    organization_id: UUID,
    location_id: UUID,
    sku_id: UUID,
    request_id: UUID,
    action: str,
    quantity: int,
) -> UUID:
    """Validate WAREHOUSE authority under live locks before ledger event.

    The same transaction holds session/grant locks until the stock function
    completes, preventing revocation from racing past a committed mutation.
    """
    if (
        type(token) is not str or not token
        or not all(isinstance(v, UUID) for v in
                   (organization_id, location_id, sku_id, request_id))
        or type(action) is not str or action not in ("RECEIPT", "RESERVE", "RELEASE")
        or type(quantity) is not int or not 1 <= quantity <= 1_000_000_000
    ):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(
            conn, token, organization_id, "WAREHOUSE", location_id,
            for_mutation=True,
        ):
            raise AutoStockDenied("FORBIDDEN")
        # Object ownership is checked after live WAREHOUSE grants; never use
        # caller-provided organization/location as a substitute for SKU scope.
        row = conn.execute(
            """SELECT id FROM auto_stock_sku
               WHERE id=%s AND organization_id=%s AND location_id=%s FOR SHARE""",
            (sku_id, organization_id, location_id),
        ).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            # Savepoint converts expected database denials to domain conflict
            # while preserving the outer transaction and all authorization locks.
            with conn.transaction():
                event = conn.execute(
                    "SELECT record_auto_stock(%s,%s,%s,%s)",
                    (sku_id, request_id, action, quantity),
                ).fetchone()
            if event is None:
                raise AutoStockConflict("CONFLICT")
            return event[0]
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise AutoStockConflict("CONFLICT") from None
