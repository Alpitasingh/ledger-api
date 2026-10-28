"""SQLite connection handling and schema.

Integrity is enforced in the database itself (CHECK / UNIQUE / FOREIGN KEY /
triggers), not only in application code.
Money is stored as INTEGER minor units (e.g. paise/cents) to avoid float drift.
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
    balance     INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,   -- stable tie-breaker for ordering
    id            TEXT NOT NULL UNIQUE,
    account_id    TEXT NOT NULL REFERENCES accounts(id),
    type          TEXT NOT NULL CHECK (type IN ('credit', 'debit')),
    amount        INTEGER NOT NULL CHECK (amount > 0),
    description   TEXT NOT NULL DEFAULT '',
    reference_id  TEXT NOT NULL CHECK (length(trim(reference_id)) > 0),
    created_at    TEXT NOT NULL,
    UNIQUE (account_id, reference_id)                  -- per-account idempotency key
);
CREATE INDEX IF NOT EXISTS idx_tx_account_seq ON transactions (account_id, seq DESC);

-- Transactions are immutable: block UPDATE and DELETE at the database level.
CREATE TRIGGER IF NOT EXISTS trg_tx_no_update
BEFORE UPDATE ON transactions
BEGIN SELECT RAISE(ABORT, 'transactions are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_tx_no_delete
BEFORE DELETE ON transactions
BEGIN SELECT RAISE(ABORT, 'transactions are immutable'); END;

CREATE TABLE IF NOT EXISTS transfers (
    id               TEXT PRIMARY KEY,
    from_account_id  TEXT NOT NULL REFERENCES accounts(id),
    to_account_id    TEXT NOT NULL REFERENCES accounts(id),
    amount           INTEGER NOT NULL CHECK (amount > 0),
    description      TEXT NOT NULL DEFAULT '',
    reference_id     TEXT NOT NULL CHECK (length(trim(reference_id)) > 0),
    debit_tx_id      TEXT NOT NULL REFERENCES transactions(id),
    credit_tx_id     TEXT NOT NULL REFERENCES transactions(id),
    created_at       TEXT NOT NULL,
    CHECK (from_account_id <> to_account_id),
    UNIQUE (from_account_id, reference_id)
);
"""


def connect(path):
    # isolation_level=None -> we control transactions explicitly (BEGIN IMMEDIATE ... COMMIT)
    conn = sqlite3.connect(path, isolation_level=None, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path):
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
