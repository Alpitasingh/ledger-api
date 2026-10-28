"""Business logic. Every write runs inside a single BEGIN IMMEDIATE ... COMMIT block,
so creating a transaction and updating the balance is one atomic operation."""
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .errors import AccountNotFound, DuplicateTransaction, InsufficientBalance
from .validators import RESERVED_REF_PREFIX as LEG_PREFIX


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def money(minor):
    """Minor units -> JSON number with 2 decimal places of precision."""
    return minor / 100 if minor % 100 else minor // 100


def account_to_dict(row):
    return {"id": row["id"], "name": row["name"], "balance": money(row["balance"]), "created_at": row["created_at"]}


def tx_to_dict(row):
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "type": row["type"],
        "amount": money(row["amount"]),
        "description": row["description"],
        "reference_id": row["reference_id"],
        "created_at": row["created_at"],
    }


@contextmanager
def atomic(conn):
    """Single write transaction: commit on success, roll back on ANY exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _get_account(conn, account_id):
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise AccountNotFound(f"Account '{account_id}' not found.")
    return row


def _find_tx(conn, account_id, reference_id):
    return conn.execute(
        "SELECT * FROM transactions WHERE account_id = ? AND reference_id = ?", (account_id, reference_id)
    ).fetchone()


def _apply(conn, account_id, tx_type, amount, description, reference_id):
    """Insert a ledger row and move the balance. Must be called inside atomic()."""
    tx_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO transactions (id, account_id, type, amount, description, reference_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tx_id, account_id, tx_type, amount, description, reference_id, _now()),
    )
    if tx_type == "credit":
        conn.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (amount, account_id))
    else:
        # Conditional update: the balance check and the decrement are one statement,
        # so concurrent debits can never overdraw the account.
        cur = conn.execute(
            "UPDATE accounts SET balance = balance - ? WHERE id = ? AND balance >= ?", (amount, account_id, amount)
        )
        if cur.rowcount != 1:
            raise InsufficientBalance("Insufficient balance for this debit.")
    return conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()


# ---------------------------------------------------------------- accounts
def create_account(conn, name):
    account_id = str(uuid.uuid4())
    with atomic(conn):
        # balance is never taken from the client
        conn.execute(
            "INSERT INTO accounts (id, name, balance, created_at) VALUES (?, ?, 0, ?)", (account_id, name, _now())
        )
    return account_to_dict(conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone())


def list_accounts(conn, page, limit):
    total = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
    rows = conn.execute(
        "SELECT * FROM accounts ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?", (limit, (page - 1) * limit)
    ).fetchall()
    return {
        "data": [account_to_dict(r) for r in rows],
        "pagination": {"page": page, "limit": limit, "total_records": total,
                       "total_pages": math.ceil(total / limit) if total else 0},
    }


def get_account(conn, account_id):
    return account_to_dict(_get_account(conn, account_id))


def get_balance(conn, account_id):
    row = _get_account(conn, account_id)
    return {"account_id": row["id"], "balance": money(row["balance"])}


def reconcile(conn, account_id):
    """Compare the stored balance with the sum of the account's ledger entries."""
    row = _get_account(conn, account_id)
    agg = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(CASE type WHEN 'credit' THEN amount ELSE -amount END), 0) AS s "
        "FROM transactions WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return {
        "account_id": row["id"],
        "balance": money(row["balance"]),
        "ledger_sum": money(agg["s"]),
        "transaction_count": agg["n"],
        "consistent": row["balance"] == agg["s"],
    }


# ---------------------------------------------------------------- transactions
def create_transaction(conn, account_id, tx_type, amount, description, reference_id):
    with atomic(conn):
        _get_account(conn, account_id)
        existing = _find_tx(conn, account_id, reference_id)
        if existing is not None:
            # Same reference_id on the same account is always a duplicate: nothing is created and the
            # balance is untouched. `payload_matches` tells the client whether it was a faithful retry.
            same = (existing["type"], existing["amount"], existing["description"]) == (tx_type, amount, description)
            raise DuplicateTransaction(
                "A transaction with this reference_id already exists for this account.",
                extra={"existing_transaction": tx_to_dict(existing), "payload_matches": same},
            )
        try:
            tx = _apply(conn, account_id, tx_type, amount, description, reference_id)
        except sqlite3.IntegrityError:
            # DB-level UNIQUE(account_id, reference_id) is the final safety net.
            raise DuplicateTransaction("A transaction with this reference_id already exists for this account.") from None
        balance = conn.execute("SELECT balance FROM accounts WHERE id = ?", (account_id,)).fetchone()["balance"]
    return tx_to_dict(tx), money(balance)


