"""Изолированный offline-контракт автомобильного каталога запчастей (#231).

Не является каталогом склада, доказательством применяемости или доступом к OEM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Applicability(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class CatalogErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    LICENSE_REQUIRED = "LICENSE_REQUIRED"


class CatalogError(Exception):
    def __init__(self, code: CatalogErrorCode):
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class CatalogPart:
    provider: str
    source_revision: str
    retrieved_at: datetime
    brand: str
    article: str
    applicability: Applicability

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() or len(value) > 160\n               for value in (self.provider, self.source_revision, self.brand, self.article)):
            raise ValueError("Каталожная запись неполна")
        if not isinstance(self.applicability, Applicability):
            raise ValueError("Неизвестный статус применяемости")
        if not isinstance(self.retrieved_at, datetime):
            raise ValueError("retrieved_at должен быть datetime")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at должен содержать timezone")


@runtime_checkable
class PartsCatalogProvider(Protocol):
    def find_vehicle(self, vin: str) -> str: ...
    def search_by_oem(self, article: str) -> tuple[CatalogPart, ...]: ...
    def list_alternatives(self, article: str) -> tuple[CatalogPart, ...]: ...
    def get_applicability(self, article: str, vin: str) -> Applicability: ...
    def get_assemblies(self, vin: str) -> tuple[str, ...]: ...


def _vin(value: str) -> str:
    if not isinstance(value, str):
        raise CatalogError(CatalogErrorCode.INVALID_INPUT)
    result = value.strip().upper()
    if len(result) != 17 or any(c not in "ABCDEFGHJKLMNPRSTUVWXYZ0123456789" for c in result):
        raise CatalogError(CatalogErrorCode.INVALID_INPUT)
    return result


def _article(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise CatalogError(CatalogErrorCode.INVALID_INPUT)
    return value.strip().upper()


class FixturePartsCatalog:
    """Synthetic only. Unknown cross/reference never becomes verified."""

    def __init__(self, *, failure: CatalogErrorCode | None = None):
        self.failure = failure
        self._retrieved_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._vehicle = "1HGCM82633A004352"
        self._part = CatalogPart(
            provider="synthetic-fixture",
            source_revision="fixture-v1",
            retrieved_at=self._retrieved_at,
            brand="TEST",
            article="SYN-001",
            applicability=Applicability.UNKNOWN,
        )

    def _check(self) -> None:
        if self.failure is not None:
            raise CatalogError(self.failure)

    def find_vehicle(self, vin: str) -> str:
        vin = _vin(vin)
        self._check()
        if vin != self._vehicle:
            raise CatalogError(CatalogErrorCode.NOT_FOUND)
        return "SYNTHETIC-VEHICLE"

    def search_by_oem(self, article: str) -> tuple[CatalogPart, ...]:
        article = _article(article)
        self._check()
        return (self._part,) if article == "SYN-001" else ()

    def list_alternatives(self, article: str) -> tuple[CatalogPart, ...]:
        _article(article)
        self._check()
        return ()

    def get_applicability(self, article: str, vin: str) -> Applicability:
        _article(article)
        _vin(vin)
        self._check()
        return Applicability.UNKNOWN

    def get_assemblies(self, vin: str) -> tuple[str, ...]:
        _vin(vin)
        self._check()
        return ()
