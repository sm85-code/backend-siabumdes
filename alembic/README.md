# Alembic vs `ensure_schema()` (SIABUMDES)

Short clarity note so future schema work does not assume App Platform runs
migrations the way a classic Heroku release-phase job would.

## Two paths, one metadata

| Path | When it runs | What it does |
|------|--------------|--------------|
| `modules/siabumdes/schema.py` → `ensure_schema()` | App boot (`main.py` lifespan) | `Base.metadata.create_all` for missing tables; then idempotent `_WIDEN` / `_ADD_COLUMNS` (and a one-shot UU05/UU06 `business_type` backfill) |
| `alembic/` revisions | Manual / ops (`alembic upgrade head`) | Versioned DDL; **not** wired into App Platform deploy today |

SQLAlchemy models under `modules/siabumdes/**/infrastructure/models.py` feed
**both** paths: Alembic `env.py` imports the same model packages so
`target_metadata = Base.metadata` stays complete for autogenerate.

## Production reality

- Neon / App Platform boots an empty DB the first time. There is **no** one-off
  alembic job in the Procfile/deploy pipeline, so `ensure_schema()` is the
  live safety net that creates tables and adds columns that `create_all`
  cannot alter on existing tables.
- Alembic revisions remain the **source of truth for intentional history**
  (reviewable diffs, local/staging upgrades, future CI). Keep new columns in
  **both** a revision **and** `_ADD_COLUMNS` / `_WIDEN` when prod still relies
  on boot-time ensure — otherwise a fresh Neon gets the column via ensure, but
  an alembic-only environment would drift the other way.
- Drift risk is acknowledged: some historical columns exist only because
  `ensure_schema` patched them. Closing that gap is a dedicated migration
  hygiene PR, not a drive-by Actions YAML change.

## Postgres in free CI

- Free CI leaves `DATABASE_URL` unset; PG integration tests **skip**
  (`tests/test_postgresql_integration.py`, concurrent stock tests, etc.).
- Unit tests that only compile SQLAlchemy dialect SQL or mock sessions still
  run (see `tests/test_b1_stock_atomicity.py`, `tests/test_ensure_schema_lists.py`).
- Adding a `services: postgres` job needs a token/App with **`workflow` scope**
  to edit `.github/workflows/ci.yml`. Current OAuth scopes (`gist, read:org, repo`)
  cannot; Contents API returns 404 on workflow paths; `doctl` not available here.
- Local stand-in: root `docker-compose.pg-ci.yml` (Postgres 16 on port 55432).
  Until a human/PAT with `workflow` lands the Actions job, use that compose file
  + `DATABASE_URL` for integration smokes.

## Checklist for a new SIABUMDES column

1. Add the column on the SQLAlchemy model.
2. Add an Alembic revision under `alembic/versions/`.
3. If the table already exists in prod, also append to `_ADD_COLUMNS` (or
   `_WIDEN`) in `modules/siabumdes/schema.py` so boot stays safe without an
   alembic job.
4. Prefer a small unit test that asserts the new `(table, column, …)` tuple is
   present in those lists (no live DB required).
