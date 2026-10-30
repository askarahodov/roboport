"""Contract tests do not consume any PartsAPI requests."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from roboport_api.auto_partsapi import DailyRequestBudget, parse_get_crosses
from roboport_api.auto_parts_provider import Applicability, CatalogError, CatalogErrorCode

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def test_cross_response_retains_source_and_never_proves_fitment():
    items = [{"brand": "ORIGINAL", "partNumber": "OEM-1",
              "crossBrand": "AFTERMARKET", "crossNumber": "ALT-2"}]
    result = parse_get_crosses(items, revision="test-2026", retrieved_at=NOW)
    assert len(result) == 1
    assert result[0].original_brand == "ORIGINAL"
    assert result[0].original_number == "OEM-1"
    assert result[0].replacement.brand == "AFTERMARKET"
    assert result[0].replacement.article == "ALT-2"
    assert result[0].replacement.provider == "PartsAPI"
    assert result[0].replacement.source_revision == "test-2026"
    assert result[0].replacement.retrieved_at == NOW
    assert result[0].replacement.applicability == Applicability.UNVERIFIED


@pytest.mark.parametrize("invalid", [
    None, {}, "[]", [{"brand": "X"}],
    [{"brand": "X", "partNumber": "Y", "crossBrand": "Z", "crossNumber": None}],
    [{"brand": "X", "partNumber": "Y", "crossBrand": "Z", "crossNumber": " "}],
    [{"brand": "X", "partNumber": "Y", "crossBrand": "Z", "crossNumber": "A"}] * 1001,
])
def test_invalid_batch_is_atomic(invalid):
    with pytest.raises(CatalogError) as err:
        parse_get_crosses(invalid, revision="test-2026", retrieved_at=NOW)
    assert err.value.code == CatalogErrorCode.UNAVAILABLE


def test_no_invented_revision_or_timestamp():
    for kwargs in ({"revision": ""}, {"revision": None},
                   {"retrieved_at": datetime(2026, 10, 30)}):
        with pytest.raises(CatalogError) as err:
            parse_get_crosses([], **(dict(revision="v1", retrieved_at=NOW) | kwargs))
        assert err.value.code == CatalogErrorCode.UNAVAILABLE


def test_empty_result_and_quota_limit():
    assert parse_get_crosses([], revision="v1", retrieved_at=NOW) == ()
    budget = DailyRequestBudget(2)
    assert budget.remaining == 2
    budget.acquire()
    budget.acquire()
    assert budget.remaining == 0
    with pytest.raises(CatalogError) as err:
        budget.acquire()
    assert err.value.code == CatalogErrorCode.RATE_LIMITED


def test_concurrent_callers_cannot_exceed_budget():
    budget = DailyRequestBudget(3)
    def acquire(_):
        try:
            budget.acquire()
            return True
        except CatalogError as exc:
            assert exc.code == CatalogErrorCode.RATE_LIMITED
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        granted = list(pool.map(acquire, range(40)))
    assert sum(granted) == 3
    assert budget.remaining == 0


@pytest.mark.parametrize("limit", [0, -1, 51, True, 1.5, "50"])
def test_invalid_budget_settings_rejected(limit):
    with pytest.raises(ValueError):
        DailyRequestBudget(limit)
