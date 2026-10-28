import os
import sqlite3
import tempfile
import threading
import unittest
import uuid

from app import create_app, services


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.app = create_app(self.db_path)
        self.c = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def account(self, name="Alice"):
        r = self.c.post("/accounts", json={"name": name})
        self.assertEqual(r.status_code, 201)
        return r.get_json()

    def tx(self, acc_id, type_, amount, ref, **kw):
        return self.c.post(f"/accounts/{acc_id}/transactions",
                           json={"type": type_, "amount": amount, "reference_id": ref, **kw})

    def balance(self, acc_id):
        return self.c.get(f"/accounts/{acc_id}/balance").get_json()["balance"]


class AccountTests(BaseTest):
    def test_create_account(self):
        a = self.account()
        self.assertEqual(a["balance"], 0)
        self.assertTrue(a["created_at"])
        uuid.UUID(a["id"])

    def test_ids_unique(self):
        self.assertNotEqual(self.account()["id"], self.account()["id"])

    def test_name_required(self):
        for body in ({}, {"name": ""}, {"name": "   "}, {"name": 5}):
            r = self.c.post("/accounts", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.get_json()["error"]["code"], "VALIDATION_ERROR")

    def test_client_cannot_set_balance(self):
        r = self.c.post("/accounts", json={"name": "Bob", "balance": 1000})
        self.assertEqual(r.status_code, 400)

    def test_invalid_body(self):
        r = self.c.post("/accounts", data="not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["code"], "INVALID_REQUEST_BODY")
        r = self.c.post("/accounts", json=[1, 2])
        self.assertEqual(r.status_code, 400)

    def test_balance_404_and_bad_id(self):
        r = self.c.get(f"/accounts/{uuid.uuid4()}/balance")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"]["code"], "ACCOUNT_NOT_FOUND")
        self.assertEqual(self.c.get("/accounts/abc/balance").status_code, 400)


class TransactionTests(BaseTest):
    def test_credit_debit_flow(self):
        a = self.account()["id"]
        for t, amt, ref in [("credit", 100, "r1"), ("credit", 50, "r2"), ("debit", 30, "r3"), ("debit", 20, "r4")]:
            self.assertEqual(self.tx(a, t, amt, ref).status_code, 201)
        self.assertEqual(self.balance(a), 100)

    def test_credit_response(self):
        a = self.account()["id"]
        self.tx(a, "credit", 100, "r1")
        r = self.tx(a, "credit", 50, "r2", description="top up")
        body = r.get_json()
        self.assertEqual(body["balance"], 150)
        self.assertEqual(body["transaction"]["description"], "top up")

    def test_insufficient_balance(self):
        a = self.account()["id"]
        self.tx(a, "credit", 100, "r1")
        r = self.tx(a, "debit", 150, "r2")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["error"]["code"], "INSUFFICIENT_BALANCE")
        self.assertEqual(self.balance(a), 100)
        # rejected debit must not leave a ledger row, and its reference stays usable
        self.assertEqual(self.c.get(f"/accounts/{a}/transactions").get_json()["pagination"]["total_records"], 1)
        self.assertEqual(self.tx(a, "debit", 40, "r2").status_code, 201)

    def test_exact_balance_debit_allowed(self):
        a = self.account()["id"]
        self.tx(a, "credit", 10, "r1")
        self.assertEqual(self.tx(a, "debit", 10, "r2").status_code, 201)
        self.assertEqual(self.balance(a), 0)

    def test_duplicate_reference(self):
        a = self.account()["id"]
        first = self.tx(a, "credit", 100, "dup")
        r = self.tx(a, "credit", 100, "dup")
        self.assertEqual(r.status_code, 409)
        err = r.get_json()["error"]
        self.assertEqual(err["code"], "DUPLICATE_TRANSACTION")
        self.assertEqual(err["existing_transaction"]["id"], first.get_json()["transaction"]["id"])
        self.assertEqual(self.balance(a), 100)
        self.assertEqual(self.c.get(f"/accounts/{a}/transactions").get_json()["pagination"]["total_records"], 1)

    def test_same_reference_different_accounts(self):
        a, b = self.account("A")["id"], self.account("B")["id"]
        self.assertEqual(self.tx(a, "credit", 5, "same").status_code, 201)
        self.assertEqual(self.tx(b, "credit", 5, "same").status_code, 201)

    def test_validation(self):
        a = self.account()["id"]
        bad = [
            {"type": "refund", "amount": 5, "reference_id": "x"},
            {"amount": 5, "reference_id": "x"},
            {"type": "credit", "amount": 0, "reference_id": "x"},
            {"type": "credit", "amount": -5, "reference_id": "x"},
            {"type": "credit", "amount": "10", "reference_id": "x"},
            {"type": "credit", "amount": True, "reference_id": "x"},
            {"type": "credit", "amount": 1.234, "reference_id": "x"},
            {"type": "credit", "amount": 5},
            {"type": "credit", "amount": 5, "reference_id": "  "},
        ]
        for body in bad:
            r = self.c.post(f"/accounts/{a}/transactions", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.get_json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(self.balance(a), 0)

    def test_unknown_account(self):
        r = self.tx(str(uuid.uuid4()), "credit", 5, "x")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.tx("not-a-uuid", "credit", 5, "x").status_code, 400)

    def test_decimal_amounts(self):
        a = self.account()["id"]
        self.tx(a, "credit", 0.1, "r1")
        self.tx(a, "credit", 0.2, "r2")
        self.assertEqual(self.balance(a), 0.3)  # no float drift (stored as integer minor units)


class HistoryTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.a = self.account()["id"]
        for i in range(1, 8):
            self.tx(self.a, "credit" if i % 2 else "debit", 1, f"r{i}") if i > 1 else self.tx(self.a, "credit", 50, "r1")

    def get(self, qs=""):
        return self.c.get(f"/accounts/{self.a}/transactions{qs}")

    def test_latest_first(self):
        refs = [t["reference_id"] for t in self.get().get_json()["data"]]
        self.assertEqual(refs, [f"r{i}" for i in range(7, 0, -1)])

    def test_pagination(self):
        body = self.get("?page=2&limit=3").get_json()
        self.assertEqual(body["pagination"], {"page": 2, "limit": 3, "total_records": 7, "total_pages": 3})
        self.assertEqual([t["reference_id"] for t in body["data"]], ["r4", "r3", "r2"])
        self.assertEqual(self.get("?page=9&limit=3").get_json()["data"], [])

    def test_filter(self):
        credits = self.get("?type=credit").get_json()
        self.assertTrue(all(t["type"] == "credit" for t in credits["data"]))
        self.assertEqual(credits["pagination"]["total_records"], 4)
        self.assertEqual(self.get("?type=debit").get_json()["pagination"]["total_records"], 3)

    def test_invalid_params(self):
        for qs in ("?page=0", "?page=x", "?limit=0", "?limit=101", "?type=foo", "?limit=-1"):
            r = self.get(qs)
            self.assertEqual(r.status_code, 400, qs)
            self.assertEqual(r.get_json()["error"]["code"], "VALIDATION_ERROR")

    def test_unknown_account(self):
        self.assertEqual(self.c.get(f"/accounts/{uuid.uuid4()}/transactions").status_code, 404)


