# Transaction Ledger API

A small backend API for a transaction ledger: create accounts, post credit/debit
transactions, read balances, browse paginated history, and (bonus) transfer money
between accounts.

**Stack:** Python 3.10+, Flask, SQLite (standard library `sqlite3`). No ORM, so every
SQL statement and constraint is visible and reviewable.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                      # serves http://127.0.0.1:5000
```

Or, with Docker: `docker compose up --build` (data persists in a named volume).

Optional environment variables: `LEDGER_DB` (database file, default `ledger.db`),
`PORT` (default `5000`). The schema is created automatically on first start.

## Dashboard

Open http://127.0.0.1:5000 once the server is running. It is a single static page (no build step,
no extra dependencies) that talks to the same API: create accounts, post credits and debits, make
transfers, and browse the filtered, paginated history. Server errors are shown as they come back
from the API, so it doubles as a quick way to try the validation rules.

## Interactive API docs and Docker

Swagger UI is available at http://127.0.0.1:5000/docs (spec at `/openapi.json`). It is self-hosted
(vendored under `app/static/swagger`), so it works offline. `GET /accounts` lists accounts with pagination. Every response carries an `X-Request-ID` header and each request is logged.

```bash
docker build -t ledger-api .
docker run -p 5000:5000 -v ledger-data:/data ledger-api   # runs as a non-root user, has a healthcheck
```

## Run the tests

```bash
python -m unittest discover -s tests -t . -v
```

55 tests cover validation, the rules in the brief, pagination/filtering/sorting,
immutability, atomic rollback, concurrent debits and concurrent duplicate references,
idempotency (including payload mismatch), transfers, reconciliation and input hardening.

Lint (optional): `pip install -r requirements-dev.txt && ruff check .`. CI (`.github/workflows/ci.yml`)
runs both on every push.

## Project structure

```
app/
  __init__.py     app factory + JSON error handlers
  routes.py       HTTP layer (thin: parse -> validate -> call service)
  validators.py   request / query validation
  services.py     business logic + transaction (atomicity) handling
  db.py           schema, constraints, triggers, connection setup
  errors.py       typed API errors -> consistent error JSON
  openapi.py      OpenAPI spec behind /docs
  static/index.html   dashboard UI served at /
  static/swagger/     vendored Swagger UI (offline docs)
tests/test_api.py
docs/API.md       endpoint reference with examples
docs/ASSUMPTIONS.md
run.py  Dockerfile  docker-compose.yml  pyproject.toml (ruff)  .github/workflows/ci.yml
```

## Key design decisions

| Requirement | How it is enforced |
|---|---|
| Initial balance always 0 | Server hard-codes 0; a client-sent `balance` is rejected with 400 |
| Unique `reference_id` per account | `UNIQUE (account_id, reference_id)` in the DB + an explicit check that returns the existing transaction |
| No overdraft, even under concurrency | Single conditional `UPDATE ... SET balance = balance - ? WHERE balance >= ?`, plus `CHECK (balance >= 0)` |
| Atomicity | Ledger insert + balance update run in one `BEGIN IMMEDIATE ... COMMIT`; any exception triggers `ROLLBACK` |
| Immutability | No update/delete endpoints (405), and DB triggers abort any `UPDATE`/`DELETE` on `transactions` |
| Balance consistency | Balance only changes inside the same DB transaction that inserts the ledger row; a test asserts `balance == SUM(credits) - SUM(debits)` |
| Money precision | Stored as integer minor units (amount x 100), max 2 decimal places, no floats in storage |
| Transfers | Both legs, both balance updates and the `transfers` record commit together or not at all; leg references are derived (`<ref>:debit` / `<ref>:credit`) so they cannot collide with normal transactions |
| Idempotency | Same `reference_id` + same payload: 409 with the original record. Same `reference_id` + different payload: 422 `IDEMPOTENCY_MISMATCH`. `Idempotency-Key` header accepted as an alternative |
| Auditable consistency | `GET /accounts/{id}/reconcile` recomputes the balance from the ledger and reports whether it matches |
| Input hardening | `page` capped (no integer-overflow 500s), 64 KB body limit, unknown fields rejected |

See [docs/API.md](docs/API.md) for the endpoint reference and
[docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) for assumptions.

## Design decisions and trade-offs

- **Stored balance + ledger, not derived balance.** Reads are O(1) and the `CHECK (balance >= 0)` constraint
  can guard overdrafts in the database. The cost is that two numbers must stay in sync; they only ever change
  inside the same DB transaction, and `/reconcile` plus a test verify the invariant.
- **Conditional UPDATE for debits.** `UPDATE ... WHERE balance >= ?` makes the check and the decrement one
  statement, so there is no read-then-write race even without explicit row locks.
- **Duplicates are caught twice.** An explicit lookup produces a friendly response; the `UNIQUE` constraint is the
  backstop if anything ever bypasses it.
- **Rejected attempts are not stored.** The ledger only contains valid transactions, which keeps "balance = sum of
  the ledger" trivially true. Failed attempts would belong in a separate audit/log table.
- **SQLite, no ORM.** Zero setup and every constraint is visible. Writers are serialized (`BEGIN IMMEDIATE`), which
  is why the container runs one worker with threads.
- **Moving to PostgreSQL.** Swap `BEGIN IMMEDIATE` for a normal transaction with `SELECT ... FOR UPDATE` on the
  account row(s), locking transfer accounts in a consistent order (by id) to avoid deadlocks. The conditional
  UPDATE, constraints and error mapping carry over unchanged; the immutability triggers become PL/pgSQL triggers.
  Use `NUMERIC`/`BIGINT` for money and `TIMESTAMPTZ` for timestamps, and add migrations (e.g. Alembic).
- **Not included (out of scope):** authentication/authorization, rate limiting, currency support, account closing.
