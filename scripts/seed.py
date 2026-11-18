"""Seed the database from backend/seed/seed-data.json.

- Creates the tables if missing (db.create_all — used for SQLite dev/tests
  and first MySQL bootstrap; migrations/0001_initial.sql is authoritative
  for production schema changes).
- Seeds the 1 real category ("soical media" — the typo is SAJID's own data,
  keep it exactly) and the 5 REAL posts. The 2 excluded test rows documented
  in the JSON (id 4 "post", id 5 "qwe") are NEVER seeded.
- Seed images: post 1 -> ~/workspace/user/media_library/image/image-8.png,
  post 6 -> ~/workspace/user/media_library/image/WhatsApp_Image_2026-11-14_at_10.11.08_AM.jpeg.
  The script also checks frontend posters/ (posters/main-poster.png,
  posters/mujarab-amal-nazar.jpg). If neither source exists, the post is
  seeded with image_url null and a warning is logged.
  Copied images are stored under uploads/post-images/ with CSPRNG keys and
  a matching media row is created.
- Creates a FRESH admin account: username "Sajid", role super_admin, a
  RANDOM temporary password printed ONCE to stdout, must_change_password=true.
  There is NO default/bootstrap password anywhere in this codebase.

Usage:
    python scripts/seed.py            # uses DATABASE_URL from env or .env
    python scripts/seed.py --force-admin   # re-create the admin if it exists
                                            # (prints a NEW random password)

The temporary password is printed once and never written to any file.
"""
import argparse
import json
import os
import secrets
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from app import create_app  # noqa: E402
from app.models import Category, Media, Post, Tag, User, db  # noqa: E402
from app.security import detect_image, hash_password, new_upload_key  # noqa: E402
from app.utils import slugify  # noqa: E402

# imageLocal value in seed-data.json -> candidate source paths (first hit wins).
IMAGE_SOURCES = {
    "posters/main-poster.png": [
        os.path.expanduser("~/workspace/user/media_library/image/image-8.png"),
        os.path.join(BACKEND_DIR, "..", "frontend", "posters", "main-poster.png"),
        os.path.join(BACKEND_DIR, "seed", "posters", "main-poster.png"),
    ],
    "posters/mujarab-amal-nazar.jpg": [
        os.path.expanduser(
            "~/workspace/user/media_library/image/WhatsApp_Image_2026-11-14_at_10.11.08_AM.jpeg"
        ),
        os.path.join(BACKEND_DIR, "..", "frontend", "posters", "mujarab-amal-nazar.jpg"),
        os.path.join(BACKEND_DIR, "seed", "posters", "mujarab-amal-nazar.jpg"),
    ],
}


def find_image_source(image_local):
    for candidate in IMAGE_SOURCES.get(image_local, []):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def store_seed_image(source_path, upload_dir):
    """Copy a seed image into uploads/post-images/ with a CSPRNG key.
    Returns (key, url, mime, size) or None when the file is not a valid image."""
    with open(source_path, "rb") as fh:
        data = fh.read()
    detected = detect_image(data)
    if detected is None:
        print(f"  WARNING: {source_path} failed magic-byte check; skipping image.")
        return None
    ext, mime = detected
    key = new_upload_key("post-images", ext)
    dest = os.path.join(upload_dir, key)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(data)
    return key, f"/uploads/{key}", mime, len(data)


