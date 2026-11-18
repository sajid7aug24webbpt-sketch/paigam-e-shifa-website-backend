"""Authentication / authorisation decorators.

Role hierarchy (highest first): super_admin > admin > content_editor > viewer.

Permissions (server-side, enforced on every admin route — never trust the client):
- viewer: read-only (GET admin endpoints only).
- content_editor: posts/blogs/categories/tags/media CRUD + publish/trash/restore.
- admin: content_editor + users management EXCEPT Super-Admin-only ops
  (list/create/delete users, reset-password) + settings + change-username.
- super_admin: everything.

Super-Admin-only ops: GET/POST/DELETE /api/admin/users,
POST /api/admin/users/:id/reset-password, promoting a user to super_admin.
"""
from functools import wraps

from flask import current_app, g, jsonify, request

from .models import Session, User, db
from .security import decode_token

ROLE_RANK = {"viewer": 0, "content_editor": 1, "admin": 2, "super_admin": 3}

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _bearer_token():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        if not token:
            return jsonify({"error": "Authentication required."}), 401
        try:
            payload = decode_token(token, current_app.config["JWT_SECRET"])
        except Exception:
            return jsonify({"error": "Invalid or expired token."}), 401
        session = Session.query.filter_by(jti=payload.get("jti")).first()
        if session is None or session.revoked:
            return jsonify({"error": "Session revoked. Please sign in again."}), 401
        user = db.session.get(User, int(payload["sub"]))
        if user is None or not user.active:
            return jsonify({"error": "Account is inactive."}), 401
        g.current_user = user
        g.current_session = session
        return f(*args, **kwargs)

    return wrapper


def require_roles(*roles):
    """Require the current user to hold one of the given roles."""

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if g.current_user.role not in roles:
                return jsonify({"error": "Forbidden: insufficient permissions."}), 403
            return f(*args, **kwargs)

        return wrapper

    return decorator


def require_min_role(role):
    """Require a role at least as high as the given role in the hierarchy."""

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if ROLE_RANK.get(g.current_user.role, -1) < ROLE_RANK[role]:
                return jsonify({"error": "Forbidden: insufficient permissions."}), 403
            return f(*args, **kwargs)

        return wrapper

    return decorator


def require_write():
    """Viewer is read-only: block all non-GET admin calls for viewers."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if request.method in WRITE_METHODS and g.current_user.role == "viewer":
                return jsonify({"error": "Forbidden: viewers are read-only."}), 403
            return f(*args, **kwargs)

        return wrapper

    return decorator


def revoke_user_sessions(user_id, except_jti=None):
    """Revoke all sessions of a user (optionally keeping one jti alive)."""
    q = Session.query.filter_by(user_id=user_id, revoked=False)
    if except_jti:
        q = q.filter(Session.jti != except_jti)
    for s in q.all():
        s.revoked = True
    db.session.commit()
