"""No PartsAPI network calls; persistent quota tests use temporary SQLite."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pytest

from roboport_api.auto_partsapi_quota import PersistentDailyBudget
from roboport_api.auto_parts_provider import CatalogError, CatalogErrorCode

DAY = datetime(2026, 10, 30, tzinfo=timezone.utc)


def test_reservations_survive_new_instances(tmp_path):
    path = tmp_path / "quota.db"
    assert PersistentDailyBudget(path, cap=3).reserve(observed_used=0, now=DAY) == 2
    assert PersistentDailyBudget(path, cap=3).reserve(observed_used=0, now=DAY) == 1
    assert PersistentDailyBudget(path, cap=3).reserve(observed_used=0, now=DAY) == 0
    with pytest.raises(CatalogError) as err:
        PersistentDailyBudget(path, cap=3).reserve(observed_used=0, now=DAY)
    assert err.value.code == CatalogErrorCode.RATE_LIMITED


def test_observed_portal_usage_is_not_decreased(tmp_path):
    budget = PersistentDailyBudget(tmp_path / "quota.db", cap=5)
    assert budget.reserve(observed_used=3, now=DAY) == 1
    assert budget.reserve(observed_used=0, now=DAY) == 0
    with pytest.raises(CatalogError, match="RATE_LIMITED"):
        budget.reserve(observed_used=0, now=DAY)


def test_utc_day_changes_not_local_day(tmp_path):
    budget = PersistentDailyBudget(tmp_path / "quota.db", cap=1)
    budget.reserve(observed_used=0, now=DAY)
    next_day = datetime(2026, 10, 31, tzinfo=timezone.utc)
    assert budget.reserve(observed_used=0, now=next_day) == 0


def test_concurrent_requests_are_serialized(tmp_path):
    path = tmp_path / "quota.db"
    def attempt(_):
        try:
            PersistentDailyBudget(path, cap=5).reserve(observed_used=0, now=DAY)
            return True
        except CatalogError as err:
            assert err.code == CatalogErrorCode.RATE_LIMITED
            return False
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(25)))
    assert sum(outcomes) == 5


def test_unavailable_storage_fails_closed(tmp_path):
    budget = PersistentDailyBudget(tmp_path / "missing" / "quota.db")
    with pytest.raises(CatalogError, match="UNAVAILABLE"):
        budget.reserve(observed_used=0, now=DAY)


@pytest.mark.parametrize("cap", [0, 51, True, "50"])
def test_invalid_cap(tmp_path, cap):
    with pytest.raises(ValueError):
        PersistentDailyBudget(tmp_path / "quota.db", cap=cap)


@pytest.mark.parametrize("observed", [-1, 51, True, None])
def test_invalid_observation(tmp_path, observed):
    with pytest.raises(ValueError):
        PersistentDailyBudget(tmp_path / "quota.db").reserve(observed_used=observed, now=DAY)


def test_no_in_memory_storage_and_no_naive_time(tmp_path):
    with pytest.raises(ValueError):
        PersistentDailyBudget(":memory:")
    with pytest.raises(ValueError):
        PersistentDailyBudget(tmp_path / "quota.db").reserve(
            observed_used=0, now=datetime(2026, 10, 30)
        )
