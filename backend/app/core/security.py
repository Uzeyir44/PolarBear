"""
Security utilities — password hashing with Argon2 via pwdlib.

Hashing logic lives here (not inside route handlers) so it is reusable
and testable, and so route code never touches plain-text passwords
beyond passing them in for hashing/verification.
"""
from pwdlib import PasswordHash


class PasswordHasher:
    def __init__(self) -> None:
        # Argon2id with pwdlib's recommended parameters.
        self._hasher = PasswordHash.recommended()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        return self._hasher.verify(password, password_hash)


password_hasher = PasswordHasher()
