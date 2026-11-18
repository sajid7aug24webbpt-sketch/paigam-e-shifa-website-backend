"""Media library: list / upload / delete.

Uploads are validated by magic bytes (JPEG/PNG/WebP), size-capped
(post image <= 6 MB), stored under uploads/post-images/ with a CSPRNG key,
and served at /uploads/<key>. Avatar uploads live in the settings blueprint.
"""
import os

from flask import Blueprint, current_app, g, jsonify, request

from ..auth import login_required, require_min_role
from ..models import Media, Post, db
from ..security import detect_image, new_upload_key
from ..utils import log_activity
from .admin import _admin

media_bp = Blueprint("media", __name__, url_prefix="/api/admin/media")


@media_bp.route("", methods=["GET"])
@login_required
def list_media():
    items = Media.query.order_by(Media.created_at.desc()).all()
    out = []
    for m in items:
        used_by = [
            p.title
            for p in Post.query.filter(
                (Post.image_key == m.key) | (Post.image_url == m.url)
            ).all()
        ]
        out.append(m.to_dict(used_by=used_by))
    return jsonify(out)


@media_bp.route("", methods=["POST"])
@_admin()
@require_min_role("content_editor")
def upload_media():
    if "image" not in request.files:
        return jsonify({"error": "No file uploaded (field 'image')."}), 400
    f = request.files["image"]
    data = f.read()
    max_bytes = current_app.config["MAX_POST_IMAGE_MB"] * 1024 * 1024
    if len(data) > max_bytes:
        return jsonify(
            {"error": f"Image exceeds the {current_app.config['MAX_POST_IMAGE_MB']} MB limit."}
        ), 400
    detected = detect_image(data)
    if detected is None:
        return jsonify({"error": "Unsupported image type. Only JPEG, PNG and WebP are allowed."}), 400
    ext, mime = detected
    key = new_upload_key("post-images", ext)
    path = os.path.join(current_app.config["UPLOAD_DIR"], key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    media = Media(
        key=key,
        url=f"/uploads/{key}",
        filename=f.filename or f"upload.{ext}",
        size=len(data),
        mime=mime,
    )
    db.session.add(media)
    db.session.commit()
    log_activity(g.current_user.username, "upload-media", key)
    return jsonify(media.to_dict()), 201


@media_bp.route("/<int:media_id>", methods=["DELETE"])
@_admin()
@require_min_role("content_editor")
def delete_media(media_id):
    media = db.session.get(Media, media_id)
    if media is None:
        return jsonify({"error": "Media not found."}), 404
    path = os.path.join(current_app.config["UPLOAD_DIR"], media.key)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
    db.session.delete(media)
    db.session.commit()
    log_activity(g.current_user.username, "delete-media", media.key)
    return jsonify({"ok": True})
