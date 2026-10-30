"""Offline contract tests: NHTSA calls never reach the internet in CI."""
import json
from urllib.error import HTTPError, URLError
import pytest

from roboport_api.auto_nhtsa_vin import NhtsaVinDecoder, VPIC_ORIGIN, MAX_RESPONSE_BYTES
from roboport_api.auto_parts_provider import CatalogError, CatalogErrorCode

VIN = "1HGCM82633A004352"


def payload(**updates):
    row = {"ErrorCode": "0", "Make": "HONDA", "Model": "Accord", "ModelYear": "2003"}
    row.update(updates)
    return json.dumps({"Results": [row]}).encode()


def test_success_normalizes_vin_and_does_not_claim_fitment():
    calls = []
    def transport(url, timeout):
        calls.append((url, timeout))
        return payload()
    result = NhtsaVinDecoder(transport=transport).decode(VIN.lower())
    assert (result.vin, result.make, result.model, result.model_year) == (
        VIN, "HONDA", "Accord", "2003")
    assert result.source == "NHTSA-vPIC"
    assert calls == [(f"{VPIC_ORIGIN}/api/vehicles/DecodeVinValues/{VIN}?format=json", 5.0)]
    assert not hasattr(result, "applicability")


@pytest.mark.parametrize("vin", ["", "SHORT", "1HGCM82633A00435I", None, 7])
def test_invalid_vin_is_rejected_without_network(vin):
    def forbidden(*args):
        raise AssertionError("unexpected network request")
    with pytest.raises(CatalogError) as error:
        NhtsaVinDecoder(transport=forbidden).decode(vin)
    assert error.value.code == CatalogErrorCode.INVALID_INPUT


@pytest.mark.parametrize("response", [
    b"not-json", b"{}", b'{"Results":[]}', b'{"Results":[null]}',
    payload(ErrorCode="1"), payload(ErrorCode="0,1"), payload(Make=""),
    payload(Model=None), payload(ModelYear=""), b"[]" ,
])
def test_malformed_or_partial_decode_fails_closed(response):
    with pytest.raises(CatalogError) as error:
        NhtsaVinDecoder(transport=lambda *_: response).decode(VIN)
    assert error.value.code in (CatalogErrorCode.UNAVAILABLE, CatalogErrorCode.NOT_FOUND)


def test_error_response_and_timeout_fail_closed():
    scenarios = [
        (HTTPError("https://example.invalid", 429, "rate limited", {}, None),
         CatalogErrorCode.RATE_LIMITED),
        (HTTPError("https://example.invalid", 500, "bad", {}, None),
         CatalogErrorCode.UNAVAILABLE),
        (URLError("connection failed"), CatalogErrorCode.UNAVAILABLE),
        (TimeoutError(), CatalogErrorCode.UNAVAILABLE),
        (ValueError("oversize"), CatalogErrorCode.UNAVAILABLE),
    ]
    for exc, expected in scenarios:
        def failing(*_):
            raise exc
        with pytest.raises(CatalogError) as error:
            NhtsaVinDecoder(transport=failing).decode(VIN)
        assert error.value.code == expected
        assert "example.invalid" not in str(error.value)


def test_response_size_is_bounded_and_timeout_config_validated():
    with pytest.raises(CatalogError) as error:
        NhtsaVinDecoder(transport=lambda *_: b" " * (MAX_RESPONSE_BYTES + 1)).decode(VIN)
    assert error.value.code == CatalogErrorCode.UNAVAILABLE
    for timeout in (-1, 0, 11):
        with pytest.raises(ValueError):
            NhtsaVinDecoder(timeout=timeout)
