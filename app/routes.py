from flask import Blueprint, current_app, g, jsonify, request, send_from_directory

from . import services, validators
from .db import connect
from .openapi import build_spec

bp = Blueprint("api", __name__)


def db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


@bp.teardown_app_request
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@bp.get("/")
def index():
    return send_from_directory(current_app.static_folder, "index.html")


@bp.get("/health")
def health():
    return jsonify({"status": "ok"})


@bp.get("/openapi.json")
def openapi():
    return jsonify(build_spec())


@bp.get("/docs")
def docs():
    return (
        '<!doctype html><title>Ledger API docs</title>'
        '<link rel="stylesheet" href="/static/swagger/swagger-ui.css">'
        '<div id="ui"></div>'
        '<script src="/static/swagger/swagger-ui-bundle.js"></script>'
        '<script>SwaggerUIBundle({url:"/openapi.json",dom_id:"#ui"})</script>'
    )


@bp.get("/accounts")
def list_accounts():
    q = validators.validate_history_params({k: v for k, v in request.args.items() if k != "type"})
    return jsonify(services.list_accounts(db(), q["page"], q["limit"]))


@bp.post("/accounts")
def create_account():
    data = validators.validate_create_account(request.get_json(silent=True))
    return jsonify(services.create_account(db(), data["name"])), 201


@bp.get("/accounts/<account_id>")
def get_account(account_id):
    account_id = validators.validate_account_id(account_id)
    return jsonify(services.get_account(db(), account_id))


@bp.get("/accounts/<account_id>/balance")
def get_balance(account_id):
    account_id = validators.validate_account_id(account_id)
    return jsonify(services.get_balance(db(), account_id))


def _json_body():
    body = request.get_json(silent=True)
    return validators.apply_idempotency_header(body, request.headers.get("Idempotency-Key"))


def _post_transaction(data):
    tx, balance = services.create_transaction(
        db(), data["account_id"], data["type"], data["amount"], data["description"], data["reference_id"]
    )
    return jsonify({"transaction": tx, "balance": balance}), 201


@bp.post("/accounts/<account_id>/transactions")
def create_transaction(account_id):
    account_id = validators.validate_account_id(account_id)
    return _post_transaction(validators.validate_create_transaction(_json_body(), path_account_id=account_id))


@bp.post("/transactions")
def create_transaction_flat():
    """Alias: same operation, with `account_id` in the body instead of the URL."""
    return _post_transaction(validators.validate_create_transaction(_json_body(), require_account_id=True))


@bp.get("/accounts/<account_id>/reconcile")
def reconcile(account_id):
    account_id = validators.validate_account_id(account_id)
    return jsonify(services.reconcile(db(), account_id))


@bp.get("/accounts/<account_id>/transactions")
def list_transactions(account_id):
    account_id = validators.validate_account_id(account_id)
    q = validators.validate_history_params(request.args)
    return jsonify(services.list_transactions(db(), account_id, q["type"], q["page"], q["limit"]))


@bp.post("/transfers")
def create_transfer():
    d = validators.validate_transfer(_json_body())
    result = services.create_transfer(
        db(), d["from_account_id"], d["to_account_id"], d["amount"], d["description"], d["reference_id"]
    )
    return jsonify(result), 201
