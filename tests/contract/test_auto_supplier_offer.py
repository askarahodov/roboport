"""Offline supplier quote contract tests; no real supplier or payment integration."""
from copy import deepcopy
import pytest

from roboport_api.auto_parts_provider import CatalogError, CatalogErrorCode
from roboport_api.auto_supplier_offer import parse_supplier_offer, parse_supplier_offers


def offer(**changes):
    data = {
        "offer_id": "synthetic-01",
        "supplier_id": "fixture-supplier",
        "source_revision": "fixture-v1",
        "quoted_at": "2026-10-28T10:00:00Z",
        "expires_at": "2026-10-29T10:00:00+00:00",
        "currency": "RUB",
        "unit_purchase_minor": 12500,
        "available_quantity": 3,
        "article": "SYN-001",
    }
    return data | changes


def rejects(data):
    with pytest.raises(CatalogError) as exc:
        parse_supplier_offer(data)
    assert exc.value.code == CatalogErrorCode.UNAVAILABLE
    assert "token" not in str(exc.value).lower()


def test_valid_quote_has_provenance_but_no_applicability_or_selling_price():
    snapshot = parse_supplier_offer(offer())
    assert snapshot.unit_purchase_minor == 12500
    assert snapshot.currency == "RUB"
    assert snapshot.source_revision == "fixture-v1"
    assert snapshot.expires_at > snapshot.quoted_at
    assert not hasattr(snapshot, "applicability")
    assert not hasattr(snapshot, "selling_price_minor")
    assert parse_supplier_offer(offer()) == snapshot
    with pytest.raises(Exception):
        snapshot.unit_purchase_minor = 2


@pytest.mark.parametrize("field", [
    "offer_id", "supplier_id", "source_revision", "quoted_at", "expires_at",
    "currency", "unit_purchase_minor", "available_quantity", "article",
])
def test_missing_field_fail_closed(field):
    bad = offer()
    del bad[field]
    rejects(bad)


@pytest.mark.parametrize("field,value", [
    ("offer_id", ""), ("supplier_id", None), ("source_revision", " "),
    ("article", 3), ("currency", "rub"), ("currency", "US"), ("currency", "€UR"),
    ("currency", 3), ("unit_purchase_minor", 1.5), ("unit_purchase_minor", True),
    ("unit_purchase_minor", -1), ("unit_purchase_minor", 10**16),
    ("available_quantity", True), ("available_quantity", -1),
    ("available_quantity", 10**10), ("quoted_at", "2026-10-28T10:00:00"),
    ("quoted_at", 1234), ("expires_at", "yesterday"),
    ("expires_at", "2026-10-27T10:00:00Z"),
    ("expires_at", "2026-10-28T10:00:00Z"),
])
def test_bad_values_fail_closed(field, value):
    rejects(offer(**{field: value}))


def test_extra_fields_cannot_smuggle_applicability_or_selling_price():
    rejects(offer(applicability="VERIFIED"))
    rejects(offer(selling_price_minor=1))


def test_zero_cost_and_zero_availability_are_valid_not_in_stock():
    record = parse_supplier_offer(offer(unit_purchase_minor=0, available_quantity=0))
    assert record.available_quantity == 0
    assert record.unit_purchase_minor == 0


def test_atomic_batch_duplicate_identity_and_bound():
    assert parse_supplier_offers([]) == ()
    two = parse_supplier_offers([offer(), offer(offer_id="synthetic-02")])
    assert len(two) == 2
    assert two == parse_supplier_offers([offer(), offer(offer_id="synthetic-02")])
    for bad in (None, {}, [offer(), {"bad": 1}], [offer(), deepcopy(offer())], [offer()] * 1001):
        with pytest.raises(CatalogError) as exc:
            parse_supplier_offers(bad)
        assert exc.value.code == CatalogErrorCode.UNAVAILABLE
