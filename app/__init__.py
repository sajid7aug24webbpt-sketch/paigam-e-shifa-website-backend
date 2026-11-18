"""Flask application factory."""
import os
import re

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from .config import Config
from .models import db
from .security import is_valid_upload_key
from .utils import publish_due_posts


def create_app(config_obj=None, config_overrides=None):
    app = Flask(__name__)
    if config_obj is not None:
        app.config.from_object(config_obj)
    else:
        app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    # SQLite in-memory (tests) needs a single shared connection; the default
    # pool would otherwise hand each request a fresh empty database.
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        from sqlalchemy.pool import StaticPool

        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        }

    db.init_app(app)
    CORS(
        app,
        resources={r"/api/*": {"origins": app.config["FRONTEND_ORIGIN"]}},
        supports_credentials=False,
    )

    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    os.makedirs(os.path.join(app.config["UPLOAD_DIR"], "post-images"), exist_ok=True)
    os.makedirs(os.path.join(app.config["UPLOAD_DIR"], "avatars"), exist_ok=True)

    from .blueprints.admin import admin_bp
    from .blueprints.auth_bp import auth_bp
    from .blueprints.media import media_bp
    from .blueprints.public import public_bp
    from .blueprints.settings import settings_bp
    from .blueprints.users import users_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(settings_bp)

    @app.route("/uploads/<path:key>")
    def serve_upload(key):
        # Strict key validation: rejects path traversal and anything outside
        # the two allowed namespaces.
        if not is_valid_upload_key(key):
            return jsonify({"error": "Not found."}), 404
        namespace, filename = key.split("/", 1)
        directory = os.path.join(app.config["UPLOAD_DIR"], namespace)
        return send_from_directory(directory, filename)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "Bad request."}), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found."}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method not allowed."}), 405

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "Uploaded file is too large."}), 413

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "Internal server error."}), 500

    # Flip any scheduled posts that are already due, at startup.
    with app.app_context():
        try:
            db.create_all()
            publish_due_posts()
        except Exception:
            # Startup must not fail if the DB is unreachable yet (e.g. the
            # container starts before MySQL); requests will surface the error.
            app.logger.warning("Startup publish-due skipped (DB not ready).")

    return app
