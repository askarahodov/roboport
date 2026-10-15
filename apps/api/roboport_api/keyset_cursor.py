"""Process-local signed dev-only cursors, bound to session and filter.

Restart or switching workers invalidates outstanding cursors by design.
This is only used for the opt-in synthetic API, not production.
"""
import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime
from uuid import UUID

_KEY = secrets.token_bytes(32)


def encode_cursor(stamp: datetime, ticket: UUID, state: str | None, token: str) -> str:
    raw = json.dumps([stamp.isoformat(), str(ticket), state], separators=(",", ":")).encode()
    signature = hmac.new(_KEY, token.encode() + b":" + raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")


def decode_cursor(cursor: str, state: str | None, token: str) -> tuple[datetime, UUID]:
    # Reject alternate textual encodings for the same signed bytes and oversized input.
    # The cursor is a server-issued opaque value, not arbitrary caller JSON.
    if (not isinstance(cursor, str) or not cursor or len(cursor) > 256
            or not isinstance(token, str) or not token):
        raise ValueError("invalid cursor")
    try:
        blob = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        if (base64.urlsafe_b64encode(blob).decode().rstrip("=") != cursor
                or len(blob) <= 32):
            raise ValueError("noncanonical or short cursor")
        raw, signature = blob[:-32], blob[-32:]
        expected = hmac.new(_KEY, token.encode() + b":" + raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        data = json.loads(raw)
        if (not isinstance(data, list) or len(data) != 3
                or data[2] != state or not isinstance(data[0], str)
                or not isinstance(data[1], str)):
            raise ValueError("invalid cursor payload")
        stamp, ticket = datetime.fromisoformat(data[0]), UUID(data[1])
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("timezone required")
        return stamp, ticket
    except (UnicodeError, TypeError, OverflowError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
