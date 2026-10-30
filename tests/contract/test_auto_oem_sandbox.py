"""Offline OEM sandbox contract tests: no network or vendor credentials."""
import pytest
from roboport_api.auto_oem_sandbox import OemSandboxAdapter
from roboport_api.auto_parts_provider import Applicability, CatalogError, CatalogErrorCode


def record(**fields):
    base = dict(provider="synthetic", source_revision="fixture-v1",
                retrieved_at="2026-10-30T10:00:00Z", brand="TEST",
                article="SYN-001", applicability="VERIFIED")
    return base | fields


def test_oem_and_cross_have_different_fitment_trust():
    calls = []
    def transport(kind, article):
        calls.append((kind, article))
        return 200, [record()]
    adapter = OemSandboxAdapter(transport)
    assert adapter.search(" syn-001 ")[0].applicability == Applicability.VERIFIED
    assert adapter.search("syn-001", alternatives=True)[0].applicability == Applicability.UNVERIFIED
    assert calls == [("oem", "SYN-001"), ("alternatives", "SYN-001")]


@pytest.mark.parametrize("bad", ["", "  ", None, 123, "x" * 81])
def test_invalid_article_does_not_call_provider(bad):
    def forbidden(*_):
        raise AssertionError("invalid input reached provider")
    with pytest.raises(CatalogError) as error:
        OemSandboxAdapter(forbidden).search(bad)
    assert error.value.code == CatalogErrorCode.INVALID_INPUT


@pytest.mark.parametrize("status,expected", [
    (429, CatalogErrorCode.RATE_LIMITED),
    (401, CatalogErrorCode.LICENSE_REQUIRED),
    (403, CatalogErrorCode.LICENSE_REQUIRED),
    (404, CatalogErrorCode.NOT_FOUND),
    (500, CatalogErrorCode.UNAVAILABLE),
    (True, CatalogErrorCode.UNAVAILABLE),
])
def test_http_failure_does_not_leak_provider_response(status, expected):
    adapter = OemSandboxAdapter(lambda *_: (status, {"secret": "bearer-credential"}))
    with pytest.raises(CatalogError) as error:
        adapter.search("SYN-001")
    assert error.value.code == expected
    assert "bearer" not in str(error.value)


def test_malformed_batch_never_returns_partial_results():
    adapter = OemSandboxAdapter(lambda *_: (200, [record(), record(article=None)]))
    with pytest.raises(CatalogError) as error:
        adapter.search("SYN-001")
    assert error.value.code == CatalogErrorCode.UNAVAILABLE


@pytest.mark.parametrize("failure", [TimeoutError(), OSError(), ValueError()])
def test_transport_failures_map_to_unavailable(failure):
    def failing(*_):
        raise failure
    with pytest.raises(CatalogError) as error:
        OemSandboxAdapter(failing).search("SYN-001")
    assert error.value.code == CatalogErrorCode.UNAVAILABLE


def test_empty_results_are_not_invented():
    assert OemSandboxAdapter(lambda *_: (200, [])).search("SYN-001") == ()
