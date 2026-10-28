"""API error types. Every error is rendered as:
{"error": {"code": "...", "message": "...", "details": [...]}}
"""


class ApiError(Exception):
    status = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message, details=None, extra=None):
        super().__init__(message)
        self.message = message
        self.details = details or []
        self.extra = extra or {}

    def to_dict(self):
        body = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        body.update(self.extra)
        return {"error": body}


class ValidationError(ApiError):
    status = 400
    code = "VALIDATION_ERROR"


class InvalidRequestBody(ApiError):
    status = 400
    code = "INVALID_REQUEST_BODY"


class AccountNotFound(ApiError):
    status = 404
    code = "ACCOUNT_NOT_FOUND"


class InsufficientBalance(ApiError):
    status = 422
    code = "INSUFFICIENT_BALANCE"


class DuplicateTransaction(ApiError):
    status = 409
    code = "DUPLICATE_TRANSACTION"
