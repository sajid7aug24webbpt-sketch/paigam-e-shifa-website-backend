"""Authentication API: login / me / logout / change-password /
force-change-password.

- Login is case-insensitive (lookup on the lowercased username).
- Failures return ONE generic message for unknown users and wrong passwords.
  On the unknown-user path a dummy scrypt hash is computed first so the
  timing profile matches a real check (anti-enumeration).
- After 5 failed attempts for a (lowercased username, IP) the account is
  locked for 15 minutes (429).
- JWT (PyJWT HS256, 30-day expiry) with a jti stored in the sessions table;
  the jti is validated on every authenticated request so logout / revoke works.
"""
import re
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request

from ..auth import login_required, revoke_user_sessions
from ..models import LoginAttempt, Session, User, db, utcnow
from ..security import (
    create_token,
    dummy_password_check,
    validate_password,
    verify_password,
)
from ..utils import log_activity

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

GENERIC_LOGIN_ERROR = "Invalid username or password"
LOCKOUT_ERROR = "Too many login attempts. Try again in 15 minutes."


def _client_ip():
    # Respect X-Forwarded-For only for the first hop (behind the Nginx proxy).
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    username_lower = username.lower()
    ip = _client_ip()
    now = utcnow()

    attempt = LoginAttempt.query.filter_by(username_lower=username_lower, ip=ip).first()
    if attempt and attempt.locked_until and attempt.locked_until > now:
        return jsonify({"error": LOCKOUT_ERROR}), 429

    user = User.query.filter_by(username_lower=username_lower).first()
    ok = False
    if user is None:
        # Unknown user: burn the same scrypt work, then the generic error.
        dummy_password_check(password)
    elif not user.active:
        # Inactive account: still run the password check so the response
        # does not reveal that the account exists but is disabled.
        verify_password(password, user.salt, user.password_hash)
    else:
        ok = verify_password(password, user.salt, user.password_hash)

    if not ok:
        if attempt is None:
            attempt = LoginAttempt(username_lower=username_lower, ip=ip, attempts=1)
            db.session.add(attempt)
        else:
            attempt.attempts += 1
        max_failures = current_app.config["LOGIN_MAX_FAILURES"]
        lock_minutes = current_app.config["LOGIN_LOCK_MINUTES"]
        if attempt.attempts >= max_failures:
            attempt.locked_until = now + timedelta(minutes=lock_minutes)
        db.session.commit()
        if attempt.locked_until and attempt.locked_until > now:
            return jsonify({"error": LOCKOUT_ERROR}), 429
        return jsonify({"error": GENERIC_LOGIN_ERROR}), 401

    # Success: clear the failure counter for this (username, IP).
    if attempt:
        db.session.delete(attempt)
        db.session.commit()

    token, jti = create_token(
        user.id,
        user.username,
        user.role,
        current_app.config["JWT_SECRET"],
        current_app.config["JWT_EXPIRY"],
    )
    expires_at = utcnow() + current_app.config["JWT_EXPIRY"]
    db.session.add(Session(user_id=user.id, jti=jti, expires_at=expires_at))
    db.session.commit()
    log_activity(user.username, "login", "auth")
    return jsonify(
        {
            "token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "mustChangePassword": user.must_change_password,
                "avatarUrl": user.avatar_url,
            },
        }
    )


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    user = g.current_user
    return jsonify(
        {
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "mustChangePassword": user.must_change_password,
                "avatarUrl": user.avatar_url,
            }
        }
    )


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    g.current_session.revoked = True
    db.session.commit()
    return jsonify({"ok": True})


@auth_bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    from ..security import hash_password

    data = request.get_json(silent=True) or {}
    current = data.get("currentPassword") or ""
    new = data.get("newPassword") or ""
    user = g.current_user
    if not verify_password(current, user.salt, user.password_hash):
        return jsonify({"error": "Current password is incorrect."}), 400
    err = validate_password(new)
    if err:
        return jsonify({"error": err}), 400
    salt, pw_hash = hash_password(new)
    user.salt = salt
    user.password_hash = pw_hash
    user.must_change_password = False
    db.session.commit()
    # Revoke ALL other sessions of this user.
    revoke_user_sessions(user.id, except_jti=g.current_session.jti)
    log_activity(user.username, "change-password", f"user:{user.username}")
    return jsonify({"ok": True})


@auth_bp.route("/force-change-password", methods=["POST"])
@login_required
def force_change_password():
    from ..security import hash_password

    user = g.current_user
    if not user.must_change_password:
        return jsonify({"error": "Password change is not required."}), 400
    data = request.get_json(silent=True) or {}
    new = data.get("newPassword") or ""
    err = validate_password(new)
    if err:
        return jsonify({"error": err}), 400
    salt, pw_hash = hash_password(new)
    user.salt = salt
    user.password_hash = pw_hash
    user.must_change_password = False
    db.session.commit()
    log_activity(user.username, "force-change-password", f"user:{user.username}")
    return jsonify({"ok": True})
