"""HTTP request/response trace logging middleware and credential redaction helpers."""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiohttp import web
from yarl import URL

from .config import ServerConfig

logger = logging.getLogger(__name__)

TRUNCATE_BODY_LOG = 10 * 1024

REDACTED_VALUE = "***"
REDACTED_HEADER_KEYS = frozenset(
    {"x-access-token", "authorization", "cookie", "set-cookie"}
)
REDACTED_URL_QUERY_KEYS = frozenset(
    {"signature", "token", "access_token", "refresh_token", "client_secret", "code"}
)
REDACTED_BODY_KEYS = frozenset(
    {
        "password",
        "oldpassword",
        "newpassword",
        "token",
        "accesstoken",
        "access_token",
        "refreshtoken",
        "refresh_token",
        "client_secret",
        "authorization",
        "randomcode",
        "random_code",
        "code",
        "code_verifier",
    }
)
_REDACTED_KEYS_PATTERN = "|".join(re.escape(k) for k in sorted(REDACTED_BODY_KEYS))
_JSON_STRING_SECRET_RE = re.compile(
    rf'(?i)("({_REDACTED_KEYS_PATTERN})"\s*:\s*)"[^"]*"'
)
_FORM_SECRET_RE = re.compile(rf"(?i)((?:^|&)(?:{_REDACTED_KEYS_PATTERN})=)[^&\s]*")


async def _write_trace_log(config: ServerConfig, log_entry: dict[str, Any]) -> None:
    """Write a trace log entry to the configured trace file."""
    if not config.trace_log_file:
        return

    trace_log_path = Path(config.trace_log_file)

    def write_op() -> None:
        try:
            trace_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(trace_log_path, "a") as f:
                f.write(json.dumps(log_entry, indent=2) + "\n")
                f.flush()
        except Exception as e:
            logger.error(f"Failed to write to trace log: {e}")

    await asyncio.to_thread(write_op)


def try_parse_json(body: str | None) -> Any:
    """Attempt to parse string as JSON, returning original if parsing fails."""
    if not isinstance(body, str):
        return body
    try:
        return json.loads(body)
    except Exception:
        return body


def is_binary_content_type(content_type: str) -> bool:
    """Check if content type is likely binary."""
    binary_types = [
        "application/octet-stream",
        "application/pdf",
        "application/zip",
        "image/",
        "audio/",
        "video/",
    ]
    return any(t in content_type for t in binary_types)


def sanitize_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """Redact authentication and cookie headers case-insensitively."""
    return {
        k: (REDACTED_VALUE if k.lower() in REDACTED_HEADER_KEYS else v)
        for k, v in headers.items()
    }


def redact_url(url: Any) -> str:
    """Redact sensitive query parameters from a URL."""
    url_str = str(url)
    if not any(f"{key}=" in url_str for key in REDACTED_URL_QUERY_KEYS):
        return url_str

    try:
        u = URL(url_str)
        query = u.query.copy()
        redacted = False
        for key in list(query.keys()):
            if key.lower() in REDACTED_URL_QUERY_KEYS:
                query[key] = REDACTED_VALUE
                redacted = True
        return str(u.with_query(query)) if redacted else url_str
    except Exception:
        return url_str


def sanitize_body(body: Any) -> Any:
    """Recursively redact sensitive credentials and tokens from parsed or raw bodies."""
    if isinstance(body, dict):
        return {
            k: (
                REDACTED_VALUE
                if isinstance(k, str)
                and k.lower() in REDACTED_BODY_KEYS
                and v is not None
                else sanitize_body(v)
            )
            for k, v in body.items()
        }
    if isinstance(body, list):
        return [sanitize_body(item) for item in body]
    if isinstance(body, str):
        if body.startswith(("http://", "https://", "/")):
            return redact_url(body)
        redacted = _JSON_STRING_SECRET_RE.sub(rf'\1"{REDACTED_VALUE}"', body)
        return _FORM_SECRET_RE.sub(rf"\1{REDACTED_VALUE}", redacted)
    return body


@web.middleware
async def trace_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Middleware that writes redacted HTTP request and response traces to disk."""
    try:
        response = await handler(request)
        # Capture Request Body (SAFELY AFTER HANDLER)
        req_body_str = None
        if "/api/oss/upload" in request.path:
            req_body_str = "<multipart upload skipped>"
        elif request.body_exists and not request.content_type.startswith("multipart/"):
            try:
                # aiohttp allows reading multiple times once buffered
                body_bytes = await request.read()
                req_body_str = body_bytes.decode("utf-8", errors="replace")
                if len(req_body_str) > TRUNCATE_BODY_LOG:
                    req_body_str = req_body_str[:2048] + "... (truncated)"
            except Exception:
                req_body_str = "<error reading body>"

        # Capture Response Body
        res_body_str = None
        if isinstance(response, web.Response) and response.body:
            if is_binary_content_type(response.content_type):
                res_body_str = "<binary data>"
            else:
                try:
                    if isinstance(response.body, bytes):
                        res_body_str = response.body.decode("utf-8", errors="replace")
                        if len(res_body_str) > TRUNCATE_BODY_LOG:
                            res_body_str = res_body_str[:2048] + "... (truncated)"
                except Exception:
                    res_body_str = "<error reading response>"

        # Write Log
        log_entry = {
            "timestamp": time.time(),
            "request": {
                "method": request.method,
                "url": str(redact_url(request.url)),
                "headers": sanitize_headers(dict(request.headers)),
                "body": sanitize_body(try_parse_json(req_body_str)),
            },
            "response": {
                "status": response.status,
                "headers": sanitize_headers(dict(response.headers)),
                "body": sanitize_body(try_parse_json(res_body_str)),
            },
        }
        await _write_trace_log(request.app["config"], log_entry)
        return response

    except Exception as e:
        logger.exception(f"Error handling request: {e}")
        # Try to capture body even on error
        req_body_str = "<unknown>"
        try:
            if request.body_exists:
                body_bytes = await request.read()
                req_body_str = body_bytes.decode("utf-8", errors="replace")
        except Exception:
            pass

        log_entry = {
            "timestamp": time.time(),
            "request": {
                "method": request.method,
                "url": str(redact_url(request.url)),
                "headers": sanitize_headers(dict(request.headers)),
                "body": sanitize_body(try_parse_json(req_body_str)),
            },
            "error": str(e),
            "status": 500,
        }
        await _write_trace_log(request.app["config"], log_entry)
        raise
