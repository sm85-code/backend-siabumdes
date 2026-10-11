# Riska operational contract

Riska uses deterministic commands and existing services; it never calls an LLM. Select an entity explicitly for reports. Unit managers can only use their own active unit with an allowed `ai_session_permissions` row. Missing permission denies access. The old per-user `users.riska_enabled` column is retained for compatibility but no longer controls access.

Admins manage all current units at `/api/v1/admin/riska-permissions` (GET/PATCH). The frontend page `/riska-permissions` is admin-only. Permission changes are audited and effective on every backend request; open widgets refresh visibility every 30 seconds.

Managers create server-owned drafts through POST `/api/riska/drafts`. Exact command examples (replace identifiers and dates):

- `imbal mitra=ID tanggal=2026-10-11 bulan=10 tahun=2026 nominal=300`
- `masuk produk=SKU qty=2 harga=100 pemasok=ID tanggal=2026-10-11`
- `keluar produk=SKU qty=1 harga=150 customer=ID tanggal=2026-10-11`
- `rusak produk=SKU qty=1 tanggal=2026-10-11`

Nominal uses a decimal point without thousands separators. Quote SKUs with spaces. Stock commands currently use cash settlement; use the existing inventory forms for credit purchases/sales, internal consumption or other adjustments. Yield drafts add a new payment; existing month records must be edited in the existing Imbal Hasil page.

Drafts expire after 10 minutes. POST `/api/riska/drafts/{id}/execute` confirms; DELETE cancels. Ownership, current permission, active unit, period locks, stock, counterparties and scoped active COA are revalidated. Posting and consuming the draft commit together. Replay returns 409. Changed account/amount previews require a new draft. Network ambiguity must be checked in the transaction page; retrying the same draft cannot post twice.

UU04 uses its configured `pendapatan_bagi_hasil_unit4` transaction type (canonical seed cash 1.1.01.14, income 4.1.01.24). Trade uses unit-scoped COA: canonical UU05/UU06 inventory/cash/HPP/revenue suffix 15/16. Damaged stock uses scoped transit 1.1.05.51 and loss 6.2.99.52. Missing or ambiguous accounts fail closed, rather than borrowing another unit's account. New trade units require an unambiguous scoped operational chart. Existing adjustment service defaults are unchanged; Riska supplies the selected unit's accounts.

Queries `mitra`/`imbal` show UU04 capital and current-year monthly receipts (missing is not zero). `stok`/`persediaan` show valuation and counterparties; `stok tipis` filters quantity <=5. These are distinct from consolidated profit or loan principal balances.

Models/migrations add `ai_session_permissions` and `riska_drafts`; startup `ensure_schema()` registers them through existing create_all. No production data is seeded by Riska. The initial workbook may not include loss/transit accounts, and freshly seeded trade units can still default to jasa: admins must configure live unit types and COA. Production Neon access was unavailable during implementation. Verification used isolated local PostgreSQL and browser sessions; no production financial records were changed.