class IntegrityTests(BaseTest):
    def test_no_update_delete_endpoints(self):
        a = self.account()["id"]
        t = self.tx(a, "credit", 5, "r1").get_json()["transaction"]["id"]
        for m in ("put", "patch", "delete"):
            r = getattr(self.c, m)(f"/accounts/{a}/transactions/{t}")
            self.assertIn(r.status_code, (404, 405))
            r = getattr(self.c, m)(f"/accounts/{a}/transactions")
            self.assertEqual(r.status_code, 405)
            self.assertEqual(r.get_json()["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_db_blocks_update_delete(self):
        a = self.account()["id"]
        self.tx(a, "credit", 5, "r1")
        conn = sqlite3.connect(self.db_path)
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("UPDATE transactions SET amount = 999")
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM transactions")
        conn.close()

    def test_db_blocks_negative_balance(self):
        a = self.account()["id"]
        conn = sqlite3.connect(self.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE accounts SET balance = -1 WHERE id = ?", (a,))
        conn.close()

    def test_balance_equals_sum_of_ledger(self):
        a = self.account()["id"]
        for i in range(20):
            self.tx(a, "credit" if i % 3 else "debit", 7, f"r{i}")
        conn = sqlite3.connect(self.db_path)
        (ledger,) = conn.execute(
            "SELECT COALESCE(SUM(CASE type WHEN 'credit' THEN amount ELSE -amount END),0) "
            "FROM transactions WHERE account_id=?", (a,)).fetchone()
        (bal,) = conn.execute("SELECT balance FROM accounts WHERE id=?", (a,)).fetchone()
        conn.close()
        self.assertEqual(ledger, bal)

    def test_atomic_rollback_on_failure(self):
        """If anything fails after the ledger row is inserted, nothing is persisted."""
        a = self.account()["id"]
        original = services._apply

        def boom(conn, *args):
            original(conn, *args)
            raise RuntimeError("crash after insert/update")

        services._apply = boom
        try:
            r = self.tx(a, "credit", 100, "r1")
            self.assertEqual(r.status_code, 500)
        finally:
            services._apply = original
        self.assertEqual(self.balance(a), 0)
        self.assertEqual(self.c.get(f"/accounts/{a}/transactions").get_json()["pagination"]["total_records"], 0)

    def test_concurrent_debits_never_overdraw(self):
        a = self.account()["id"]
        self.tx(a, "credit", 100, "seed")
        results = []

        def worker(i):
            client = self.app.test_client()
            r = client.post(f"/accounts/{a}/transactions",
                            json={"type": "debit", "amount": 30, "reference_id": f"d{i}"})
            results.append(r.status_code)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results.count(201), 3)
        self.assertEqual(results.count(422), 7)
        self.assertEqual(self.balance(a), 10)

    def test_concurrent_duplicate_reference(self):
        a = self.account()["id"]
        results = []

        def worker():
            r = self.app.test_client().post(f"/accounts/{a}/transactions",
                                            json={"type": "credit", "amount": 10, "reference_id": "same"})
            results.append(r.status_code)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results.count(201), 1)
        self.assertEqual(results.count(409), 7)
        self.assertEqual(self.balance(a), 10)


class TransferTests(BaseTest):
    def transfer(self, src, dst, amount, ref="t1"):
        return self.c.post("/transfers", json={"from_account_id": src, "to_account_id": dst,
                                               "amount": amount, "reference_id": ref})

    def setUp(self):
        super().setUp()
        self.a, self.b = self.account("A")["id"], self.account("B")["id"]
        self.tx(self.a, "credit", 100, "seed")
        self.tx(self.b, "credit", 50, "seed")

    def test_transfer_success(self):
        r = self.transfer(self.a, self.b, 30)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.balance(self.a), 70)
        self.assertEqual(self.balance(self.b), 80)
        self.assertEqual(self.c.get(f"/accounts/{self.a}/transactions?type=debit").get_json()["pagination"]["total_records"], 1)
        self.assertEqual(self.c.get(f"/accounts/{self.b}/transactions?type=credit").get_json()["pagination"]["total_records"], 2)

    def test_insufficient_funds_no_partial(self):
        r = self.transfer(self.a, self.b, 500)
        self.assertEqual(r.status_code, 422)
        self.assertEqual((self.balance(self.a), self.balance(self.b)), (100, 50))

    def test_missing_account_no_partial(self):
        r = self.transfer(self.a, str(uuid.uuid4()), 10)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.balance(self.a), 100)

    def test_same_account_rejected(self):
        self.assertEqual(self.transfer(self.a, self.a, 10).status_code, 400)

    def test_duplicate_transfer(self):
        self.assertEqual(self.transfer(self.a, self.b, 30).status_code, 201)
        r = self.transfer(self.a, self.b, 30)
        self.assertEqual(r.status_code, 409)
        self.assertEqual((self.balance(self.a), self.balance(self.b)), (70, 80))

    def test_credit_leg_failure_rolls_back_debit(self):
        # Pre-existing tx on B with the derived credit-leg reference makes the credit leg violate UNIQUE.
        self.tx(self.b, "credit", 1, "clash:credit")
        r = self.transfer(self.a, self.b, 30, ref="clash")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.balance(self.a), 100)  # debit leg rolled back
        self.assertEqual(self.balance(self.b), 51)

    def test_transfer_reference_does_not_collide_with_plain_transaction(self):
        self.tx(self.b, "credit", 1, "shared")
        self.tx(self.a, "credit", 1, "shared")
        r = self.transfer(self.a, self.b, 30, ref="shared")
        self.assertEqual(r.status_code, 201)
        self.assertEqual((self.balance(self.a), self.balance(self.b)), (71, 81))

    def test_transfer_reference_reused_with_different_payload(self):
        self.assertEqual(self.transfer(self.a, self.b, 30).status_code, 201)
        r = self.transfer(self.a, self.b, 31)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["error"]["code"], "IDEMPOTENCY_MISMATCH")
        self.assertEqual((self.balance(self.a), self.balance(self.b)), (70, 80))

    def test_transfer_idempotency_header(self):
        body = {"from_account_id": self.a, "to_account_id": self.b, "amount": 5}
        h = {"Idempotency-Key": "hdr-1"}
        self.assertEqual(self.c.post("/transfers", json=body, headers=h).status_code, 201)
        self.assertEqual(self.c.post("/transfers", json=body, headers=h).status_code, 409)
        self.assertEqual(self.balance(self.a), 95)

    def test_validation(self):
        r = self.c.post("/transfers", json={"from_account_id": "x", "amount": -1})
        self.assertEqual(r.status_code, 400)


