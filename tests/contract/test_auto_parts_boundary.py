"""Синтетические contract tests для границы внешнего каталога #233."""
import pytest
from roboport_api.auto_parts_provider import Applicability, CatalogError, CatalogErrorCode
from roboport_api.auto_parts_boundary import parse_provider_part, parse_provider_parts, provider_error


def sample():
    return {
        "provider": "TEST-LICENSED", "source_revision": "rev-42",
        "retrieved_at": "2026-10-28T12:00:00Z", "brand": "TEST",
        "article": "SYN-101", "applicability": "UNKNOWN",
    }


def assert_unavailable(value):
    with pytest.raises(CatalogError) as err:
        parse_provider_part(value)
    assert err.value.code == CatalogErrorCode.UNAVAILABLE


def test_valid_provenance_and_iso_timezone():
    value = parse_provider_part(sample())
    assert value.retrieved_at.utcoffset().total_seconds() == 0
    assert value.provider == "TEST-LICENSED"
    assert value.source_revision == "rev-42"
    assert value.applicability == Applicability.UNKNOWN


@pytest.mark.parametrize("field", [
    "provider", "source_revision", "retrieved_at", "brand", "article", "applicability",
])
def test_required_fields_never_default(field):
    record = sample()
    del record[field]
    assert_unavailable(record)


@pytest.mark.parametrize("field,value", [
    ("provider", ""), ("source_revision", None), ("brand", 42),
    ("article", " "), ("applicability", "VERIFIED_BY_CROSS"),
    ("applicability", None), ("retrieved_at", "2026-10-28T12:00:00"),
    ("retrieved_at", "not-an-iso-date"), ("retrieved_at", 1700000000),
])
def test_invalid_fields_are_fail_closed(field, value):
    assert_unavailable(sample() | {field: value})


@pytest.mark.parametrize("input_value", [None, "{}", [], 42])
def test_invalid_item_not_accepted(input_value):
    assert_unavailable(input_value)


def test_cross_never_proves_applicability():
    valid = sample() | {"applicability": "VERIFIED"}
    assert parse_provider_part(valid).applicability == Applicability.VERIFIED
    assert parse_provider_part(valid, is_cross=True).applicability == Applicability.UNVERIFIED
    assert parse_provider_parts([valid], is_cross=True)[0].applicability == Applicability.UNVERIFIED


def test_batch_is_atomic_and_bounded():
    assert parse_provider_parts([]) == ()
    assert len(parse_provider_parts([sample(), sample()])) == 2
    for response in (None, {}, [sample(), {"article": "bad"}], [sample()] * 1001):
        with pytest.raises(CatalogError) as err:
            parse_provider_parts(response)
        assert err.value.code == CatalogErrorCode.UNAVAILABLE


@pytest.mark.parametrize("status,expected", [
    (429, CatalogErrorCode.RATE_LIMITED),
    (401, CatalogErrorCode.LICENSE_REQUIRED),
    (403, CatalogErrorCode.LICENSE_REQUIRED),
    (404, CatalogErrorCode.NOT_FOUND),
    (500, CatalogErrorCode.UNAVAILABLE),
    (503, CatalogErrorCode.UNAVAILABLE),
    (200, CatalogErrorCode.UNAVAILABLE),
    (None, CatalogErrorCode.UNAVAILABLE),
    ("429", CatalogErrorCode.UNAVAILABLE),
])
def test_http_errors_are_domain_only(status, expected):
    error = provider_error(status)
    assert error.code == expected
    assert "token" not in str(error).lower()
    assert "https" not in str(error).lower()


def test_timeout_fails_closed_even_with_status():
    assert provider_error(200, timed_out=True).code == CatalogErrorCode.UNAVAILABLE
