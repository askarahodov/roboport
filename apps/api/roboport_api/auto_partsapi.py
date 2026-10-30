"""Offline PartsAPI getCrosses mapping and conservative request quota (#111).

No HTTP requests or credentials. The real authenticated transport is out of scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from .auto_parts_provider import Applicability, CatalogError, CatalogErrorCode, CatalogPart, _article


@dataclass(frozen=True)
class PartsApiCross:
    original_brand: str
    original_number: str
    replacement: CatalogPart


def parse_get_crosses(raw: object, *, revision: str, retrieved_at: datetime) -> tuple[PartsApiCross, ...]:
    """Map documented brand/partNumber/crossBrand/crossNumber fields; never verify fitment."""
    if type(revision) is not str or not revision.strip() or len(revision) > 160:
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    if type(raw) is not list or len(raw) > 1000:
        raise CatalogError(CatalogErrorCode.UNAVAILABLE)
    result = []
    for item in raw:
        if type(item) is not dict:
            raise CatalogError(CatalogErrorCode.UNAVAILABLE)
        values = [item.get(k) for k in ("brand", "partNumber", "crossBrand", "crossNumber")]
        if any(type(v) is not str or not v.strip() or len(v) > 160 for v in values):
            raise CatalogError(CatalogErrorCode.UNAVAILABLE)
        original_brand, original_number, brand, number = (v.strip() for v in values)
        result.append(PartsApiCross(
            original_brand, original_number,
            CatalogPart("PartsAPI", revision.strip(), retrieved_at, brand,
                        number, Applicability.UNVERIFIED),
        ))
    return tuple(result)


class DailyRequestBudget:
    """Fail-closed in-process cap. Never use as a distributed daily quota guarantee."""

    def __init__(self, max_requests: int = 50):
        if type(max_requests) is not int or not 1 <= max_requests <= 50:
            raise ValueError("request limit must be 1..50")
        self._limit = max_requests
        self._used = 0
        self._lock = Lock()

    def acquire(self) -> None:
        with self._lock:
            if self._used >= self._limit:
                raise CatalogError(CatalogErrorCode.RATE_LIMITED)
            self._used += 1

    @property
    def remaining(self) -> int:
        with self._lock:
            return self._limit - self._used