class HardeningTests(BaseTest):
    def test_huge_page_is_400_not_500(self):
        a = self.account()["id"]
        for url in (f"/accounts/{a}/transactions?page=99999999999999999999", "/accounts?page=99999999999999999999",
                    f"/accounts/{a}/transactions?page=1000001"):
            r = self.c.get(url)
            self.assertEqual(r.status_code, 400, url)
            self.assertEqual(r.get_json()["error"]["code"], "VALIDATION_ERROR")

    def test_huge_amount_is_400_not_500(self):
        a = self.account()["id"]
        for i, raw in enumerate(("1e30", "1e100", "10000000000000000000000000000000")):
            r = self.c.post(f"/accounts/{a}/transactions", data=f'{{"type":"credit","amount":{raw},"reference_id":"big{i}"}}',
                            content_type="application/json")
            self.assertEqual(r.status_code, 400, raw)
            self.assertEqual(r.get_json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(self.balance(a), 0)

    def test_account_id_in_body_matching_path_is_accepted(self):
        a = self.account()["id"]
        r = self.c.post(f"/accounts/{a}/transactions",
                        json={"account_id": a, "type": "credit", "amount": 5, "reference_id": "x1"})
        self.assertEqual(r.status_code, 201)

    def test_account_id_in_body_mismatch_or_invalid(self):
        a, b = self.account("A")["id"], self.account("B")["id"]
        base = {"type": "credit", "amount": 5, "reference_id": "x2"}
        r = self.c.post(f"/accounts/{a}/transactions", json={**base, "account_id": b})
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/accounts/{a}/transactions", json={**base, "account_id": "nope"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.balance(a), 0)

    def test_flat_transactions_endpoint(self):
        a = self.account()["id"]
        r = self.c.post("/transactions", json={"account_id": a, "type": "credit", "amount": 12, "reference_id": "f1"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["transaction"]["account_id"], a)
        self.assertEqual(self.balance(a), 12)
        self.assertEqual(self.c.post("/transactions", json={"type": "credit", "amount": 1, "reference_id": "f2"}).status_code, 400)
        r = self.c.post("/transactions", json={"account_id": str(uuid.uuid4()), "type": "credit", "amount": 1, "reference_id": "f3"})
        self.assertEqual(r.status_code, 404)

    def test_same_reference_different_payload_is_a_mismatch(self):
        a = self.account()["id"]
        self.tx(a, "credit", 100, "ref")
        r = self.tx(a, "credit", 999, "ref")
        self.assertEqual(r.status_code, 422)
        err = r.get_json()["error"]
        self.assertEqual(err["code"], "IDEMPOTENCY_MISMATCH")
        self.assertEqual(err["existing_transaction"]["amount"], 100)
        r = self.tx(a, "debit", 100, "ref")
        self.assertEqual(r.get_json()["error"]["code"], "IDEMPOTENCY_MISMATCH")
        self.assertEqual(self.balance(a), 100)

    def test_same_reference_same_payload_is_a_plain_duplicate(self):
        a = self.account()["id"]
        self.tx(a, "credit", 100, "ref", description="x")
        r = self.tx(a, "credit", 100, "ref", description="x")
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (409, "DUPLICATE_TRANSACTION"))

    def test_idempotency_key_header(self):
        a = self.account()["id"]
        h = {"Idempotency-Key": "key-1"}
        body = {"type": "credit", "amount": 10}
        r = self.c.post(f"/accounts/{a}/transactions", json=body, headers=h)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["transaction"]["reference_id"], "key-1")
        self.assertEqual(self.c.post(f"/accounts/{a}/transactions", json=body, headers=h).status_code, 409)
        self.assertEqual(self.balance(a), 10)

    def test_idempotency_key_header_conflicts_with_body(self):
        a = self.account()["id"]
        r = self.c.post(f"/accounts/{a}/transactions", headers={"Idempotency-Key": "k"},
                        json={"type": "credit", "amount": 10, "reference_id": "other"})
        self.assertEqual(r.status_code, 400)

    def test_reconcile(self):
        a, b = self.account("A")["id"], self.account("B")["id"]
        self.tx(a, "credit", 100, "r1")
        self.tx(a, "debit", 30.5, "r2")
        self.c.post("/transfers", json={"from_account_id": a, "to_account_id": b, "amount": 20, "reference_id": "t"})
        for acc, bal in ((a, 49.5), (b, 20)):
            body = self.c.get(f"/accounts/{acc}/reconcile").get_json()
            self.assertTrue(body["consistent"])
            self.assertEqual((body["balance"], body["ledger_sum"]), (bal, bal))
        self.assertEqual(self.c.get(f"/accounts/{uuid.uuid4()}/reconcile").status_code, 404)
        self.assertEqual(self.c.get("/accounts/bad/reconcile").status_code, 400)

    def test_reconcile_detects_tampering(self):
        a = self.account()["id"]
        self.tx(a, "credit", 100, "r1")
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE accounts SET balance = 5 WHERE id = ?", (a,))
        conn.commit()
        conn.close()
        self.assertFalse(self.c.get(f"/accounts/{a}/reconcile").get_json()["consistent"])

    def test_oversized_body_rejected(self):
        r = self.c.post("/accounts", data='{"name": "' + "x" * 70000 + '"}', content_type="application/json")
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.get_json()["error"]["code"], "PAYLOAD_TOO_LARGE")

    def test_docs_assets_are_self_hosted(self):
        self.assertNotIn("cdn.", self.c.get("/docs").get_data(as_text=True))
        r = self.c.get("/static/swagger/swagger-ui-bundle.js")
        self.assertEqual(r.status_code, 200)
        r.close()


