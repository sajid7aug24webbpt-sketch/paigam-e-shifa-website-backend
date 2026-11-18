"""Post CRUD, trash/restore/permanent delete, scheduling, role enforcement."""
from datetime import datetime, timedelta, timezone

from app.models import Post, db
from tests.conftest import auth_headers, login, make_user


def _create_post(client, headers, **overrides):
    payload = {
        "title": "Test Post",
        "type": "post",
        "textEn": "English text",
        "textUr": "Urdu text",
        "status": "draft",
    }
    payload.update(overrides)
    return client.post("/api/admin/posts", headers=headers, json=payload)


def test_create_post_as_editor(app, client, editor_user):
    resp = _create_post(client, editor_user, status="published")
    assert resp.status_code == 201, resp.get_json()
    data = resp.get_json()
    assert data["slug"] == "test-post"
    assert data["status"] == "published"
    assert data["textEn"] == "English text"


def test_slug_uniqueness(app, client, editor_user):
    assert _create_post(client, editor_user).status_code == 201
    resp = _create_post(client, editor_user)
    assert resp.status_code == 201
    assert resp.get_json()["slug"] == "test-post-2"


def test_viewer_cannot_create_post(client, viewer_user):
    resp = _create_post(client, viewer_user)
    assert resp.status_code == 403


def test_editor_cannot_manage_users(client, editor_user):
    assert client.get("/api/admin/users", headers=editor_user).status_code == 403
    resp = client.post(
        "/api/admin/users",
        headers=editor_user,
        json={"username": "X1", "password": "SomePass123", "role": "viewer"},
    )
    assert resp.status_code == 403


def test_admin_can_list_users_but_not_delete(client, admin_user, super_admin):
    # GET /api/admin/users is Super-Admin-only.
    assert client.get("/api/admin/users", headers=admin_user).status_code == 403
    resp = client.get("/api/admin/users", headers=super_admin)
    assert resp.status_code == 200
    assert any(u["username"] == "Sajid" for u in resp.get_json())


def test_update_post(app, client, editor_user):
    post_id = _create_post(client, editor_user).get_json()["id"]
    resp = client.put(
        f"/api/admin/posts/{post_id}",
        headers=editor_user,
        json={"title": "Renamed Post", "status": "published"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["slug"] == "renamed-post"


def test_trash_restore_cycle(app, client, editor_user):
    post_id = _create_post(client, editor_user, status="published").get_json()["id"]
    # Published post is publicly visible.
    assert client.get("/api/posts/test-post").status_code == 200
    # Trash it: gone from public + admin list, present in trash.
    resp = client.post(f"/api/admin/posts/{post_id}/trash", headers=editor_user)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "trashed"
    assert client.get("/api/posts/test-post").status_code == 404
    trash = client.get("/api/admin/trash", headers=editor_user).get_json()
    assert trash["total"] == 1
    # Restore: back as a draft (not auto-published).
    resp = client.post(f"/api/admin/posts/{post_id}/restore", headers=editor_user)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "draft"


def test_permanent_delete_only_from_trash(app, client, editor_user):
    post_id = _create_post(client, editor_user).get_json()["id"]
    # Not trashed yet: permanent delete is refused.
    resp = client.delete(f"/api/admin/posts/{post_id}", headers=editor_user)
    assert resp.status_code == 400
    with app.app_context():
        assert Post.query.get(post_id) is not None
    # Trash, then delete for real.
    client.post(f"/api/admin/posts/{post_id}/trash", headers=editor_user)
    resp = client.delete(f"/api/admin/posts/{post_id}", headers=editor_user)
    assert resp.status_code == 200
    with app.app_context():
        assert Post.query.get(post_id) is None


def test_set_status_endpoint(app, client, editor_user):
    post_id = _create_post(client, editor_user).get_json()["id"]
    resp = client.post(
        f"/api/admin/posts/{post_id}/status",
        headers=editor_user,
        json={"status": "published"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "published"
    # Scheduling without publishAt is refused.
    resp = client.post(
        f"/api/admin/posts/{post_id}/status",
        headers=editor_user,
        json={"status": "scheduled"},
    )
    assert resp.status_code == 400


def test_public_list_only_published(app, client, editor_user):
    _create_post(client, editor_user, title="Draft One", status="draft")
    _create_post(client, editor_user, title="Live One", status="published")
    data = client.get("/api/posts").get_json()
    titles = [p["title"] for p in data["posts"]]
    assert titles == ["Live One"]
    assert data["total"] == 1


def test_public_filters(app, client, editor_user):
    _create_post(client, editor_user, title="Cats Blog", type="blog",
                 status="published", tags=["islam", "health"])
    _create_post(client, editor_user, title="Dogs Post", type="post",
                 status="published", tags=["health"])
    assert client.get("/api/posts?type=blog").get_json()["total"] == 1
    assert client.get("/api/posts?tag=islam").get_json()["total"] == 1
    assert client.get("/api/posts?tag=health").get_json()["total"] == 2
    assert client.get("/api/posts?search=Dogs").get_json()["total"] == 1
    assert client.get("/api/posts?search=nomatch").get_json()["total"] == 0


def test_public_post_404_for_draft_slug(app, client, editor_user):
    _create_post(client, editor_user, title="Hidden Draft", status="draft")
    assert client.get("/api/posts/hidden-draft").status_code == 404
    assert client.get("/api/posts/does-not-exist").status_code == 404