def seed_data(app):
    seed_path = os.path.join(BACKEND_DIR, "seed", "seed-data.json")
    with open(seed_path, encoding="utf-8") as fh:
        payload = json.load(fh)

    upload_dir = app.config["UPLOAD_DIR"]
    excluded_ids = {row["id"] for row in payload.get("excluded_test_rows", [])}

    # --- Categories -------------------------------------------------------
    cat_by_legacy_id = {}
    for cat in payload.get("categories", []):
        existing = Category.query.filter_by(name=cat["name"]).first()
        if existing:
            print(f"Category exists: {existing.name!r} (id {existing.id})")
            cat_by_legacy_id[cat["id"]] = existing
        else:
            created = Category(name=cat["name"])
            db.session.add(created)
            db.session.flush()
            cat_by_legacy_id[cat["id"]] = created
            print(f"Category created: {created.name!r} (id {created.id})")
    db.session.commit()

    # --- Posts ------------------------------------------------------------
    seeded = 0
    for item in payload.get("posts", []):
        if item.get("legacy_id") in excluded_ids:
            print(f"SKIP excluded test row legacy_id={item.get('legacy_id')} title={item.get('title')!r}")
            continue
        slug = item.get("slug") or slugify(item["title"])
        if Post.query.filter_by(slug=slug).first():
            print(f"Post exists: {slug!r}; skipping.")
            continue
        post = Post(
            slug=slug,
            title=item["title"],
            type=item.get("type", "post"),
            text_en=item.get("textEn") or "",
            text_ur=item.get("textUr") or "",
            link_url=item.get("linkUrl") or None,
            status=item.get("status", "published"),
        )
        if item.get("categoryId") is not None:
            cat = cat_by_legacy_id.get(item["categoryId"])
            if cat is not None:
                post.category_id = cat.id
        if item.get("postDate"):
            post.post_date = date.fromisoformat(item["postDate"])
        if item.get("publishAt"):
            dt = datetime.fromisoformat(item["publishAt"])
            post.publish_at = dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
        if item.get("imageUrl"):
            post.image_url = item["imageUrl"]
        elif item.get("imageLocal"):
            source = find_image_source(item["imageLocal"])
            if source:
                stored = store_seed_image(source, upload_dir)
                if stored:
                    key, url, mime, size = stored
                    post.image_key = key
                    post.image_url = url
                    db.session.add(
                        Media(
                            key=key,
                            url=url,
                            filename=os.path.basename(source),
                            size=size,
                            mime=mime,
                        )
                    )
                    print(f"  Image for {slug!r}: copied {source} -> {key}")
            else:
                print(
                    f"  WARNING: image source not found for {slug!r} "
                    f"({item['imageLocal']}); seeding with image_url=null."
                )
        for tag_name in item.get("tags") or []:
            tag = Tag.query.filter_by(name=tag_name).first() or Tag(name=tag_name)
            db.session.add(tag)
            post.tags.append(tag)
        db.session.add(post)
        db.session.commit()
        seeded += 1
        print(f"Post seeded: {slug!r} ({post.type}, {post.status})")
    print(f"Seeded {seeded} new post(s).")


def seed_admin(force=False):
    existing = User.query.filter_by(username_lower="sajid").first()
    if existing and not force:
        print("Admin user 'Sajid' already exists; not creating a new one.")
        print("  (Use --force-admin to replace it with a fresh random password.)")
        return None
    if existing and force:
        db.session.delete(existing)
        db.session.commit()
    temp_password = secrets.token_urlsafe(16)  # ~21 chars, random, printed once
    salt, pw_hash = hash_password(temp_password)
    admin = User(
        username="Sajid",
        username_lower="sajid",
        password_hash=pw_hash,
        salt=salt,
        role="super_admin",
        active=True,
        must_change_password=True,
    )
    db.session.add(admin)
    db.session.commit()
    print()
    print("=" * 64)
    print("FRESH ADMIN ACCOUNT CREATED (print this once, then keep it safe):")
    print("  username: Sajid")
    print(f"  temporary password: {temp_password}")
    print("  role: super_admin — must change the password on first login.")
    print("  This password is NOT stored anywhere in the code or in a file.")
    print("=" * 64)
    return temp_password


def main():
    parser = argparse.ArgumentParser(description="Seed the Paigam-e-Shifa database.")
    parser.add_argument("--force-admin", action="store_true",
                        help="Replace an existing 'Sajid' admin with a fresh random password.")
    parser.add_argument("--skip-admin", action="store_true",
                        help="Seed content only; do not create the admin account.")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        db.create_all()
        print(f"Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
        seed_data(app)
        if not args.skip_admin:
            seed_admin(force=args.force_admin)
    print("Seed complete.")


if __name__ == "__main__":
    main()