class MiscTests(BaseTest):
    def test_unknown_route_json(self):
        r = self.c.get("/nope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"]["code"], "NOT_FOUND")


class DashboardTests(BaseTest):
    def test_dashboard_served(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Ledger Console", r.data)
        r.close()


class PlatformTests(BaseTest):
    def test_list_accounts_paginated_newest_first(self):
        for n in ("A", "B", "C"):
            self.account(n)
        r = self.c.get("/accounts?limit=2").get_json()
        self.assertEqual([a["name"] for a in r["data"]], ["C", "B"])
        self.assertEqual(r["pagination"]["total_records"], 3)
        self.assertEqual(r["pagination"]["total_pages"], 2)
        self.assertEqual(self.c.get("/accounts?page=0").status_code, 400)

    def test_openapi_and_docs(self):
        spec = self.c.get("/openapi.json").get_json()
        self.assertIn("/transfers", spec["paths"])
        self.assertEqual(self.c.get("/docs").status_code, 200)

    def test_request_id_header(self):
        r = self.c.get("/health", headers={"X-Request-ID": "abc123"})
        self.assertEqual(r.headers["X-Request-ID"], "abc123")
        self.assertTrue(self.c.get("/health").headers["X-Request-ID"])


if __name__ == "__main__":
    unittest.main()
