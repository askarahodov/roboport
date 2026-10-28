"""Контрактные проверки offline автомобильного провайдера #231."""
from datetime import datetime, timezone
import pytest

from roboport_api.auto_parts_provider import (
    Applicability, CatalogError, CatalogErrorCode, CatalogPart,
    FixturePartsCatalog, PartsCatalogProvider,
)


def test_implements_vendor_independent_protocol_and_provenance():
    provider = FixturePartsCatalog()
    assert isinstance(provider, PartsCatalogProvider)
    result = provider.search_by_oem(" syn-001 ")
    assert len(result) == 1
    part = result[0]
    assert part.provider == "synthetic-fixture"
    assert part.source_revision == "fixture-v1"
    assert part.retrieved_at.tzinfo is not None
    assert part.applicability == Applicability.UNKNOWN
    assert provider.list_alternatives("SYN-001") == ()
    assert provider.get_applicability("SYN-001", "1HGCM82633A004352") == Applicability.UNKNOWN
    assert provider.get_assemblies("1HGCM82633A004352") == ()
    assert provider.find_vehicle("1hgcm82633a004352") == "SYNTHETIC-VEHICLE"


@pytest.mark.parametrize("invalid", ["", "SHORT", "1HGCM82633A00435I", "1HGCM82633A00435O", None])
def test_invalid_vin_rejected(invalid):
    with pytest.raises(CatalogError) as error:
        FixturePartsCatalog().find_vehicle(invalid)
    assert error.value.code == CatalogErrorCode.INVALID_INPUT


def test_unknown_valid_vin_is_not_found_and_unknown_parts_are_not_verified():
    provider = FixturePartsCatalog()
    with pytest.raises(CatalogError) as error:
        provider.find_vehicle("1HGCM82633A004353")
    assert error.value.code == CatalogErrorCode.NOT_FOUND
    assert provider.search_by_oem("MISSING") == ()
    assert provider.get_applicability("MISSING", "1HGCM82633A004353") == Applicability.UNKNOWN


@pytest.mark.parametrize("value", ["", "  ", None, "x" * 81])
def test_invalid_article_rejected(value):
    with pytest.raises(CatalogError) as error:
        FixturePartsCatalog().search_by_oem(value)
    assert error.value.code == CatalogErrorCode.INVALID_INPUT


@pytest.mark.parametrize("code", [
    CatalogErrorCode.RATE_LIMITED, CatalogErrorCode.UNAVAILABLE,
    CatalogErrorCode.LICENSE_REQUIRED,
])
def test_failures_never_return_fabricated_data(code):
    provider = FixturePartsCatalog(failure=code)
    for operation in (
        lambda: provider.find_vehicle("1HGCM82633A004352"),
        lambda: provider.search_by_oem("SYN-001"),
        lambda: provider.list_alternatives("SYN-001"),
        lambda: provider.get_applicability("SYN-001", "1HGCM82633A004352"),
        lambda: provider.get_assemblies("1HGCM82633A004352"),
    ):
        with pytest.raises(CatalogError) as error:
            operation()
        assert error.value.code == code


def test_malformed_provider_record_fails_closed():
    args = dict(provider="fixture", source_revision="v1",
                retrieved_at=datetime.now(timezone.utc), brand="TEST",
                article="SYN-001", applicability=Applicability.UNKNOWN)
    for field in ("provider", "source_revision", "brand", "article"):
        with pytest.raises(ValueError):
            CatalogPart(**(args | {field: ""}))
    with pytest.raises(ValueError):
        CatalogPart(**(args | {"retrieved_at": datetime(2026, 1, 1)}))
    with pytest.raises(ValueError):
        CatalogPart(**(args | {"applicability": "guess"}))
