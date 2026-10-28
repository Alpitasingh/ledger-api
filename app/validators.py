"""Input validation. Each function returns cleaned values or raises ValidationError."""
import uuid
from decimal import Decimal, InvalidOperation

from .errors import InvalidRequestBody, ValidationError

MAX_AMOUNT_MINOR = 10**14  # 1 trillion major units
MAX_LIMIT = 100
MAX_PAGE = 1_000_000  # keeps OFFSET well inside SQLite's 64-bit integer range
DEFAULT_LIMIT = 10


def require_json_object(payload):
    if not isinstance(payload, dict):
        raise InvalidRequestBody("Request body must be a valid JSON object.")
    return payload


def _err(field, message):
    return {"field": field, "message": message}


def validate_account_id(value, field="account_id"):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError("Invalid account id.", [_err(field, "must be a valid UUID")]) from None


def _check_amount(value, errors, field="amount"):
    """Return amount in minor units, or None (and append to errors)."""
    if value is None:
        errors.append(_err(field, "is required"))
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(_err(field, "must be a number"))
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        errors.append(_err(field, "must be a number"))
        return None
    if not d.is_finite():
        errors.append(_err(field, "must be a finite number"))
        return None
    if d <= 0:
        errors.append(_err(field, "must be greater than 0"))
        return None
    if d > Decimal(MAX_AMOUNT_MINOR) / 100:  # check size first: quantize() raises on huge values
        errors.append(_err(field, "exceeds the maximum allowed amount"))
        return None
    if d != d.quantize(Decimal("0.01")):
        errors.append(_err(field, "must have at most 2 decimal places"))
        return None
    minor = int(d * 100)
    if minor > MAX_AMOUNT_MINOR:
        errors.append(_err(field, "exceeds the maximum allowed amount"))
        return None
    return minor


def _check_str(value, field, errors, required=True, max_len=100):
    if value is None:
        if required:
            errors.append(_err(field, "is required"))
        return None
    if not isinstance(value, str):
        errors.append(_err(field, "must be a string"))
        return None
    value = value.strip()
    if required and not value:
        errors.append(_err(field, "must not be empty"))
        return None
    if len(value) > max_len:
        errors.append(_err(field, f"must be at most {max_len} characters"))
        return None
    return value


def validate_create_account(body):
    body = require_json_object(body)
    errors = []
    if "balance" in body:
        errors.append(_err("balance", "cannot be set by the client; initial balance is always 0"))
    for k in sorted(set(body) - {"name", "balance"}):
        errors.append(_err(k, "unknown field"))
    name = _check_str(body.get("name"), "name", errors)
    if errors:
        raise ValidationError("Invalid account data.", errors) from None
    return {"name": name}


def apply_idempotency_header(body, header_value):
    """Allow `Idempotency-Key: <key>` as an alternative to the body's reference_id."""
    if not header_value or not isinstance(body, dict):
        return body
    key = header_value.strip()
    if "reference_id" in body and body["reference_id"] != key:
        raise ValidationError(
            "Conflicting idempotency keys.",
            [_err("reference_id", "does not match the Idempotency-Key header")],
        )
    return {**body, "reference_id": key}


def validate_create_transaction(body, path_account_id=None, require_account_id=False):
    """`account_id` may be echoed in the body (the brief lists it as a transaction field).

    For POST /accounts/{id}/transactions it must match the path if present; for the
    POST /transactions alias it is required and taken from the body.
    """
    body = require_json_object(body)
    errors = []
    allowed = {"account_id", "type", "amount", "description", "reference_id"}
    for k in sorted(set(body) - allowed):
        errors.append(_err(k, "unknown field"))
    account_id = path_account_id
    if "account_id" in body or require_account_id:
        raw = body.get("account_id")
        if raw is None:
            errors.append(_err("account_id", "is required"))
        else:
            try:
                body_id = str(uuid.UUID(str(raw)))
            except (ValueError, TypeError, AttributeError):
                errors.append(_err("account_id", "must be a valid UUID"))
            else:
                if path_account_id is not None and body_id != path_account_id:
                    errors.append(_err("account_id", "does not match the account in the URL"))
                account_id = body_id
    tx_type = body.get("type")
    if tx_type is None:
        errors.append(_err("type", "is required"))
    elif tx_type not in ("credit", "debit"):
        errors.append(_err("type", "must be either 'credit' or 'debit'"))
    amount = _check_amount(body.get("amount"), errors)
    reference_id = _check_str(body.get("reference_id"), "reference_id", errors)
    description = _check_str(body.get("description"), "description", errors, required=False, max_len=255)
    if errors:
        raise ValidationError("Invalid transaction data.", errors) from None
    return {"account_id": account_id, "type": tx_type, "amount": amount,
            "reference_id": reference_id, "description": description or ""}


def validate_transfer(body):
    body = require_json_object(body)
    errors = []
    allowed = {"from_account_id", "to_account_id", "amount", "reference_id", "description"}
    for k in sorted(set(body) - allowed):
        errors.append(_err(k, "unknown field"))
    ids = {}
    for f in ("from_account_id", "to_account_id"):
        raw = body.get(f)
        if raw is None:
            errors.append(_err(f, "is required"))
            continue
        try:
            ids[f] = str(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            errors.append(_err(f, "must be a valid UUID"))
    if len(ids) == 2 and ids["from_account_id"] == ids["to_account_id"]:
        errors.append(_err("to_account_id", "must differ from from_account_id"))
    amount = _check_amount(body.get("amount"), errors)
    reference_id = _check_str(body.get("reference_id"), "reference_id", errors)
    description = _check_str(body.get("description"), "description", errors, required=False, max_len=255)
    if errors:
        raise ValidationError("Invalid transfer data.", errors) from None
    return {**ids, "amount": amount, "reference_id": reference_id, "description": description or ""}


def validate_history_params(args):
    errors = []
    tx_type = args.get("type")
    if tx_type is not None and tx_type not in ("credit", "debit"):
        errors.append(_err("type", "must be either 'credit' or 'debit'"))

    def _int(name, default, lo, hi):
        raw = args.get(name)
        if raw is None:
            return default
        try:
            v = int(raw)
        except ValueError:
            errors.append(_err(name, "must be an integer"))
            return default
        if v < lo or (hi is not None and v > hi):
            rng = f"between {lo} and {hi}" if hi else f">= {lo}"
            errors.append(_err(name, f"must be {rng}"))
        return v

    page = _int("page", 1, 1, MAX_PAGE)
    limit = _int("limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    if errors:
        raise ValidationError("Invalid query parameters.", errors) from None
    return {"type": tx_type, "page": page, "limit": limit}
