"""Fail-closed boundary for future licensed parts-catalog adapters (#233).

No HTTP client or provider credentials are used by this module.
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping, Any

from roboport_api.auto_parts_provider import (
    Applicability, CatalogError, CatalogErrorCode, CatalogPart,
)


def provider_error(status: int | None = None, *, timed_out: bool = False) -> CatalogError:
    """Never propagate remote response bodies, URLs or credentials."""
    if timed_out:
        return CatalogError(CatalogErrorCode.UNAVAILABLE)
    if type(status) is not int:
        return CatalogError(CatalogErrorCode.UNAVAILABLE)
    if status == 429:
        return CatalogError(CatalogErrorCode.RATE_LIMITED)
    if status in (401, 403):
        return CatalogError(CatalogErrorCode.LICENSE_REQUIRED)
    if status == 404:
        return CatalogError(CatalogErrorCode.NOT_FOUND)
    return CatalogError(CatalogErrorCode.UNAVAILABLE)


def parse_provider_part(raw: Mapping[str, Any], *, is_cross: bool = False) -> CatalogPart:
    """Validate an untrusted provider record without inventing metadata.

    Cross references are not independent applicability evidence.
    """
    if not isinstance(raw, dict):
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    required = ("provider", "source_revision", "retrieved_at", "brand", "article", "applicability")
    if any(k not in raw for k in required):
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    values = (raw["provider"], raw["source_revision"], raw["brand"], raw["article"])
    if any(type(v) is not str or not v.strip() or len(v) > 160 for v in values):
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    if type(raw["retrieved_at"]) is not str:
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    try:
        stamp = datetime.fromisoformat(raw["retrieved_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("timezone absent")
        status = Applicability(raw["applicability"])
        if type(raw["applicability"]) is not str:
            raise ValueError("status type")
        if is_cross and status == Applicability.VERIFIED:
            status = Applicability.UNVERIFIED
        return CatalogPart(
            provider=raw["provider"].strip(),
            source_revision=raw["source_revision"].strip(),
            retrieved_at=stamp,
            brand=raw["brand"].strip(),
            article=raw["article"].strip(),
            applicability=status,
        )
    except (ValueError, TypeError, OverflowError) as exc:
        raise CatalogError(CatalogErrorCode.UNAVAILABLE) from None


def parse_provider_parts(raw: object, *, is_cross: bool = False) -> tuple[CatalogPart, ...]:
    """Atomic validation: a malformed item rejects the entire provider response."""
    if not isinstance(raw, list) or len(raw) > 1000:
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    return tuple(parse_provider_part(item, is_cross=is_cross) for item in raw)
