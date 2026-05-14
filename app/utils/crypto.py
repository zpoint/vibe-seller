"""Password encryption utilities using Fernet symmetric encryption."""

import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import JWT_SECRET


def _get_fernet() -> Fernet:
    """Derive a Fernet key from JWT_SECRET."""
    key = hashlib.sha256(JWT_SECRET.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_password(password: str) -> str:
    """Encrypt a password string using Fernet."""
    return _get_fernet().encrypt(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """Decrypt a Fernet-encrypted password string."""
    return _get_fernet().decrypt(encrypted.encode()).decode()
