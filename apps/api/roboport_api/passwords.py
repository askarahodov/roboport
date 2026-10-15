"""Offline password utility. Never exposes an authentication endpoint or logs secrets."""
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID)


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("invalid password length")
    return _hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    if not isinstance(password, str) or not isinstance(encoded_hash, str):
        return False
    if not encoded_hash.startswith("$argon2id$"):
        return False
    try:
        return bool(_hasher.verify(encoded_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
        return False


def needs_rehash(encoded_hash: str) -> bool:
    return _hasher.check_needs_rehash(encoded_hash)
