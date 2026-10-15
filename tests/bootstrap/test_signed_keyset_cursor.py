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


@pytest.mark.parametrize("invalid", [
    None, 42, [], {}, " ", "a" * 257, "AA", "not-base64!",
])
def test_reject_invalid_cursor_shapes(invalid):
    with pytest.raises(ValueError):
        decode_cursor(invalid, "DRAFT", "a" * 43)


def test_reject_noncanonical_cursor_encodings():
    stamp=datetime.now(timezone.utc)
    cursor=encode_cursor(stamp,uuid4(),"DRAFT","a"*43)
    # Padding and noncanonical base64 spelling must not be accepted.
    for mutated in (cursor+"=", cursor+"==", cursor.replace("-", "+").replace("_", "/")):
        if mutated != cursor:
            with pytest.raises(ValueError):
                decode_cursor(mutated,"DRAFT","a"*43)


@pytest.mark.parametrize("token", [None, 42, "", [], {}])
def test_reject_invalid_session_token_types(token):
    cursor=encode_cursor(datetime.now(timezone.utc),uuid4(),"DRAFT","a"*43)
    with pytest.raises(ValueError):
        decode_cursor(cursor,"DRAFT",token)


def test_reject_signed_cursor_with_invalid_json_shape(monkeypatch):
    import hashlib
    import hmac
    import json
    import roboport_api.keyset_cursor as module

    token="a"*43
    for data in (None, {}, [1,2,3], [None,str(uuid4()),"DRAFT"],
                 [datetime.now(timezone.utc).isoformat(),None,"DRAFT"]):
        raw=json.dumps(data,separators=(",",":")).encode()
        mac=hmac.new(module._KEY,token.encode()+b":"+raw,hashlib.sha256).digest()
        cursor=base64.urlsafe_b64encode(raw+mac).decode().rstrip("=")
        with pytest.raises(ValueError):
            decode_cursor(cursor,"DRAFT",token)
