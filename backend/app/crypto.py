"""Symmetric encryption for secrets stored in Supabase (ESPN cookies, Anthropic keys)."""
from cryptography.fernet import Fernet, InvalidToken

from app import config

_fernet: Fernet | None = None


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        if not config.APP_ENCRYPTION_KEY:
            raise RuntimeError("APP_ENCRYPTION_KEY is not set")
        _fernet = Fernet(config.APP_ENCRYPTION_KEY.encode())
    return _fernet


def encrypt(value: str | None) -> str | None:
    return _f().encrypt(value.encode()).decode() if value else None


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _f().decrypt(token.encode()).decode()
    except InvalidToken:  # key rotated or data corrupted - treat as missing
        return None
