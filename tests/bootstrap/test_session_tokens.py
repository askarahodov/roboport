"""Session token primitive tests; no browser cookies or login yet."""
from roboport_api.session_tokens import new_token, token_digest, digest_matches
import pytest


def test_session_and_csrf_values_random_and_only_digest_persistable():
    session = new_token()
    csrf = new_token()
    assert session != csrf
    assert len(session) >= 40 and len(csrf) >= 40
    stored = token_digest(session)
    assert len(stored) == 64
    assert session not in stored
    assert digest_matches(session, stored)
    assert not digest_matches(csrf, stored)
    assert not digest_matches(session, "bad")


def test_token_digest_rejects_empty_input():
    with pytest.raises(ValueError):
        token_digest("")
    assert not digest_matches("", "f" * 64)
