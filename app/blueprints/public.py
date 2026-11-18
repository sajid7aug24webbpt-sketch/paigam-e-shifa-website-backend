"""Public (unauthenticated) API: health, posts, categories, tags."""
from flask import Blueprint, jsonify, request
from sqlalchemy import or_

from ..models import Category, Post, Tag, db, utcnow
from ..utils import parse_pagination, publish_due_posts

public_bp = Blueprint("public", __name__, url_prefix="/api")


@public_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


def _public_post_query():
    """Only published posts whose publishAt is null or already due."""
    now = utcnow()
    return Post.query.filter(
        Post.status == "published",
        or_(Post.publish_at.is_(None), Post.publish_at <= now),
    )


@public_bp.route("/posts", methods=["GET"])
def list_posts():
    # Lazy publishing: flip due scheduled posts before listing.
    publish_due_posts()
    page, per_page = parse_pagination(request.args, default_per_page=12)
    q = _public_post_query()

    type_filter = request.args.get("type")
    if type_filter in ("post", "blog"):
        q = q.filter(Post.type == type_filter)

    category = request.args.get("category")
    if category:
        if category.isdigit():
            q = q.filter(Post.category_id == int(category))
        else:
            cat = Category.query.filter(Category.name == category).first()
            q = q.filter(Post.category_id == cat.id) if cat else q.filter(False)

    tag = request.args.get("tag")
    if tag:
        q = q.join(Post.tags).filter(Tag.name == tag)

    search = request.args.get("search")
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Post.title.ilike(like), Post.text_en.ilike(like), Post.text_ur.ilike(like)))

    total = q.count()
    posts = (
        q.order_by(Post.post_date.desc().nullslast(), Post.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return jsonify(
        {
            "posts": [p.to_dict() for p in posts],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    )


@public_bp.route("/posts/<slug>", methods=["GET"])
def get_post(slug):
    publish_due_posts()
    post = _public_post_query().filter(Post.slug == slug).first()
    if post is None:
        return jsonify({"error": "Post not found."}), 404
    return jsonify(post.to_dict())


@public_bp.route("/categories", methods=["GET"])
def list_categories():
    cats = Category.query.order_by(Category.name.asc()).all()
    return jsonify([c.to_dict() for c in cats])


@public_bp.route("/tags", methods=["GET"])
def list_tags():
    # Tags with counts of published, visible posts using them.
    rows = (
        db.session.query(Tag.name, db.func.count(Post.id))
        .join(Tag.posts)
        .filter(Post.status == "published")
        .group_by(Tag.id, Tag.name)
        .order_by(Tag.name.asc())
        .all()
    )
    return jsonify([{"name": name, "count": count} for name, count in rows])
