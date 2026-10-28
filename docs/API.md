# API Documentation

Base URL: `http://127.0.0.1:5000` · All bodies are JSON (`Content-Type: application/json`).

## Error format

Every error uses the same shape:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid transaction data.",
    "details": [{"field": "amount", "message": "must be greater than 0"}]
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Bad field values, malformed account id, bad `type`/`page`/`limit` (`page` max 1,000,000), client-sent `balance`, `account_id` in the body that differs from the URL |
| 400 | `INVALID_REQUEST_BODY` | Body missing, not valid JSON, or not a JSON object |
| 404 | `ACCOUNT_NOT_FOUND` | Account id is well-formed but does not exist |
| 404 | `NOT_FOUND` | Unknown route |
| 405 | `METHOD_NOT_ALLOWED` | e.g. PUT/PATCH/DELETE on transactions |
| 409 | `DUPLICATE_TRANSACTION` | Same `reference_id` **and same payload** already recorded for that account (a safe retry; includes the existing record) |
| 413 | `PAYLOAD_TOO_LARGE` | Request body larger than 64 KB |
| 422 | `IDEMPOTENCY_MISMATCH` | `reference_id` already used for a **different** payload (type/amount/description, or transfer target/amount); includes the existing record |
| 422 | `INSUFFICIENT_BALANCE` | Debit/transfer larger than available balance |
| 500 | `INTERNAL_ERROR` | Unexpected failure (nothing is persisted) |

Amounts are JSON numbers, > 0, with at most 2 decimal places. Timestamps are UTC ISO-8601.

---

## 1. Create account — `POST /accounts`

Request: `{"name": "Alice"}` (`balance` must NOT be sent; it returns 400)

`201 Created`
```json
{"id": "3a846b5a-3b1d-4ede-8faf-122a187019af", "name": "Alice", "balance": 0,
 "created_at": "2026-10-28T13:49:24.532140Z"}
```

## 1b. List accounts: `GET /accounts?page=1&limit=10`
Newest first. Returns `{"data": [...], "pagination": {...}}` in the same shape as transaction history.

## 2. Get account — `GET /accounts/{account_id}`
Returns the same object as above. `404` if it does not exist, `400` if the id is not a UUID.

## 3. Get balance — `GET /accounts/{account_id}/balance`

`200 OK`
```json
{"account_id": "3a846b5a-...", "balance": 100}
```

## 4. Add transaction — `POST /accounts/{account_id}/transactions`

| Field | Required | Rules |
|---|---|---|
| `type` | yes | `"credit"` or `"debit"` |
| `amount` | yes | number > 0, max 2 decimals |
| `reference_id` | yes* | non-empty string, ≤ 100 chars, unique per account |
| `description` | no | string ≤ 255 chars |
| `account_id` | no | optional echo of the URL's account; must match it, otherwise `400` |

\* May be sent as an `Idempotency-Key: <key>` header instead of in the body. If both are sent they must match.

**Alias:** `POST /transactions` does the same thing with `account_id` **required in the body** instead of the URL.

```json
{"type": "credit", "amount": 50, "reference_id": "pay-1001", "description": "Top up"}
```

`201 Created`
```json
{
  "transaction": {"id": "7255ab39-...", "account_id": "3a846b5a-...", "type": "credit",
                  "amount": 50, "description": "Top up", "reference_id": "pay-1001",
                  "created_at": "2026-10-28T13:49:24.565898Z"},
  "balance": 150
}
```

Other outcomes: `409 DUPLICATE_TRANSACTION` (same reference and payload: no new row, balance unchanged,
body contains `existing_transaction`), `422 IDEMPOTENCY_MISMATCH` (same reference, different payload:
nothing changes), `422 INSUFFICIENT_BALANCE` (balance unchanged), `404`, `400`.

## 5. Transaction history — `GET /accounts/{account_id}/transactions`

| Query param | Default | Rules |
|---|---|---|
| `type` | both | `credit` or `debit` |
| `page` | 1 | integer 1–1,000,000 |
| `limit` | 10 | integer 1–100 |

Sorted latest-first by `created_at` (ties broken by insertion order).

`200 OK`
```json
{
  "data": [ { "id": "...", "type": "debit", "amount": 30, "reference_id": "r3", "...": "..." } ],
  "pagination": {"page": 1, "limit": 10, "total_records": 4, "total_pages": 1}
}
```
A page beyond the last returns `data: []` with the correct totals.

## 6. Transfer money (bonus) — `POST /transfers`

```json
{"from_account_id": "<uuid>", "to_account_id": "<uuid>", "amount": 30,
 "reference_id": "xfer-1", "description": "Rent"}
```
`reference_id` is required (or send `Idempotency-Key`) and is unique per source account (safe retries).
The two ledger entries get the derived references `<reference_id>:debit` (source) and
`<reference_id>:credit` (destination), so a transfer never collides with an ordinary transaction that
uses the same reference.

`201 Created`
```json
{
  "id": "...", "from_account_id": "...", "to_account_id": "...", "amount": 30,
  "reference_id": "xfer-1", "debit_transaction": {"...": "..."}, "credit_transaction": {"...": "..."},
  "balances": {"<from_id>": 70, "<to_id>": 80}
}
```
Errors: `404` (either account missing), `422` (insufficient balance), `409` (duplicate reference, same payload), `422 IDEMPOTENCY_MISMATCH` (reference reused with a different payload),
`400` (validation, including same source and destination). On any failure neither account changes.

## 7. Reconcile — `GET /accounts/{account_id}/reconcile`
Recomputes the balance from the ledger and compares it with the stored balance.

`200 OK`
```json
{"account_id": "3a846b5a-...", "balance": 49.5, "ledger_sum": 49.5, "transaction_count": 2, "consistent": true}
```

## 8. Immutability
There are no endpoints to edit or delete transactions (`PUT`/`PATCH`/`DELETE` → `405`), and the
database rejects such statements through triggers.

## Quick curl walkthrough

```bash
curl -s -X POST localhost:5000/accounts -H 'Content-Type: application/json' -d '{"name":"Alice"}'
curl -s -X POST localhost:5000/accounts/$ID/transactions -H 'Content-Type: application/json' \
     -d '{"type":"credit","amount":100,"reference_id":"r1"}'
curl -s localhost:5000/accounts/$ID/balance
curl -s "localhost:5000/accounts/$ID/transactions?type=credit&page=1&limit=5"
```
