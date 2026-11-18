"""Admin rules: users, roles, settings, activity log, categories/tags."""
from app.models import Session, User, db
from tests.conftest import auth_headers, login, make_user


def test_create_user_must_change_password(app, client, super_admin):
    resp = client.post(
        "/api/admin/users",
        headers=super_admin,
        json={"username": "Staff1", "password": "StaffPass123", "role": "content_editor"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["mustChangePassword"] is True
    # The new user can log in, and the flag is reported.
    resp = login(client, "Staff1", "StaffPass123")
    assert resp.status_code == 200
    assert resp.get_json()["user"]["mustChangePassword"] is True


def test_create_user_bad_username_or_short_password(app, client, super_admin):
    resp = client.post(
        "/api/admin/users",
        headers=super_admin,
        json={"username": "ab", "password": "LongEnough123", "role": "viewer"},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/admin/users",
        headers=super_admin,
        json={"username": "GoodName", "password": "short", "role": "viewer"},
    )
    assert resp.status_code == 400


def test_admin_cannot_delete_super_admin(app, client, admin_user, super_admin):
    # Admins cannot even call the delete endpoint (Super-Admin-only).
    users = client.get("/api/admin/users", headers=super_admin).get_json()
    sajid = next(u for u in users if u["username"] == "Sajid")
    resp = client.delete(f"/api/admin/users/{sajid['id']}", headers=admin_user)
    assert resp.status_code == 403


def test_super_admin_cannot_delete_self(app, client, super_admin):
    users = client.get("/api/admin/users", headers=super_admin).get_json()
    sajid = next(u for u in users if u["username"] == "Sajid")
    resp = client.delete(f"/api/admin/users/{sajid['id']}", headers=super_admin)
    assert resp.status_code == 400


def test_super_admin_cannot_deactivate_or_demote_self(app, client, super_admin):
    users = client.get("/api/admin/users", headers=super_admin).get_json()
    sajid = next(u for u in users if u["username"] == "Sajid")
    resp = client.put(
        f"/api/admin/users/{sajid['id']}", headers=super_admin, json={"active": False}
    )
    assert resp.status_code == 400
    resp = client.put(
        f"/api/admin/users/{sajid['id']}", headers=super_admin, json={"role": "admin"}
    )
    assert resp.status_code == 400


def test_last_active_super_admin_helper(app):
    from app.blueprints.users import _last_active_super_admin

    make_user(app, "Sajid", "SuperSecret123", role="super_admin")
    with app.app_context():
        user = User.query.filter_by(username_lower="sajid").first()
        assert _last_active_super_admin(user) is True
        make_user(app, "SecondSA", "SecondPass123", role="super_admin")
        assert _last_active_super_admin(user) is False


def test_deactivation_ends_sessions_immediately(app, client, super_admin):
    make_user(app, "Editor9", "EditorPass123", role="content_editor")
    editor_headers = auth_headers(client, "Editor9", "EditorPass123")
    assert client.get("/api/auth/me", headers=editor_headers).status_code == 200
    users = client.get("/api/admin/users", headers=super_admin).get_json()
    editor = next(u for u in users if u["username"] == "Editor9")
    resp = client.put(
        f"/api/admin/users/{editor['id']}", headers=super_admin, json={"active": False}
    )
    assert resp.status_code == 200
    # The editor's token is dead immediately.
    assert client.get("/api/auth/me", headers=editor_headers).status_code == 401
    # And they cannot log in again.
    assert login(client, "Editor9", "EditorPass123").status_code == 401


def test_reset_password_revokes_sessions_and_forces_change(app, client, super_admin):
    make_user(app, "Editor9", "EditorPass123", role="content_editor")
    editor_headers = auth_headers(client, "Editor9", "EditorPass123")
    users = client.get("/api/admin/users", headers=super_admin).get_json()
    editor = next(u for u in users if u["username"] == "Editor9")
    resp = client.post(
        f"/api/admin/users/{editor['id']}/reset-password",
        headers=super_admin,
        json={"temporaryPassword": "TempReset123"},
    )
    assert resp.status_code == 200
    assert client.get("/api/auth/me", headers=editor_headers).status_code == 401
    resp = login(client, "Editor9", "TempReset123")
    assert resp.status_code == 200
    assert resp.get_json()["user"]["mustChangePassword"] is True


def test_settings_admin_only_write(app, client, editor_user, admin_user):
    # Content editor may read settings but not write them.
    assert client.get("/api/admin/settings", headers=editor_user).status_code == 200
    resp = client.put(
        "/api/admin/settings", headers=editor_user, json={"siteTitle": "X"}
    )
    assert resp.status_code == 403
    resp = client.put(
        "/api/admin/settings", headers=admin_user, json={"siteTitle": "New Title"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["siteTitle"] == "New Title"


def test_activity_log_records_admin_writes(app, client, editor_user):
    client.post(
        "/api/admin/posts",
        headers=editor_user,
        json={"title": "Logged Post", "type": "post", "status": "draft"},
    )
    data = client.get("/api/admin/activity", headers=editor_user).get_json()
    actions = [a["action"] for a in data["activity"]]
    assert "create-post" in actions
    entry = next(a for a in data["activity"] if a["action"] == "create-post")
    assert entry["actor"] == "Editor1"
    assert entry["target"] == "logged-post"


def test_dashboard_counts(app, client, editor_user, super_admin):
    client.post(
        "/api/admin/posts",
        headers=editor_user,
        json={"title": "One", "type": "post", "status": "published"},
    )
    client.post(
        "/api/admin/posts",
        headers=editor_user,
        json={"title": "Two", "type": "blog", "status": "draft"},
    )
    data = client.get("/api/admin/dashboard", headers=super_admin).get_json()
    assert data["counts"]["posts"] == 2
    assert data["counts"]["published"] == 1
    assert data["counts"]["drafts"] == 1
    assert data["counts"]["users"] >= 2


def test_category_delete_unsets_posts(app, client, editor_user):
    cat = client.post(
        "/api/admin/categories", headers=editor_user, json={"name": "News"}
    ).get_json()
    post = client.post(
        "/api/admin/posts",
        headers=editor_user,
        json={"title": "Cat Post", "type": "post", "status": "draft",
              "categoryId": cat["id"]},
    ).get_json()
    assert post["categoryId"] == cat["id"]
    assert client.delete(f"/api/admin/categories/{cat['id']}", headers=editor_user).status_code == 200
    fetched = client.get(f"/api/admin/posts/{post['id']}", headers=editor_user).get_json()
    assert fetched["categoryId"] is None
    assert fetched["title"] == "Cat Post"


def test_tag_rename_merges(app, client, editor_user):
    client.post(
        "/api/admin/posts", headers=editor_user,
        json={"title": "A", "type": "post", "status": "published", "tags": ["old"]},
    )
    client.post(
        "/api/admin/posts", headers=editor_user,
        json={"title": "B", "type": "post", "status": "published", "tags": ["new"]},
    )
    resp = client.post(
        "/api/admin/tags/rename",
        headers=editor_user,
        json={"oldName": "old", "newName": "new"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "new"
    tags = client.get("/api/admin/tags", headers=editor_user).get_json()
    names = [t["name"] for t in tags]
    assert "old" not in names
    assert names.count("new") == 1
    # Both posts now carry the merged tag.
    assert client.get("/api/posts?tag=new").get_json()["total"] == 2