def list_transactions(conn, account_id, tx_type, page, limit):
    _get_account(conn, account_id)
    where, params = "account_id = ?", [account_id]
    if tx_type:
        where += " AND type = ?"
        params.append(tx_type)
    total = conn.execute(f"SELECT COUNT(*) AS c FROM transactions WHERE {where}", params).fetchone()["c"]
    rows = conn.execute(
        f"SELECT * FROM transactions WHERE {where} ORDER BY created_at DESC, seq DESC LIMIT ? OFFSET ?",
        params + [limit, (page - 1) * limit],
    ).fetchall()
    return {
        "data": [tx_to_dict(r) for r in rows],
        "pagination": {
            "page": page,
            "limit": limit,
            "total_records": total,
            "total_pages": math.ceil(total / limit) if total else 0,
        },
    }


# ---------------------------------------------------------------- transfers
def create_transfer(conn, from_id, to_id, amount, description, reference_id):
    desc = description or "Transfer"
    with atomic(conn):
        _get_account(conn, from_id)
        _get_account(conn, to_id)
        dup = conn.execute(
            "SELECT * FROM transfers WHERE from_account_id = ? AND reference_id = ?", (from_id, reference_id)
        ).fetchone()
        if dup is not None:
            same = (dup["to_account_id"], dup["amount"], dup["description"]) == (to_id, amount, desc)
            raise DuplicateTransaction(
                "A transfer with this reference_id already exists for the source account.",
                extra={"existing_transfer": transfer_to_dict(dup), "payload_matches": same},
            )
        transfer_id = str(uuid.uuid4())
        try:
            # Both legs + both balance updates + the transfer record commit together or not at all.
            # Leg references are built from the (unique) transfer id under the reserved "xfer:" prefix
            # (rejected for client-supplied references), so a leg can never collide with an ordinary
            # transaction or with a leg of another transfer, whatever reference_id the client chose.
            debit = _apply(conn, from_id, "debit", amount, f"{desc} (to {to_id})", f"{LEG_PREFIX}{transfer_id}:debit")
            credit = _apply(conn, to_id, "credit", amount, f"{desc} (from {from_id})", f"{LEG_PREFIX}{transfer_id}:credit")
        except sqlite3.IntegrityError:
            # Unreachable by construction; kept as a last-resort guard (the whole block still rolls back).
            raise DuplicateTransaction("A ledger entry for this transfer already exists.") from None
        conn.execute(
            "INSERT INTO transfers (id, from_account_id, to_account_id, amount, description, reference_id, "
            "debit_tx_id, credit_tx_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (transfer_id, from_id, to_id, amount, desc, reference_id, debit["id"], credit["id"], _now()),
        )
        transfer = conn.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
        balances = {
            r["id"]: r["balance"]
            for r in conn.execute("SELECT id, balance FROM accounts WHERE id IN (?, ?)", (from_id, to_id))
        }
    result = transfer_to_dict(transfer)
    result["debit_transaction"] = tx_to_dict(debit)
    result["credit_transaction"] = tx_to_dict(credit)
    result["balances"] = {from_id: money(balances[from_id]), to_id: money(balances[to_id])}
    return result


def transfer_to_dict(row):
    return {
        "id": row["id"],
        "from_account_id": row["from_account_id"],
        "to_account_id": row["to_account_id"],
        "amount": money(row["amount"]),
        "description": row["description"],
        "reference_id": row["reference_id"],
        "debit_transaction_id": row["debit_tx_id"],
        "credit_transaction_id": row["credit_tx_id"],
        "created_at": row["created_at"],
    }
