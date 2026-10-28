# Assumptions

1. **Single currency**, no currency field. Amounts are decimal numbers with up to 2 decimal places,
   stored as integer minor units to avoid floating-point errors.
2. **IDs are server-generated UUID v4** for accounts and transactions. A malformed id returns 400;
   a well-formed but unknown id returns 404.
3. **`type` is lowercase** `credit` / `debit` (exact match). `amount` must be a JSON number (strings and booleans are rejected).
4. **Duplicate `reference_id`** returns **409 Conflict** (`DUPLICATE_TRANSACTION`) with the original
   transaction in the body and changes nothing. A 409 rather than a 200 is used so a client can tell
   "already existed" from "just created"; the body still carries the original record, so retries are safe.
   The same 409 is returned even if the retry's payload (type, amount, description) differs from the
   original; the body's `payload_matches` flag (`true`/`false`) lets a client tell a faithful retry from a
   reused reference. Nothing is created or changed in either case.
   A *rejected* (e.g. insufficient-balance) transaction is not stored, so its `reference_id` can be
   reused for a retry. The trade-off is that failed attempts leave no audit row; a production system might
   log them separately.
5. **`description` is optional** (defaults to empty); `reference_id` is required, ≤ 100 chars.
6. **Client-sent `balance` on account creation is rejected (400)** rather than silently ignored, so the
   misuse is visible. Unknown fields are rejected on all POST bodies for the same reason. The one
   exception is `account_id` on transactions: the brief lists it as a transaction field, so it is accepted
   when it matches the URL (and is required on the `POST /transactions` alias).
7. **Account names need not be unique**; only `id` is unique. Names are trimmed and limited to 100 characters.
8. **History pagination:** `limit` default 10, max 100; `page` max 1,000,000 (larger values are a 400);
   a page within range but past the end returns an empty list.
   Ordering is `created_at DESC` with insertion order as the tie-breaker.
9. **Transfers** require a `reference_id`, cannot have the same source and destination, and create one
   debit and one credit ledger entry. The ledger entries' own references are `xfer:<transfer_id>:debit` /
   `xfer:<transfer_id>:credit`; the `xfer:` prefix is reserved and rejected (400) on ordinary transactions,
   so legs can never collide with anything a client sends. A transfer `reference_id` is unique per source
   account; re-using it (with any payload) returns 409 `DUPLICATE_TRANSACTION` with `payload_matches`.
10. **No authentication/authorization**; out of scope for this assignment.
11. **SQLite** is used for easy local setup. It serializes writers (`BEGIN IMMEDIATE`), which makes
    concurrency correctness simple. On PostgreSQL/MySQL the same design would use `SELECT ... FOR UPDATE`
    (locking accounts in a consistent order for transfers) or the same conditional `UPDATE`.
12. Accounts cannot be deleted or renamed (not requested).
13. The brief numbers sections 1–5 then 7–11 (no section 6); I treated this as a numbering slip and
    implemented every listed section.
14. **`Idempotency-Key` header** is accepted as an alternative spelling of `reference_id` on transactions
    and transfers (same uniqueness rules). If both are sent they must match.
15. **Money in JSON** is returned as a number (e.g. `49.5`) because the brief's examples use numbers.
    Values are exact to 2 decimals; storage and all arithmetic use integer minor units.
16. **Request bodies are limited to 64 KB** (413 beyond that); they are tiny by design.
17. **`reference_id` is case-sensitive** (`ref-1` and `REF-1` are different keys); it is trimmed of surrounding whitespace.