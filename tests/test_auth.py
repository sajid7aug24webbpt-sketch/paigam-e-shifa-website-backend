"""Auth tests: login success/fail/generic error, lockout, sessions."""
from datetime import datetime, timedelta, timezone

from app.models import LoginAttempt, Session, User, db
from tests.conftest import auth_headers, login, make_user


def test_login_success_returns_token_and_user(app, client):
    make_user(app, "Sajid", "SuperSecret123", role="super_admin")
    resp = login(client, "Sajid", "SuperSecret123")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["token"]
    assert data["user"]["username"] == "Sajid"
    assert data["user"]["role"] == "super_admin"
    assert data["user"]["mustChangePassword"] is False


def test_login_case_insensitive_username(app, client):
    make_user(app, "Sajid", "SuperSecret123", role="super_admin")
    resp = login(client, "SAJID", "SuperSecret123")
    assert resp.status_code == 200
    assert resp.get_json()["user"]["username"] == "Sajid"


def test_login_wrong_password_generic_error(app, client):
    make_user(app, "Sajid", "SuperSecret123", role="super_admin")
    resp = login(client, "Sajid", "WrongPassword1")
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "Invalid username or password"}


def test_login_unknown_user_same_generic_error(app, client):
    # Unknown user must get the IDENTICAL message as a wrong password
    # (anti-enumeration); the dummy scrypt path runs first.
    resp = login(client, "NobodyHere", "Whatever123")
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "Invalid username or password"}


def test_login_unknown_user_does_not_create_attempt_row_leak(app, client):
    # The failure is recorded, but no user row is created and the message
    # reveals nothing about existence.
    login(client, "GhostUser", "Whatever123")
    with app.app_context():
        assert User.query.filter_by(username_lower="ghostuser").first() is None
        assert LoginAttempt.query.filter_by(username_lower="ghostuser").count() == 1


def test_lockout_after_five_failures(app, client):
    make_user(app, "Sajid", "SuperSecret123", role="super_admin")
    for i in range(4):
        resp = login(client, "Sajid", "WrongPassword1")
        assert resp.status_code == 401, f"attempt {i+1}"
    resp = login(client, "Sajid", "WrongPassword1")
    assert resp.status_code == 429
    assert resp.get_json() == {"error": "Too many login attempts. Try again in 15 minutes."}
    # Still locked even with the right password.
    resp = login(client, "Sajid", "SuperSecret123")
    assert resp.status_code == 429


def test_lockout_expires_after_15_minutes(app, client):
    make_user(app, "Sajid", "SuperSecret123", role="super_admin")
    for _ in range(5):
        login(client, "Sajid", "WrongPassword1")
    with app.app_context():
        attempt = LoginAttempt.query.filter_by(username_lower="sajid").first()
        assert attempt.locked_until is not None
        from app.models import utcnow

        attempt.locked_until = utcnow() - timedelta(seconds=1)
        db.session.commit()
    resp = login(client, "Sajid", "SuperSecret123")
    assert resp.status_code == 200


def test_successful_login_clears_failure_counter(app, client):
    make_user(app, "Sajid", "SuperSecret123", role="super_admin")
    login(client, "Sajid", "WrongPassword1")
    login(client, "Sajid", "WrongPassword1")
    assert login(client, "Sajid", "SuperSecret123").status_code == 200
    with app.app_context():
        assert LoginAttempt.query.filter_by(username_lower="sajid").count() == 0


def test_logout_revokes_token(app, client, super_admin):
    resp = client.post("/api/auth/logout", headers=super_admin)
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    # Same token is now rejected.
    resp = client.get("/api/auth/me", headers=super_admin)
    assert resp.status_code == 401


def test_me_without_token_is_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_change_password_revokes_other_sessions(app, client):
    make_user(app, "Sajid", "SuperSecret123", role="super_admin")
    h1 = auth_headers(client, "Sajid", "SuperSecret123")
    h2 = auth_headers(client, "Sajid", "SuperSecret123")
    resp = client.post(
        "/api/auth/change-password",
        headers=h1,
        json={"currentPassword": "SuperSecret123", "newPassword": "BrandNewPass123"},
    )
    assert resp.status_code == 200
    # The other session is revoked; the session that changed the password lives.
    assert client.get("/api/auth/me", headers=h2).status_code == 401
    assert client.get("/api/auth/me", headers=h1).status_code == 200
    # New password works.
    assert login(client, "Sajid", "BrandNewPass123").status_code == 200


def test_change_password_wrong_current_is_400(app, client, super_admin):
    resp = client.post(
        "/api/auth/change-password",
        headers=super_admin,
        json={"currentPassword": "NopeNope123", "newPassword": "BrandNewPass123"},
    )
    assert resp.status_code == 400


def test_change_password_too_short_is_400(app, client, super_admin):
    resp = client.post(
        "/api/auth/change-password",
        headers=super_admin,
        json={"currentPassword": "SuperSecret123", "newPassword": "short"},
    )
    assert resp.status_code == 400


def test_force_change_password_flow(app, client):
    make_user(app, "NewStaff", "TempPass123", role="content_editor", must_change=True)
    headers = auth_headers(client, "NewStaff", "TempPass123")
    resp = client.post(
        "/api/auth/force-change-password",
        headers=headers,
        json={"newPassword": "MyOwnPass123"},
    )
    assert resp.status_code == 200
    with app.app_context():
        user = User.query.filter_by(username_lower="newstaff").first()
        assert user.must_change_password is False
    # Second force-change is rejected: the flag is cleared.
    resp = client.post(
        "/api/auth/force-change-password",
        headers=headers,
        json={"newPassword": "AnotherPass123"},
    )
    assert resp.status_code == 400
