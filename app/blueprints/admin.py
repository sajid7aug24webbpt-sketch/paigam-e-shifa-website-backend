"""Admin API: dashboard, posts (CRUD + status + trash/restore), categories,
tags, activity log, publish-due.

Every route requires a Bearer token (login_required) and server-side role
checks. Viewers are read-only: any non-GET admin call from a viewer is 403.
Content editors may manage posts/categories/tags/media; user management and
settings are admin+ (with the Super-Admin-only ops listed in app/auth.py).
"""
from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_

from ..auth import login_required, require_min_role, require_write
from ..models import ActivityLog, Category, Media, Post, Tag, User, db, post_tags, utcnow
from ..security import validate_http_url
from ..utils import (
    log_activity,
    parse_date,
    parse_datetime,
    parse_pagination,
    publish_due_posts,
    unique_slug,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _admin():
    """Decorator stack shared by every admin route."""
    def decorator(f):
        return login_required(require_write()(f))
    return decorator


# --- Dashboard / activity -------------------------------------------------

@admin_bp.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    counts = {
        "posts": Post.query.filter(Post.status != "trashed").count(),
        "published": Post.query.filter_by(status="published").count(),
        "drafts": Post.query.filter_by(status="draft").count(),
        "scheduled": Post.query.filter_by(status="scheduled").count(),
        "trash": Post.query.filter_by(status="trashed").count(),
        "categories": Category.query.count(),
        "tags": Tag.query.count(),
        "media": Media.query.count(),
        "users": User.query.count(),
    }
    recent = (
        ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(20).all()
    )
    return jsonify({"counts": counts, "recentActivity": [a.to_dict() for a in recent]})


@admin_bp.route("/activity", methods=["GET"])
@login_required
def activity():
    page, per_page = parse_pagination(request.args, default_per_page=50)
    q = ActivityLog.query.order_by(ActivityLog.created_at.desc())
    total = q.count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()
    return jsonify(
        {"activity": [a.to_dict() for a in rows], "total": total, "page": page, "per_page": per_page}
    )


@admin_bp.route("/publish-due", methods=["POST"])
@_admin()
@require_min_role("content_editor")
def publish_due():
    """Flip due scheduled posts to published. Intended for a cron/scheduler;
    the same flip also happens lazily on public reads and at app startup."""
    flipped = publish_due_posts()
    log_activity(g.current_user.username, "publish-due", f"{flipped} post(s)")
    return jsonify({"ok": True, "published": flipped})


# --- Posts -----------------------------------------------------------------

def _get_or_create_tags(names):
    tags = []
    for raw in names or []:
        name = str(raw).strip()
        if not name:
            continue
        tag = Tag.query.filter_by(name=name).first()
        if tag is None:
            tag = Tag(name=name)
            db.session.add(tag)
        tags.append(tag)
    return tags


def _validate_post_payload(data, partial=False):
    """Return an error string or None. Checks type/status/URLs/dates."""
    if not partial or "type" in data:
        if data.get("type") not in ("post", "blog"):
            return "type must be 'post' or 'blog'."
    if not partial or "status" in data:
        if data.get("status") not in ("draft", "published", "scheduled"):
            return "status must be 'draft', 'published' or 'scheduled'."
    if "imageUrl" in data:
        err = validate_http_url(data.get("imageUrl"), "imageUrl")
        if err:
            return err
    if "linkUrl" in data:
        err = validate_http_url(data.get("linkUrl"), "linkUrl")
        if err:
            return err
    if "publishAt" in data and data.get("publishAt"):
        try:
            parse_datetime(data["publishAt"])
        except ValueError:
            return "publishAt must be an ISO-8601 datetime."
    if "postDate" in data and data.get("postDate"):
        try:
            parse_date(data["postDate"])
        except ValueError:
            return "postDate must be an ISO date (YYYY-MM-DD)."
    return None


def _apply_post_fields(post, data):
    if "title" in data:
        post.title = data["title"]
    if "type" in data:
        post.type = data["type"]
    if "textEn" in data:
        post.text_en = data.get("textEn") or ""
    if "textUr" in data:
        post.text_ur = data.get("textUr") or ""
    if "categoryId" in data:
        cat_id = data.get("categoryId")
        post.category_id = int(cat_id) if cat_id else None
    if "imageUrl" in data:
        post.image_url = data.get("imageUrl") or None
    if "linkUrl" in data:
        post.link_url = data.get("linkUrl") or None
    if "status" in data:
        post.status = data["status"]
    if "publishAt" in data:
        post.publish_at = parse_datetime(data.get("publishAt")) if data.get("publishAt") else None
    if "postDate" in data:
        post.post_date = parse_date(data.get("postDate")) if data.get("postDate") else None
    if "tags" in data:
        post.tags = _get_or_create_tags(data.get("tags"))


@admin_bp.route("/posts", methods=["GET"])
@login_required
def list_admin_posts():
    page, per_page = parse_pagination(request.args, default_per_page=20)
    q = Post.query.filter(Post.status != "trashed")
    status = request.args.get("status")
    if status in ("draft", "published", "scheduled"):
        q = q.filter(Post.status == status)
    type_filter = request.args.get("type")
    if type_filter in ("post", "blog"):
        q = q.filter(Post.type == type_filter)
    search = request.args.get("search")
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Post.title.ilike(like), Post.text_en.ilike(like), Post.text_ur.ilike(like)))
    total = q.count()
    posts = (
        q.order_by(Post.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return jsonify(
        {"posts": [p.to_dict() for p in posts], "total": total, "page": page, "per_page": per_page}
    )


@admin_bp.route("/posts", methods=["POST"])
@_admin()
@require_min_role("content_editor")
def create_post():
    data = request.get_json(silent=True) or {}
    if not (data.get("title") or "").strip():
        return jsonify({"error": "title is required."}), 400
    err = _validate_post_payload(data)
    if err:
        return jsonify({"error": err}), 400
    if data.get("categoryId"):
        if db.session.get(Category, int(data["categoryId"])) is None:
            return jsonify({"error": "categoryId does not exist."}), 400
    post = Post(title=data["title"].strip(), slug=unique_slug(data["title"]))
    _apply_post_fields(post, data)
    db.session.add(post)
    db.session.commit()
    log_activity(g.current_user.username, "create-post", post.slug)
    return jsonify(post.to_dict()), 201


@admin_bp.route("/posts/<int:post_id>", methods=["GET"])
@login_required
def get_admin_post(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        return jsonify({"error": "Post not found."}), 404
    return jsonify(post.to_dict())


@admin_bp.route("/posts/<int:post_id>", methods=["PUT"])
@_admin()
@require_min_role("content_editor")
def update_post(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        return jsonify({"error": "Post not found."}), 404
    if post.status == "trashed":
        return jsonify({"error": "Restore the post from trash before editing it."}), 400
    data = request.get_json(silent=True) or {}
    err = _validate_post_payload(data, partial=True)
    if err:
        return jsonify({"error": err}), 400
    if "title" in data and data["title"].strip() != post.title:
        post.slug = unique_slug(data["title"], exclude_id=post.id)
    if data.get("categoryId"):
        if db.session.get(Category, int(data["categoryId"])) is None:
            return jsonify({"error": "categoryId does not exist."}), 400
    _apply_post_fields(post, data)
    db.session.commit()
    log_activity(g.current_user.username, "update-post", post.slug)
    return jsonify(post.to_dict())


@admin_bp.route("/posts/<int:post_id>/status", methods=["POST"])
@_admin()
@require_min_role("content_editor")
def set_post_status(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        return jsonify({"error": "Post not found."}), 404
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("draft", "published", "scheduled"):
        return jsonify({"error": "status must be 'draft', 'published' or 'scheduled'."}), 400
    if status == "scheduled":
        publish_at = data.get("publishAt")
        if publish_at:
            try:
                post.publish_at = parse_datetime(publish_at)
            except ValueError:
                return jsonify({"error": "publishAt must be an ISO-8601 datetime."}), 400
        if not post.publish_at:
            return jsonify({"error": "publishAt is required for scheduled posts."}), 400
    post.status = status
    db.session.commit()
    log_activity(g.current_user.username, f"set-post-status:{status}", post.slug)
    return jsonify(post.to_dict())


@admin_bp.route("/posts/<int:post_id>/trash", methods=["POST"])
@_admin()
@require_min_role("content_editor")
def trash_post(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        return jsonify({"error": "Post not found."}), 404
    post.status = "trashed"
    post.deleted_at = utcnow()
    db.session.commit()
    log_activity(g.current_user.username, "trash-post", post.slug)
    return jsonify(post.to_dict())


@admin_bp.route("/posts/<int:post_id>/restore", methods=["POST"])
@_admin()
@require_min_role("content_editor")
def restore_post(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        return jsonify({"error": "Post not found."}), 404
    if post.status != "trashed":
        return jsonify({"error": "Post is not in trash."}), 400
    post.status = "draft"
    post.deleted_at = None
    db.session.commit()
    log_activity(g.current_user.username, "restore-post", post.slug)
    return jsonify(post.to_dict())


@admin_bp.route("/posts/<int:post_id>", methods=["DELETE"])
@_admin()
@require_min_role("content_editor")
def delete_post(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        return jsonify({"error": "Post not found."}), 404
    if post.status != "trashed":
        return jsonify({"error": "Only trashed posts can be permanently deleted. Trash it first."}), 400
    slug = post.slug
    db.session.delete(post)
    db.session.commit()
    log_activity(g.current_user.username, "delete-post", slug)
    return jsonify({"ok": True})


@admin_bp.route("/trash", methods=["GET"])
@login_required
def list_trash():
    page, per_page = parse_pagination(request.args, default_per_page=20)
    q = Post.query.filter_by(status="trashed").order_by(Post.deleted_at.desc())
    total = q.count()
    posts = q.offset((page - 1) * per_page).limit(per_page).all()
    return jsonify(
        {"posts": [p.to_dict() for p in posts], "total": total, "page": page, "per_page": per_page}
    )


# --- Categories --------------------------------------------------------------

@admin_bp.route("/categories", methods=["GET"])
@login_required
def list_admin_categories():
    cats = Category.query.order_by(Category.name.asc()).all()
    return jsonify([c.to_dict() for c in cats])


@admin_bp.route("/categories", methods=["POST"])
@_admin()
@require_min_role("content_editor")
def create_category():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required."}), 400
    if Category.query.filter_by(name=name).first():
        return jsonify({"error": "Category already exists."}), 400
    cat = Category(name=name)
    db.session.add(cat)
    db.session.commit()
    log_activity(g.current_user.username, "create-category", name)
    return jsonify(cat.to_dict()), 201


@admin_bp.route("/categories/<int:cat_id>", methods=["PUT"])
@_admin()
@require_min_role("content_editor")
def update_category(cat_id):
    cat = db.session.get(Category, cat_id)
    if cat is None:
        return jsonify({"error": "Category not found."}), 404
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required."}), 400
    existing = Category.query.filter_by(name=name).first()
    if existing and existing.id != cat.id:
        return jsonify({"error": "Category already exists."}), 400
    old = cat.name
    cat.name = name
    db.session.commit()
    log_activity(g.current_user.username, "update-category", f"{old} -> {name}")
    return jsonify(cat.to_dict())


@admin_bp.route("/categories/<int:cat_id>", methods=["DELETE"])
@_admin()
@require_min_role("content_editor")
def delete_category(cat_id):
    cat = db.session.get(Category, cat_id)
    if cat is None:
        return jsonify({"error": "Category not found."}), 404
    # Posts keep existing content; their category is simply unset.
    Post.query.filter_by(category_id=cat.id).update({"category_id": None})
    db.session.delete(cat)
    db.session.commit()
    log_activity(g.current_user.username, "delete-category", cat.name)
    return jsonify({"ok": True})


# --- Tags ---------------------------------------------------------------------

@admin_bp.route("/tags", methods=["GET"])
@login_required
def list_admin_tags():
    rows = (
        db.session.query(Tag, db.func.count(post_tags.c.post_id))
        .outerjoin(post_tags, Tag.id == post_tags.c.tag_id)
        .group_by(Tag.id)
        .order_by(Tag.name.asc())
        .all()
    )
    return jsonify([tag.to_dict(count=count) for tag, count in rows])


@admin_bp.route("/tags/rename", methods=["POST"])
@_admin()
@require_min_role("content_editor")
def rename_tag():
    data = request.get_json(silent=True) or {}
    old_name = (data.get("oldName") or "").strip()
    new_name = (data.get("newName") or "").strip()
    if not old_name or not new_name:
        return jsonify({"error": "oldName and newName are required."}), 400
    tag = Tag.query.filter_by(name=old_name).first()
    if tag is None:
        return jsonify({"error": "Tag not found."}), 404
    target = Tag.query.filter_by(name=new_name).first()
    if target and target.id != tag.id:
        # Merge: move all post links to the existing target, then drop the old tag.
        for post in list(tag.posts):
            if target not in post.tags:
                post.tags.append(target)
        db.session.delete(tag)
        db.session.commit()
        log_activity(g.current_user.username, "rename-tag", f"{old_name} -> {new_name} (merged)")
        return jsonify(target.to_dict())
    tag.name = new_name
    db.session.commit()
    log_activity(g.current_user.username, "rename-tag", f"{old_name} -> {new_name}")
    return jsonify(tag.to_dict())


@admin_bp.route("/tags/<name>", methods=["DELETE"])
@_admin()
@require_min_role("content_editor")
def delete_tag(name):
    tag = Tag.query.filter_by(name=name).first()
    if tag is None:
        return jsonify({"error": "Tag not found."}), 404
    db.session.delete(tag)
    db.session.commit()
    log_activity(g.current_user.username, "delete-tag", name)
    return jsonify({"ok": True})
