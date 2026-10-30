"""Provider-neutral OEM sandbox boundary (#111).

No vendor endpoints, secrets, scraping, or production credentials. A licensed
transport may be injected only after legal/security approval.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .auto_parts_boundary import parse_provider_parts, provider_error
from .auto_parts_provider import (
    CatalogError, CatalogErrorCode, CatalogPart, _article,
)


class OemSandboxAdapter:
    """Normalizes external responses without trusting fitment assertions."""

    def __init__(self, transport: Callable[[str, str], tuple[int, object]]):
        self._transport = transport

    def search(self, article: str, *, alternatives: bool = False) -> tuple[CatalogPart, ...]:
        normalized = _article(article)
        try:
            status, data = self._transport(
                "alternatives" if alternatives else "oem", normalized
            )
            if type(status) is not int:
                raise CatalogError(CatalogErrorCode.UNAVAILABLE)
            if status != 200:
                raise provider_error(status)
            # No external cross reference may imply verified VIN fitment.
            return parse_provider_parts(data, is_cross=alternatives)
        except CatalogError:
            raise
        except (TimeoutError, OSError, ValueError, TypeError):
            raise CatalogError(CatalogErrorCode.UNAVAILABLE) from None
