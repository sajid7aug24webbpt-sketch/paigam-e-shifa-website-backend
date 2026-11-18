"""Upload + URL validation tests."""
import io
import re

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 100
BAD_BYTES = b"this is not an image at all"


def _upload(client, headers, data, filename="test.png"):
    return client.post(
        "/api/admin/media",
        headers=headers,
        data={"image": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def test_upload_png_ok_key_namespace(app, client, editor_user):
    resp = _upload(client, editor_user, PNG_BYTES)
    assert resp.status_code == 201, resp.get_json()
    data = resp.get_json()
    assert re.match(r"^post-images/[A-Za-z0-9_-]+\.png$", data["key"])
    assert data["url"] == f"/uploads/{data['key']}"
    assert data["mime"] == "image/png"
    # The file is actually served back.
    fetched = client.get(data["url"])
    assert fetched.status_code == 200
    assert fetched.data == PNG_BYTES


def test_upload_jpeg_and_webp_ok(app, client, editor_user):
    assert _upload(client, editor_user, JPEG_BYTES, "a.jpg").status_code == 201
    assert _upload(client, editor_user, WEBP_BYTES, "a.webp").status_code == 201


def test_upload_bad_magic_bytes_rejected(app, client, editor_user):
    resp = _upload(client, editor_user, BAD_BYTES)
    assert resp.status_code == 400
    assert "JPEG, PNG" in resp.get_json()["error"]


def test_upload_oversized_rejected(app, client, editor_user):
    big = b"\xff\xd8\xff\xe0" + b"\x00" * (6 * 1024 * 1024 + 1)
    resp = _upload(client, editor_user, big, "big.jpg")
    assert resp.status_code == 400
    assert "6 MB" in resp.get_json()["error"]


def test_upload_viewer_forbidden(client, viewer_user):
    assert _upload(client, viewer_user, PNG_BYTES).status_code == 403


def test_avatar_oversized_rejected(app, client, editor_user):
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 1)
    resp = client.post(
        "/api/admin/avatar",
        headers=editor_user,
        data={"image": (io.BytesIO(big), "avatar.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "2 MB" in resp.get_json()["error"]


def test_avatar_upload_and_delete(app, client, editor_user):
    resp = client.post(
        "/api/admin/avatar",
        headers=editor_user,
        data={"image": (io.BytesIO(PNG_BYTES), "avatar.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert re.match(r"^/uploads/avatars/[A-Za-z0-9_-]+\.png$", resp.get_json()["avatarUrl"])
    resp = client.delete("/api/admin/avatar", headers=editor_user)
    assert resp.status_code == 200


def test_upload_key_traversal_rejected(client):
    assert client.get("/uploads/../seed/seed-data.json").status_code == 404
    assert client.get("/uploads/post-images/%2e%2e%2fsecret").status_code == 404


def test_post_url_validation(app, client, editor_user):
    base = {"title": "URL Test", "type": "post", "status": "draft"}
    for bad in ("javascript:alert(1)", "data:text/html,<h1>x</h1>", "ftp://example.com/x"):
        resp = client.post(
            "/api/admin/posts", headers=editor_user,
            json={**base, "linkUrl": bad},
        )
        assert resp.status_code == 400, bad
    resp = client.post(
        "/api/admin/posts", headers=editor_user,
        json={**base, "linkUrl": "https://example.com/page"},
    )
    assert resp.status_code == 201


def test_security_headers_present(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_media_delete_removes_file(app, client, editor_user):
    media = _upload(client, editor_user, PNG_BYTES).get_json()
    assert client.get(media["url"]).status_code == 200
    assert client.delete(f"/api/admin/media/{media['id']}", headers=editor_user).status_code == 200
    assert client.get(media["url"]).status_code == 404
