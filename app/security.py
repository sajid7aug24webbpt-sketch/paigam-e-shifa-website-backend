"""Security helpers: scrypt passwords, JWT sessions, login lockout,
upload validation, URL validation, username rules."""
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
KEY_RE = re.compile(r"^(post-images|avatars)/[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp)$")

# Dummy salt/hash used on the unknown-user path so a failed login for an
# unknown username costs the same scrypt work as a real check
# (anti-enumeration: same error message AND same timing profile).
_DUMMY_SALT = bytes.fromhex("00" * 32)


def hash_password(password, salt=None, *, n=16384, r=8, p=1, dklen=64):
    """Return (salt_hex, hash_hex) using hashlib.scrypt with a per-user salt."""
    if salt is None:
        salt = secrets.token_bytes(32)
    elif isinstance(salt, str):
        salt = bytes.fromhex(salt)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen
    )
    return salt.hex(), dk.hex()


def verify_password(password, salt_hex, hash_hex):
    """Constant-time password check. Returns False (never raises) on bad input."""
    try:
        _, candidate = hash_password(password, salt_hex)
        return hmac.compare_digest(candidate, hash_hex)
    except (ValueError, TypeError):
        return False


def dummy_password_check(password):
    """Run a scrypt hash against a fixed dummy salt (unknown-user path)."""
    hashlib.scrypt(
        password.encode("utf-8"), salt=_DUMMY_SALT, n=16384, r=8, p=1, dklen=64
    )
    return False


def validate_username(username):
    """Return an error string, or None when the username is valid."""
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        return "Username must be 3-30 characters: letters, numbers, dot, underscore or dash."
    return None


def validate_password(password):
    if not isinstance(password, str) or len(password) < 8:
        return "Password must be at least 8 characters."
    return None


def validate_http_url(value, field="url"):
    """Allow only http(s) URLs. Relative /uploads/... paths are allowed for
    imageUrl (files served by this backend); everything else must be absolute
    http(s). Rejects javascript:/data:/etc."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return f"{field} must be a string."
    v = value.strip()
    if field == "imageUrl" and v.startswith("/uploads/"):
        return None
    if v.startswith("http://") or v.startswith("https://"):
        return None
    return f"{field} must be an http(s) URL."


def create_token(user_id, username, role, secret, expiry, jti=None):
    now = datetime.now(timezone.utc)
    jti = jti or secrets.token_hex(16)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "jti": jti,
        "iat": now,
        "exp": now + expiry,
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token, jti


def decode_token(token, secret):
    return jwt.decode(token, secret, algorithms=["HS256"])


# --- Upload validation -------------------------------------------------

MAGIC = {
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
    b"\x89PNG": ("png", "image/png"),
}


def detect_image(data):
    """Return (ext, mime) from magic bytes, or None when not a supported image.

    JPEG: FF D8 FF. PNG: 89 50 4E 47. WebP: RIFF....WEBP.
    """
    if not data:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return ("jpg", "image/jpeg")
    if data[:4] == b"\x89PNG":
        return ("png", "image/png")
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("webp", "image/webp")
    return None


def new_upload_key(namespace, ext):
    """CSPRNG key under a fixed namespace, e.g. post-images/Ab3d...x9.jpg."""
    return f"{namespace}/{secrets.token_urlsafe(24)}.{ext}"


def is_valid_upload_key(key):
    """Strict key check: rejects path traversal and anything outside the two
    allowed namespaces."""
    return isinstance(key, str) and KEY_RE.match(key) is not None


def lockout_state(attempt, now):
    """Return (locked: bool, locked_until) for a LoginAttempt row."""
    if attempt and attempt.locked_until and attempt.locked_until > now:
        return True, attempt.locked_until
    return False, None


def lockout_remaining_seconds(locked_until, now):
    return max(0, int((locked_until - now).total_seconds()))
