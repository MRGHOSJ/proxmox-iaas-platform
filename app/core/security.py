from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
import hashlib

import bcrypt
from app.core.config import settings
from jose import JWTError, jwt


def _prehash_password(password: str) -> bytes:
    """
    Pre-hash password with SHA256 before bcrypt to avoid 72-char truncation.
    Bcrypt silently truncates passwords at 72 characters, which could allow
    two different passwords (e.g., 80 chars vs 100 chars with same prefix)
    to hash to the same value.
    
    By pre-hashing with SHA256, we ensure the entire password is used
    regardless of length.
    """
    return hashlib.sha256(password.encode('utf-8')).hexdigest().encode('utf-8')


def hash_password(password: str) -> str:
    """
    Hashes a password using bcrypt with SHA256 pre-hashing.
    
    Security properties:
    - SHA256 pre-hash prevents 72-char bcrypt truncation
    - bcrypt adds salt and adaptive cost factor
    - Returns UTF-8 string for database storage
    """
    prehashed = _prehash_password(password)
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(prehashed, salt)
    
    return hashed_password.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plain password against a hashed password.
    Uses the same SHA256 pre-hashing as hash_password().
    """
    prehashed = _prehash_password(plain_password)
    hash_bytes = hashed_password.encode('utf-8')
    
    return bcrypt.checkpw(prehashed, hash_bytes)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=30)
    
    jti = str(uuid.uuid4())
    
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": jti
    })    
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.ALGORITHM)
    
    return encoded_jwt


def verify_token(token: str, credentials_exception: Exception) -> dict:
    """
    Decodes token and returns the full payload dictionary.
    Raises exception if invalid.
    """
    from app.core.token_blacklist import is_token_blacklisted
    
    ALLOWED_ALGORITHMS = ["HS256"]
    
    if not settings.JWT_SECRET_KEY:
        raise credentials_exception
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=ALLOWED_ALGORITHMS)
        
        if payload.get("sub") is None:
            raise credentials_exception
        
        jti = payload.get("jti")
        if jti and is_token_blacklisted(jti):
            raise credentials_exception
            
        return payload
    except JWTError:
        raise credentials_exception