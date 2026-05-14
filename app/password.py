"""Pure password-hashing utilities (no app.database dependency)."""

import bcrypt


def hash_password(plain: str) -> str:
    """Hash a password using bcrypt."""
    password_bytes = plain.encode('utf-8')
    # bcrypt has 72 byte limit, truncate if necessary
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    if hashed == 'disabled':
        return False
    password_bytes = plain.encode('utf-8')
    # bcrypt has 72 byte limit, truncate if necessary
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    hashed_bytes = hashed.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)
