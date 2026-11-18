"""Pytest fixtures: fresh in-memory SQLite app per test, temp upload dir."""
import pytest

from app import create_app
from app.config import TestingConfig
from app.models import User, db
from app.security import hash_password


@pytest.fixture()
def app(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    application = create_app(
        TestingConfig,
        config_overrides={"UPLOAD_DIR": str(upload_dir)},
    )
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def make_user(app, username, password, role="viewer", active=True, must_change=False):
    with app.app_context():
        salt, pw_hash = hash_password(password)
        user = User(
            username=username,
            username_lower=username.lower(),
            password_hash=pw_hash,
            salt=salt,
            role=role,
            active=active,
            must_change_password=must_change,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def login(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    return resp


def auth_headers(client, username, password):
    resp = login(client, username, password)
    assert resp.status_code == 200, resp.get_json()
    return {"Authorization": f"Bearer {resp.get_json()['token']}"}


@pytest.fixture()
def super_admin(app, client):
    make_user(app, "Sajid", "SuperSecret123", role="super_admin")
    return auth_headers(client, "Sajid", "SuperSecret123")


@pytest.fixture()
def admin_user(app, client):
    make_user(app, "Admin1", "AdminSecret123", role="admin")
    return auth_headers(client, "Admin1", "AdminSecret123")


@pytest.fixture()
def editor_user(app, client):
    make_user(app, "Editor1", "EditorSecret123", role="content_editor")
    return auth_headers(client, "Editor1", "EditorSecret123")


@pytest.fixture()
def viewer_user(app, client):
    make_user(app, "Viewer1", "ViewerSecret123", role="viewer")
    return auth_headers(client, "Viewer1", "ViewerSecret123")
