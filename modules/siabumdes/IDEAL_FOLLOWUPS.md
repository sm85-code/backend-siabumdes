# SIABUMDES ideal follow-ups (server)

Short tracking notes for residual ideal work after B0–B4. Not a migration guide.

## Done (recent)

| Item | Notes |
|------|--------|
| B0 security | GDrive OAuth hardening |
| B1 | UU05 stock atomicity + JWT tenant aud/iss |
| B2 | Dynamic COA import groups + clone-on-create |
| B3 | `GET /transactions` limit/offset + `meta=true` envelope |
| B4 | Pusat sentinel: `unit_usaha_id=` / `__null__` → `IS NULL` |
| Money JSON | Tx/report JSON money fields as Decimal→str; export/internal float untouched |

## Deferred / next

### Money JSON (`amount` as string) — DONE (this PR)

- `_tx_out` emits `"amount": money_str(...)` (Decimal→`"1500.50"`).
- Report/dashboard/public/ledger/period-close JSON responses run through `stringify_money_fields` (allowlisted rupiah keys).
- Internal `ReportingService` math + PDF/Excel/Word export still use float/Decimal (unchanged).
- Write path: Pydantic `Decimal` still accepts JSON number or string; FE continues to POST numbers.
- Prerequisite: `frontend-siabumdes-ts` `parseMoney` / `fmtRp` accept `number | string` (FE PR money dual-accept).
- No SIABUMDES mobile client found; old JS FE not serving production.

### Schema / Alembic clarity — DONE (this PR)

- `alembic/README.md` documents dual path: boot `ensure_schema()` vs manual Alembic.
- `tests/test_ensure_schema_lists.py` hardens `_WIDEN` / `_ADD_COLUMNS` uniqueness +
  full share_* / identity coverage, identifier + SQL-template shape, `_safe_exec` contract
  (no live Postgres required).

### Postgres in CI — unblocked when this PR merges

- Sibling job `integration-pg` in `.github/workflows/ci.yml`: `postgres:16-alpine` service (user/password/db `siabumdes`/`siabumdes`/`siabumdes_test`), healthcheck, Python 3.11, then `DATABASE_URL=postgresql://siabumdes:siabumdes@localhost:5432/siabumdes_test` + `python -m pytest tests/test_postgresql_integration.py tests/test_b1_stock_atomicity.py -n 0`.
- Existing `lint-and-test` job unchanged (`DATABASE_URL` unset so PG tests skip there).
- `psycopg` + URL rewrite → `asyncpg` already landed (#152).
- Local alternative still available: `docker-compose.pg-ci.yml` (Postgres 16 on `127.0.0.1:55432`).
- Once this PR merges, Postgres CI runs on every push/PR alongside `lint-and-test`.

### Other

- Inventory multi-unit (beyond UU05-centric paths)
- UU05 list pagination with dual-mode `meta` (same B3 pattern)
