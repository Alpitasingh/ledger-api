"""OpenAPI 3 description of the API, served at /openapi.json and rendered at /docs."""

_REF = lambda n: {"$ref": f"#/components/schemas/{n}"}
_JSON = lambda s: {"application/json": {"schema": s}}


def _resp(desc, schema=None):
    r = {"description": desc}
    if schema:
        r["content"] = _JSON(schema)
    return r


_ERR = lambda d: _resp(d, _REF("Error"))
_ID = {"name": "account_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}
_IDEM = {"name": "Idempotency-Key", "in": "header", "required": False, "schema": {"type": "string"},
         "description": "Alternative to the body's reference_id."}
_PAGE = [
    {"name": "page", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 1000000, "default": 1}},
    {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10}},
]


def build_spec():
    return {
        "openapi": "3.0.3",
        "info": {"title": "Transaction Ledger API", "version": "1.0.0",
                 "description": "Accounts, credit/debit transactions, balances, history and transfers."},
        "paths": {
            "/accounts": {
                "post": {"summary": "Create account", "requestBody": {"required": True, "content": _JSON(_REF("NewAccount"))},
                         "responses": {"201": _resp("Created", _REF("Account")), "400": _ERR("Validation error")}},
                "get": {"summary": "List accounts (newest first)", "parameters": _PAGE,
                        "responses": {"200": _resp("OK"), "400": _ERR("Invalid pagination")}},
            },
            "/accounts/{account_id}": {"get": {"summary": "Get account", "parameters": [_ID],
                "responses": {"200": _resp("OK", _REF("Account")), "404": _ERR("Account not found")}}},
            "/accounts/{account_id}/balance": {"get": {"summary": "Get balance", "parameters": [_ID],
                "responses": {"200": _resp("OK"), "404": _ERR("Account not found")}}},
            "/accounts/{account_id}/transactions": {
                "post": {"summary": "Add transaction", "parameters": [_ID, _IDEM],
                         "requestBody": {"required": True, "content": _JSON(_REF("NewTransaction"))},
                         "responses": {"201": _resp("Created"), "400": _ERR("Validation error"),
                                       "404": _ERR("Account not found"), "409": _ERR("Duplicate reference_id (same payload)"),
                                       "422": _ERR("Insufficient balance, or reference_id reused with a different payload")}},
                "get": {"summary": "Transaction history (latest first)",
                        "parameters": [_ID, {"name": "type", "in": "query", "schema": {"type": "string", "enum": ["credit", "debit"]}}] + _PAGE,
                        "responses": {"200": _resp("OK"), "400": _ERR("Invalid parameters"), "404": _ERR("Account not found")}},
            },
            "/transactions": {"post": {"summary": "Add transaction (account_id in body)", "parameters": [_IDEM],
                "requestBody": {"required": True, "content": _JSON(_REF("NewTransactionFlat"))},
                "responses": {"201": _resp("Created"), "400": _ERR("Validation error"), "404": _ERR("Account not found"),
                              "409": _ERR("Duplicate reference_id (same payload)"),
                              "422": _ERR("Insufficient balance, or reference_id reused with a different payload")}}},
            "/accounts/{account_id}/reconcile": {"get": {"summary": "Compare stored balance with the ledger sum",
                "parameters": [_ID], "responses": {"200": _resp("OK"), "404": _ERR("Account not found")}}},
            "/transfers": {"post": {"summary": "Transfer between accounts (atomic)", "parameters": [_IDEM],
                "requestBody": {"required": True, "content": _JSON(_REF("NewTransfer"))},
                "responses": {"201": _resp("Created"), "400": _ERR("Validation error"), "404": _ERR("Account not found"),
                              "409": _ERR("Duplicate reference_id (same payload)"),
                              "422": _ERR("Insufficient balance, or reference_id reused with a different payload")}}},
        },
        "components": {"schemas": {
            "Error": {"type": "object", "properties": {"error": {"type": "object", "properties": {
                "code": {"type": "string"}, "message": {"type": "string"},
                "details": {"type": "array", "items": {"type": "object"}}}}}},
            "NewAccount": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string", "maxLength": 100}}},
            "Account": {"type": "object", "properties": {"id": {"type": "string", "format": "uuid"}, "name": {"type": "string"},
                "balance": {"type": "number"}, "created_at": {"type": "string", "format": "date-time"}}},
            "NewTransaction": {"type": "object", "required": ["type", "amount", "reference_id"], "properties": {
                "type": {"type": "string", "enum": ["credit", "debit"]}, "amount": {"type": "number", "exclusiveMinimum": 0},
                "reference_id": {"type": "string", "maxLength": 100}, "description": {"type": "string", "maxLength": 255},
                "account_id": {"type": "string", "format": "uuid", "description": "Optional; must match the URL."}}},
            "NewTransactionFlat": {"type": "object", "required": ["account_id", "type", "amount", "reference_id"], "properties": {
                "account_id": {"type": "string", "format": "uuid"},
                "type": {"type": "string", "enum": ["credit", "debit"]}, "amount": {"type": "number", "exclusiveMinimum": 0},
                "reference_id": {"type": "string", "maxLength": 100}, "description": {"type": "string", "maxLength": 255}}},
            "NewTransfer": {"type": "object", "required": ["from_account_id", "to_account_id", "amount", "reference_id"], "properties": {
                "from_account_id": {"type": "string", "format": "uuid"}, "to_account_id": {"type": "string", "format": "uuid"},
                "amount": {"type": "number", "exclusiveMinimum": 0}, "reference_id": {"type": "string"}, "description": {"type": "string"}}},
        }},
    }
