import os
import time
import uuid

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

from .db import init_db
from .errors import ApiError
from .routes import bp


def create_app(database=None):
    app = Flask(__name__)
    app.config["DATABASE"] = database or os.environ.get("LEDGER_DB", "ledger.db")
    app.json.sort_keys = False
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # request bodies here are tiny; reject anything larger
    init_db(app.config["DATABASE"])
    app.register_blueprint(bp)

    @app.before_request
    def start_request():
        g.rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        g.t0 = time.perf_counter()

    @app.after_request
    def finish_request(resp):
        resp.headers["X-Request-ID"] = g.get("rid", "-")
        if not app.testing:
            ms = (time.perf_counter() - g.get("t0", time.perf_counter())) * 1000
            app.logger.info("%s %s %s %s %.1fms", g.get("rid"), request.method, request.path, resp.status_code, ms)
        return resp

    @app.errorhandler(ApiError)
    def handle_api_error(err):
        return jsonify(err.to_dict()), err.status

    @app.errorhandler(HTTPException)
    def handle_http_error(err):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE"}.get(err.code, "HTTP_ERROR")
        return jsonify({"error": {"code": code, "message": err.description}}), err.code

    @app.errorhandler(Exception)
    def handle_unexpected(err):
        app.logger.exception("Unhandled error")
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}}), 500

    return app
