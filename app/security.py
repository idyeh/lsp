"""Password hashing (argon2id via argon2-cffi)."""

import hashlib

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

# Verified against when the username is unknown, so that a failed login takes
# about as long whether or not the account exists.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_fingerprint(password_hash: str) -> str:
    """A short value that changes whenever the password does (the hash embeds a fresh salt).

    Sessions record it at login; a session whose value no longer matches was started
    before the password was changed and is rejected. It reveals nothing about the password.
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]
