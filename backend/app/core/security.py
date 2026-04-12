"""
Password hashing, JWT token creation/decoding, and Fernet encryption helpers.
"""
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Module-level Fernet singleton — initialised on first use
_fernet = None


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    """Raises jose.JWTError if the token is invalid or expired."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def _get_fernet():
    """Return (and cache) the Fernet instance used for broker token encryption."""
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet

        key = settings.BROKER_TOKEN_ENCRYPTION_KEY
        if not key:
            # Dev fallback: derive a stable 32-byte key from SECRET_KEY.
            # In production always set BROKER_TOKEN_ENCRYPTION_KEY explicitly.
            derived = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
            key = base64.urlsafe_b64encode(derived).decode()
        if isinstance(key, str):
            key = key.encode()
        _fernet = Fernet(key)
    return _fernet


def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
