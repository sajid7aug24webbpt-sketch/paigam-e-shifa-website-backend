"""Shared helpers: slugs, lazy publishing, activity logging, pagination."""
import re
import unicodedata
from datetime import datetime, timezone

from .models import ActivityLog, Post, db, utcnow


def slugify(title):
    text = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "post"


def unique_slug(title, exclude_id=None):
    base = slugify(title)
    slug = base
    n = 2
    while True:
        q = Post.query.filter_by(slug=slug)
        if exclude_id is not None:
            q = q.filter(Post.id != exclude_id)
        if q.first() is None:
            return slug
        slug = f"{base}-{n}"
        n += 1


def publish_due_posts():
    """Flip scheduled posts whose publishAt has passed to published.

    Called lazily on public reads, at app startup, and by the
    POST /api/admin/publish-due endpoint (for a cron/scheduler).
    Returns the number of posts flipped.
    """
    now = utcnow()
    due = (
        Post.query.filter(
            Post.status == "scheduled",
            Post.publish_at.isnot(None),
            Post.publish_at <= now,
        )
        .all()
    )
    for post in due:
        post.status = "published"
    if due:
        db.session.commit()
    return len(due)


def log_activity(actor, action, target=None):
    db.session.add(ActivityLog(actor=actor, action=action, target=target))
    db.session.commit()


def parse_pagination(args, default_per_page=12, max_per_page=50):
    try:
        page = max(1, int(args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(args.get("per_page", default_per_page))
    except (TypeError, ValueError):
        per_page = default_per_page
    per_page = min(max(1, per_page), max_per_page)
    return page, per_page


def parse_datetime(value):
    """Parse an ISO-8601 datetime (with optional Z / offset). Returns a
    timezone-aware datetime or None. Raises ValueError on bad input."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Store naive UTC (see models.utcnow).
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def parse_date(value):
    """Parse an ISO date (YYYY-MM-DD). Returns a date or None."""
    if value is None or value == "":
        return None
    from datetime import date

    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).strip()).date()
