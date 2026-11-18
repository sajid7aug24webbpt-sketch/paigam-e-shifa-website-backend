"""Scheduling: scheduled posts stay invisible until due, then flip."""
from datetime import datetime, timedelta, timezone

from app.models import Post, db
from tests.conftest import make_user


def _scheduled_post(client, editor_headers, publish_at):
    resp = client.post(
        "/api/admin/posts",
        headers=editor_headers,
        json={
            "title": "Future Post",
            "type": "post",
            "textEn": "soon",
            "status": "scheduled",
            "publishAt": publish_at,
        },
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def test_scheduled_post_invisible_before_due(app, client, editor_user):
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    _scheduled_post(client, editor_user, future)
    data = client.get("/api/posts").get_json()
    assert data["total"] == 0
    assert client.get("/api/posts/future-post").status_code == 404


def test_scheduled_post_visible_after_due_flip(app, client, editor_user):
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    post = _scheduled_post(client, editor_user, future)
    # Move publishAt into the past directly (simulating time passing).
    with app.app_context():
        row = Post.query.get(post["id"])
        row.publish_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
    # A public-list request lazily flips it.
    data = client.get("/api/posts").get_json()
    assert data["total"] == 1
    assert data["posts"][0]["status"] == "published"
    with app.app_context():
        assert Post.query.get(post["id"]).status == "published"


def test_publish_due_endpoint(app, client, editor_user):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    post = _scheduled_post(client, editor_user, past)
    # Created with a past publishAt but still 'scheduled' until flipped.
    assert client.get("/api/posts").get_json()["total"] == 1  # lazy flip already ran
    # Re-schedule it to the past again and flip via the endpoint instead.
    with app.app_context():
        row = Post.query.get(post["id"])
        row.status = "scheduled"
        row.publish_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.session.commit()
    resp = client.post("/api/admin/publish-due", headers=editor_user)
    assert resp.status_code == 200
    assert resp.get_json()["published"] == 1
    with app.app_context():
        assert Post.query.get(post["id"]).status == "published"


def test_publish_due_forbidden_for_viewer(client, viewer_user):
    assert client.post("/api/admin/publish-due", headers=viewer_user).status_code == 403
