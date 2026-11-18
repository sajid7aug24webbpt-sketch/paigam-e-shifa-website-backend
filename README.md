# Paigam-e-Shifa — Website + Admin Backend

Standalone Flask backend for the Paigam-e-Shifa (Ruhani + Hakeem Clinic)
website + admin panel. A faithful rebuild of the live Muse fullstack
artifact `paigam-e-shifa-website-admin` (v71) as portable code — the
artifact's own code cannot be exported, so this is rebuilt from spec.

**No store features.** The Dawakhana store was removed from the original
app; there are no medicines, carts, orders or customers here — posts/blogs,
categories, tags, media, users, settings and activity only.

## Stack

- Python 3.12, Flask 3, SQLAlchemy 2, PyMySQL, PyJWT, Flask-CORS, gunicorn
- **MySQL 8** is the deploy target. SQLite is supported **only** for local
  dev / tests — never for production.
- Passwords: `hashlib.scrypt` with a per-user salt. JWT: PyJWT HS256,
  30-day expiry, `jti` stored in the `sessions` table so logout / revoke
  works server-side.

## Setup

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in real values
```

### Environment

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | e.g. `mysql+pymysql://user:pass@host:3306/paigam_shifa` (prod) or `sqlite:///paigam_shifa_dev.db` (local dev only) |
| `SECRET_KEY` | Flask secret (long random value) |
| `JWT_SECRET` | JWT signing secret (different long random value) |
| `FRONTEND_ORIGIN` | Allowed CORS origin, default `http://localhost:3000` |
| `UPLOAD_DIR` | Upload storage dir, default `uploads` (relative to backend/) |
| `MAX_AVATAR_MB` | Avatar size cap, default `2` |
| `MAX_POST_IMAGE_MB` | Post-image size cap, default `6` |
| `PORT` | Port, default `5000` |

## Migrations

`migrations/0001_initial.sql` creates every table (MySQL 8 dialect).

```bash
DATABASE_URL=mysql+pymysql://user:pass@host:3306/paigam_shifa \
  python scripts/migrate.py
```

Applies `*.sql` in order, idempotently, tracked in `schema_migrations`.
`migrate.py` refuses SQLite — for SQLite dev/tests the app creates tables
itself (`db.create_all()` at startup / in `scripts/seed.py`).

## Seed

```bash
python scripts/seed.py
```

- Creates tables if missing.
- Seeds the 1 real category (`"soical media"` — the typo is SAJID's own
  data, kept exactly) and the 5 real posts from `seed/seed-data.json`.
  The 2 excluded test rows (id 4 `"post"`, id 5 `"qwe"`) are **never** seeded.
- Copies the two seed images into `uploads/post-images/` under CSPRNG keys
  (post 1 ← `~/workspace/user/media_library/image/image-8.png`,
  post 6 ← `.../WhatsApp_Image_2026-11-14_at_10.11.08_AM.jpeg`);
  if a source file is missing the post is seeded with `image_url: null`
  and a warning is printed. Post 7's image could not be retrieved from
  artifact storage — it is seeded with no image; re-upload it in Admin.
- Creates a **fresh** admin account: username `Sajid`, role `super_admin`,
  a **random temporary password printed ONCE to stdout**,
  `must_change_password=true`. There is **no default password** anywhere
  in this codebase (no `admin` / `admin123` bootstrap). Re-running the seed
  is safe: existing posts/category/admin are skipped
  (`--force-admin` replaces the admin with a new random password).

## Run

```bash
# dev server
python run.py
# production
gunicorn -c gunicorn.conf.py run:app
```

## Docker

Docker is not installed in the build VM, so `docker compose up` could **not**
be verified here — the files were written carefully but are unverified
until run on a host with Docker + MySQL.

```bash
docker compose up --build -d
docker compose exec backend python scripts/migrate.py
docker compose exec backend python scripts/seed.py   # prints the admin password ONCE
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

Tests use SQLite in-memory. The MySQL smoke test
(`tests/test_mysql_smoke.py`) runs only when `MYSQL_SMOKE_URL`/`DATABASE_URL`
points at a reachable MySQL 8 server; it skips otherwise (no MySQL server
exists in the build VM).

## Scheduling

A post with `status=scheduled` and a future `publishAt` is invisible on the
public site until due. Due posts are flipped to `published` in three ways:

1. **Lazily** — on every public posts request (`GET /api/posts`,
   `GET /api/posts/:slug`).
2. **At app startup** — `create_app()` flips any already-due posts.
3. **Explicitly** — `POST /api/admin/publish-due` (content_editor+), meant
   for a cron/scheduler, e.g. every 5 minutes:
   `curl -X POST -H "Authorization: Bearer $TOKEN" $API/admin/publish-due`.

## API (base `/api`, all JSON, errors `{"error": "..."}`)

Public: `GET /health`, `GET /posts` (filters `status` is not public —
public list is published-only; `type`, `category`, `tag`, `search`, `page`,
`per_page`), `GET /posts/:slug`, `GET /categories`, `GET /tags`.

Auth: `POST /auth/login`, `GET /auth/me`, `POST /auth/logout`,
`POST /auth/change-password`, `POST /auth/force-change-password`.

Admin (Bearer on every route; server-side role checks on every route):
dashboard, activity, posts CRUD + `/status`, `/trash`, `/restore`,
permanent delete (trashed only), trash list, categories, tags
(`/rename` merges when the target exists), media (multipart `image`),
users (Super-Admin-only list/create/delete/reset-password), settings,
avatar (self), change-username (admin+), publish-due.

## Roles

`super_admin` > `admin` > `content_editor` > `viewer`.
Viewer is read-only. Content editor manages posts/categories/tags/media.
Admin adds user management (except Super-Admin-only ops) + settings +
change-username. Nobody may delete / deactivate / demote themselves or the
last active Super Admin; deactivation ends that user's sessions immediately.
Every admin write is logged to `activity_log`.

## Security notes

- Login is case-insensitive; failures return one generic message for unknown
  users and wrong passwords, and the unknown-user path runs a dummy scrypt
  hash first (anti-enumeration). After 5 failures for a (username, IP) the
  login is locked for 15 minutes (429).
- Username rule `^[A-Za-z0-9._-]{3,30}$`; passwords minimum 8 characters.
- Uploads: magic-byte check (JPEG/PNG/WebP only), avatar ≤ 2 MB, post image
  ≤ 6 MB, keys `post-images/<CSPRNG token>.<ext>` / `avatars/<token>.<ext>`,
  served at `/uploads/<key>` with strict key validation (no traversal).
- `linkUrl`/`imageUrl` must be http(s) (relative `/uploads/...` paths are
  allowed for `imageUrl`); `javascript:`/`data:` are rejected.
- Security headers on all responses: `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `X-Frame-Options: SAMEORIGIN`.
- All datetimes stored in the DB are naive UTC (MySQL `DATETIME` and SQLite
  both drop tzinfo); see `app/models.py::utcnow`.
