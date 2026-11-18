"""Settings + self-service profile: avatar upload/delete, change-username.

Settings are a generic key/value store (site title, contact details, hours,
etc. — no invented content is seeded; the frontend reads what is stored).
"""
import os

from flask import Blueprint, current_app, g, jsonify, request

from ..auth import login_required, require_min_role
from ..models import Setting, db
from ..security import detect_image, new_upload_key, validate_username, verify_password
from ..utils import log_activity
from .admin import _admin

settings_bp = Blueprint("settings", __name__, url_prefix="/api/admin")

DEFAULT_SETTINGS = {
    "siteTitle": "Paigam-e-Shifa — Ruhani + Hakeem Clinic",
}


@settings_bp.route("/settings", methods=["GET"])
@login_required
def get_settings():
    rows = Setting.query.all()
    out = dict(DEFAULT_SETTINGS)
    out.update({r.key: r.value for r in rows})
    return jsonify(out)


@settings_bp.route("/settings", methods=["PUT"])
@_admin()
@require_min_role("admin")
def update_settings():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not data:
        return jsonify({"error": "Settings object is required."}), 400
    for key, value in data.items():
        if not isinstance(key, str) or len(key) > 100:
            return jsonify({"error": f"Invalid setting key: {key!r}"}), 400
        row = db.session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value="" if value is None else str(value))
            db.session.add(row)
        else:
            row.value = "" if value is None else str(value)
    db.session.commit()
    log_activity(g.current_user.username, "update-settings", ",".join(sorted(data.keys())))
    rows = Setting.query.all()
    out = dict(DEFAULT_SETTINGS)
    out.update({r.key: r.value for r in rows})
    return jsonify(out)


@settings_bp.route("/avatar", methods=["POST"])
@_admin()
def upload_avatar():
    """Self only: replaces the current user's avatar (<= 2 MB)."""
    if "image" not in request.files:
        return jsonify({"error": "No file uploaded (field 'image')."}), 400
    f = request.files["image"]
    data = f.read()
    max_bytes = current_app.config["MAX_AVATAR_MB"] * 1024 * 1024
    if len(data) > max_bytes:
        return jsonify(
            {"error": f"Avatar exceeds the {current_app.config['MAX_AVATAR_MB']} MB limit."}
        ), 400
    detected = detect_image(data)
    if detected is None:
        return jsonify({"error": "Unsupported image type. Only JPEG, PNG and WebP are allowed."}), 400
    ext, mime = detected
    key = new_upload_key("avatars", ext)
    path = os.path.join(current_app.config["UPLOAD_DIR"], key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    user = g.current_user
    _delete_avatar_file(user)
    user.avatar_key = key
    user.avatar_url = f"/uploads/{key}"
    db.session.commit()
    log_activity(user.username, "upload-avatar", user.username)
    return jsonify({"ok": True, "avatarUrl": user.avatar_url})


def _delete_avatar_file(user):
    if user.avatar_key:
        path = os.path.join(current_app.config["UPLOAD_DIR"], user.avatar_key)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


@settings_bp.route("/avatar", methods=["DELETE"])
@_admin()
def delete_avatar():
    user = g.current_user
    _delete_avatar_file(user)
    user.avatar_key = None
    user.avatar_url = None
    db.session.commit()
    log_activity(user.username, "delete-avatar", user.username)
    return jsonify({"ok": True})


@settings_bp.route("/change-username", methods=["POST"])
@_admin()
@require_min_role("admin")
def change_username():
    data = request.get_json(silent=True) or {}
    new_username = (data.get("newUsername") or "").strip()
    current_password = data.get("currentPassword") or ""
    err = validate_username(new_username)
    if err:
        return jsonify({"error": err}), 400
    user = g.current_user
    if not verify_password(current_password, user.salt, user.password_hash):
        return jsonify({"error": "Current password is incorrect."}), 400
    existing = User.query.filter_by(username_lower=new_username.lower()).first()
    if existing and existing.id != user.id:
        return jsonify({"error": "Username already exists."}), 400
    old = user.username
    user.username = new_username
    user.username_lower = new_username.lower()
    db.session.commit()
    log_activity(user.username, "change-username", f"{old} -> {new_username}")
    return jsonify({"ok": True, "username": new_username})
