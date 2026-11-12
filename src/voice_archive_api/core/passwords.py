"""Shared Argon2 configuration for account commands and sign-in."""

from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()


def create_password_hash(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    return password_hasher.hash(password)
