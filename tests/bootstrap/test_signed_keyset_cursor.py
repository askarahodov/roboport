"""Signed cursor format is tamper-evident and bound to one session/filter."""
import base64
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from roboport_api.keyset_cursor import encode_cursor,decode_cursor


def test_signed_cursor_roundtrip_and_tamper_rejection():
    stamp=datetime.now(timezone.utc)
    ticket=uuid4()
    token="a"*43
    cursor=encode_cursor(stamp,ticket,"DRAFT",token)
    assert decode_cursor(cursor,"DRAFT",token)==(stamp,ticket)
    with pytest.raises(ValueError):
        decode_cursor(cursor,"COMPLETED",token)
    with pytest.raises(ValueError):
        decode_cursor(cursor,"DRAFT","b"*43)
    decoded=bytearray(base64.urlsafe_b64decode(cursor+"="*(-len(cursor)%4)))
    decoded[4]^=1
    corrupted=base64.urlsafe_b64encode(decoded).decode().rstrip("=")
    with pytest.raises(ValueError):
        decode_cursor(corrupted,"DRAFT",token)
    for value in ("not-base64!", "a"*257, "", "AA"):
        with pytest.raises(ValueError):
            decode_cursor(value,"DRAFT",token)
