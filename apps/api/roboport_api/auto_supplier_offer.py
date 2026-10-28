"""Immutable offline supplier quote boundary, separate from parts applicability and stock."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from roboport_api.auto_parts_provider import CatalogError, CatalogErrorCode


@dataclass(frozen=True)
class SupplierOffer:
    offer_id: str
    supplier_id: str
    source_revision: str
    quoted_at: datetime
    expires_at: datetime
    currency: str
    unit_purchase_minor: int
    available_quantity: int
    article: str

    def __post_init__(self) -> None:
        # This object has no applicability, selling price, reservation or ordering authority.
        for field in ("offer_id", "supplier_id", "source_revision", "article"):
            value = getattr(self, field)
            if type(value) is not str or not value.strip() or len(value) > 160:
                raise ValueError("invalid supplier offer identifier")
        if type(self.currency) is not str or len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError("invalid currency")
        if type(self.unit_purchase_minor) is not int or self.unit_purchase_minor < 0 or self.unit_purchase_minor > 10**15:
            raise ValueError("invalid purchase price")
        if type(self.available_quantity) is not int or not 0 <= self.available_quantity <= 10**9:
            raise ValueError("invalid quantity")
        for stamp in (self.quoted_at, self.expires_at):
            if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError("timestamp requires timezone")
        if self.expires_at <= self.quoted_at:
            raise ValueError("offer expiry must follow quote")


def _timestamp(raw: object) -> datetime:
    if type(raw) is not str:
        raise ValueError("invalid timestamp")
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def parse_supplier_offer(raw: object) -> SupplierOffer:
    """Unknown/invalid provider input rejects the entire offer without external details."""
    fields = (
        "offer_id", "supplier_id", "source_revision", "quoted_at", "expires_at",
        "currency", "unit_purchase_minor", "available_quantity", "article",
    )
    if type(raw) is not dict or set(raw) != set(fields):
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    try:
        return SupplierOffer(
            offer_id=raw["offer_id"], supplier_id=raw["supplier_id"],
            source_revision=raw["source_revision"],
            quoted_at=_timestamp(raw["quoted_at"]),
            expires_at=_timestamp(raw["expires_at"]),
            currency=raw["currency"], unit_purchase_minor=raw["unit_purchase_minor"],
            available_quantity=raw["available_quantity"], article=raw["article"],
        )
    except (ValueError, TypeError, OverflowError):
        raise CatalogError(CatalogErrorCode.UNAVAILABLE) from None


def parse_supplier_offers(raw: object) -> tuple[SupplierOffer, ...]:
    """Bounded all-or-nothing snapshot: duplicate external IDs are an error."""
    if type(raw) is not list or len(raw) > 1000:
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    values = tuple(parse_supplier_offer(item) for item in raw)
    ids = {(offer.supplier_id, offer.offer_id) for offer in values}
    if len(ids) != len(values):
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    return values
