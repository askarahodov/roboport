"""NHTSA vPIC VIN decoder, isolated from OEM applicability and customer identity.

No production HTTP route. Calls are opt-in; tests inject a synthetic transport.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .auto_parts_provider import CatalogError, CatalogErrorCode, _vin

VPIC_ORIGIN = "https://vpic.nhtsa.dot.gov"
MAX_RESPONSE_BYTES = 128_000


@dataclass(frozen=True)
class VinDescription:
    vin: str
    make: str
    model: str
    model_year: str
    source: str = "NHTSA-vPIC"

    # This is NOT proof of ownership, fitment, parts compatibility or a VIN match.


def _http_get(url: str, timeout: float) -> bytes:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return content


class NhtsaVinDecoder:
    """VIN-only adapter. Does not implement the OEM parts provider protocol."""

    def __init__(self, *, transport: Callable[[str, float], bytes] = _http_get,
                 timeout: float = 5.0):
        if not 0 < timeout <= 10:
            raise ValueError("invalid timeout")
        self._transport = transport
        self._timeout = timeout

    def decode(self, vin: str) -> VinDescription:
        normalized = _vin(vin)
        # The host is constant and VIN is strictly alphanumeric, not a user URL.
        url = f"{VPIC_ORIGIN}/api/vehicles/DecodeVinValues/{normalized}?format=json"
        try:
            content = self._transport(url, self._timeout)
            if type(content) is not bytes or len(content) > MAX_RESPONSE_BYTES:
                raise ValueError("invalid response size")
            body = json.loads(content)
            if type(body) is not dict or type(body.get("Results")) is not list:
                raise ValueError("invalid envelope")
            results = body["Results"]
            if len(results) != 1 or type(results[0]) is not dict:
                raise ValueError("invalid result")
            row = results[0]
            # NHTSA reports partial / invalid decodes in ErrorCode; never
            # interpret a partly populated row as verified vehicle identity.
            error_code = row.get("ErrorCode")
            if type(error_code) is not str or not error_code or any(
                part != "0" for part in error_code.split(",")
            ):
                raise CatalogError(CatalogErrorCode.NOT_FOUND)
            fields = []
            for key in ("Make", "Model", "ModelYear"):
                value = row.get(key)
                if type(value) is not str or not value.strip() or len(value) > 160:
                    raise CatalogError(CatalogErrorCode.NOT_FOUND)
                fields.append(value.strip())
            return VinDescription(normalized, *fields)
        except HTTPError as exc:
            if exc.code == 429:
                raise CatalogError(CatalogErrorCode.RATE_LIMITED) from None
            raise CatalogError(CatalogErrorCode.UNAVAILABLE) from None
        except CatalogError:
            raise
        except (URLError, TimeoutError, OSError, ValueError, TypeError, UnicodeError):
            raise CatalogError(CatalogErrorCode.UNAVAILABLE) from None
