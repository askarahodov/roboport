"""Negative tests for password hash library and absent auth endpoints."""
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.passwords import hash_password, verify_password, needs_rehash
import pytest


def test_argon2id_is_salted_and_verifies_without_plaintext():
    first = hash_password("Synthetic-demo-passphrase-2026!")
    second = hash_password("Synthetic-demo-passphrase-2026!")
    assert first.startswith("$argon2id$")
    assert first != second
    assert "Synthetic-demo-passphrase" not in first
    assert verify_password("Synthetic-demo-passphrase-2026!", first)
    assert not verify_password("wrong-passphrase!", first)
    assert not verify_password("Synthetic-demo-passphrase-2026!", "plaintext")
    assert not verify_password("Synthetic-demo-passphrase-2026!", "$argon2id$corrupt")
    assert not needs_rehash(first)


def test_password_length_policy():
    for bad in ("short", "", "x" * 1025):
        with pytest.raises(ValueError):
            hash_password(bad)


def test_unimplemented_auth_remains_closed():
    client = TestClient(app)
    for method, route in [
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/auth/logout"),
        ("get", "/api/v1/auth/me"),
    ]:
        response = getattr(client, method)(route, headers={"X-User-ID": "synthetic-admin"})
        assert response.status_code in (404, 405)
