"""Opaque session material; plaintext tokens belong only in temporary request handling."""
import hashlib
import hmac
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(raw_token: str) -> str:
    if not isinstance(raw_token, str) or not raw_token:
        raise ValueError("token must be nonempty")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def digest_matches(raw_token: str, stored_digest: str) -> bool:
    if not isinstance(stored_digest, str) or len(stored_digest) != 64:
        return False
    try:
        return hmac.compare_digest(token_digest(raw_token), stored_digest)
    except ValueError:
        return False
