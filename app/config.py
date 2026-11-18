"""Application configuration. All secrets come from environment variables.

Production deploy target is MySQL 8 (DATABASE_URL like
``mysql+pymysql://user:pass@host:3306/paigam_shifa``).
SQLite is supported ONLY for local dev / tests — never for production.
"""
import os
from datetime import timedelta


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    JWT_SECRET = os.environ.get("JWT_SECRET", os.environ.get("SECRET_KEY", "dev-secret-change-me"))
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///paigam_shifa_dev.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # MySQL: recycle pooled connections before the server closes them.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    JWT_EXPIRY = timedelta(days=30)
    FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads"))
    MAX_AVATAR_MB = _int_env("MAX_AVATAR_MB", 2)
    MAX_POST_IMAGE_MB = _int_env("MAX_POST_IMAGE_MB", 6)
    # Login lockout
    LOGIN_MAX_FAILURES = 5
    LOGIN_LOCK_MINUTES = 15
    # scrypt parameters (hashlib.scrypt)
    SCRYPT_N = 16384
    SCRYPT_R = 8
    SCRYPT_P = 1
    SCRYPT_DKLEN = 64


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-that-is-long-enough-1234"
    JWT_SECRET = "test-jwt-secret-that-is-long-enough-1234"
