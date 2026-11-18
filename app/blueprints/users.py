"""User management (admin area).

Super-Admin-only: list users, create users, delete users, reset-password.
Admins may edit (role/active) non-Super-Admin users, but may not promote
anyone to super_admin and may not touch Super Admin accounts.
Protected on every path: nobody may delete / deactivate / demote themselves
or the last active Super Admin. Deactivation ends that user's sessions
immediately.
"""
import secrets

from flask import Blueprint, g, jsonify, request

from ..auth import login_required, require_min_role, revoke_user_sessions
from ..models import User, db
from ..security import hash_password, validate_password, validate_username
from ..utils import log_activity
from .admin import _admin

users_bp = Blueprint("users", __name__, url_prefix="/api/admin/users")

VALID_ROLES = ("super_admin", "admin", "content_editor", "viewer")


def _last_active_super_admin(user):
    """True when `user` is the only active Super Admin."""
    if user.role != "super_admin" or not user.active:
        return False
    return User.query.filter_by(role="super_admin", active=True).count() <= 1


@users_bp.route("", methods=["GET"])
@login_required
@require_min_role("super_admin")
def list_users():
    users = User.query.order_by(User.username.asc()).all()
    return jsonify([u.to_dict() for u in users])


@users_bp.route("", methods=["POST"])
@login_required
@require_min_role("super_admin")
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") or "viewer"
    err = validate_username(username)
    if err:
        return jsonify({"error": err}), 400
    err = validate_password(password)
    if err:
        return jsonify({"error": err}), 400
    if role not in VALID_ROLES:
        return jsonify({"error": "Invalid role."}), 400
    if User.query.filter_by(username_lower=username.lower()).first():
        return jsonify({"error": "Username already exists."}), 400
    salt, pw_hash = hash_password(password)
    user = User(
        username=username,
        username_lower=username.lower(),
        password_hash=pw_hash,
        salt=salt,
        role=role,
        active=True,
        must_change_password=True,  # new users must set their own password
    )
    db.session.add(user)
    db.session.commit()
    log_activity(g.current_user.username, "create-user", username)
    return jsonify(user.to_dict()), 201


@users_bp.route("/<int:user_id>", methods=["PUT"])
@_admin()
@require_min_role("admin")
def update_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"error": "User not found."}), 404
    data = request.get_json(silent=True) or {}

    # Only Super Admins may touch Super Admin accounts or grant super_admin.
    is_super = g.current_user.role == "super_admin"
    new_role = data.get("role")
    if not is_super and (user.role == "super_admin" or new_role == "super_admin"):
        return jsonify({"error": "Forbidden: only a Super Admin may manage Super Admin accounts."}), 403
    if new_role is not None and new_role not in VALID_ROLES:
        return jsonify({"error": "Invalid role."}), 400

    if new_role is not None and new_role != user.role:
        if user.id == g.current_user.id:
            return jsonify({"error": "You cannot change your own role."}), 400
        if _last_active_super_admin(user) and new_role != "super_admin":
            return jsonify({"error": "Cannot demote the last active Super Admin."}), 400
        user.role = new_role

    if "active" in data:
        new_active = bool(data.get("active"))
        if user.id == g.current_user.id and not new_active:
            return jsonify({"error": "You cannot deactivate yourself."}), 400
        if _last_active_super_admin(user) and not new_active:
            return jsonify({"error": "Cannot deactivate the last active Super Admin."}), 400
        user.active = new_active
        if not new_active:
            # Deactivation ends the user's sessions immediately.
            revoke_user_sessions(user.id)

    db.session.commit()
    log_activity(g.current_user.username, "update-user", user.username)
    return jsonify(user.to_dict())


@users_bp.route("/<int:user_id>", methods=["DELETE"])
@login_required
@require_min_role("super_admin")
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"error": "User not found."}), 404
    if user.id == g.current_user.id:
        return jsonify({"error": "You cannot delete yourself."}), 400
    if _last_active_super_admin(user):
        return jsonify({"error": "Cannot delete the last active Super Admin."}), 400
    revoke_user_sessions(user.id)
    db.session.delete(user)
    db.session.commit()
    log_activity(g.current_user.username, "delete-user", user.username)
    return jsonify({"ok": True})


@users_bp.route("/<int:user_id>/reset-password", methods=["POST"])
@login_required
@require_min_role("super_admin")
def reset_password(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"error": "User not found."}), 404
    data = request.get_json(silent=True) or {}
    temp = data.get("temporaryPassword") or ""
    generated = False
    if not temp:
        # No temporary password supplied: generate a one-time one and return
        # it so the Super Admin can share it with the staff member.
        temp = secrets.token_urlsafe(12)
        generated = True
    err = validate_password(temp)
    if err:
        return jsonify({"error": err}), 400
    salt, pw_hash = hash_password(temp)
    user.salt = salt
    user.password_hash = pw_hash
    user.must_change_password = True
    db.session.commit()
    # Ends that user's sessions; they must sign in with the temporary password
    # and then change it (force-change-password).
    revoke_user_sessions(user.id)
    log_activity(g.current_user.username, "reset-password", user.username)
    out = {"ok": True}
    if generated:
        out["temporaryPassword"] = temp
    return jsonify(out)
