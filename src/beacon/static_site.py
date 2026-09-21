"""Serve the console over HTTPS from a Lambda Function URL.

Accounts under AWS's new-account verification cannot create CloudFront
distributions, and S3 website hosting is HTTP only, which keeps the browser
from opening the microphone (no secure context). This handler proxies the
console bucket behind a Function URL, which is HTTPS out of the box.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from typing import Any

from botocore.exceptions import ClientError

from beacon import aws

_TEXT = ("text/", "application/json", "application/javascript", "image/svg+xml")
_NO_CACHE = ("index.html", "config.json")


def _content_type(key: str) -> str:
    guessed, _ = mimetypes.guess_type(key)
    if key.endswith(".js"):
        return "application/javascript"
    return guessed or "application/octet-stream"


def _response(status: int, body: bytes, content_type: str, key: str) -> dict[str, Any]:
    text = content_type.startswith(_TEXT)
    cache = (
        "no-cache, no-store, must-revalidate"
        if key in _NO_CACHE
        else "public, max-age=31536000, immutable"
    )
    return {
        "statusCode": status,
        "headers": {
            "content-type": content_type + ("; charset=utf-8" if text else ""),
            "cache-control": cache,
            "strict-transport-security": "max-age=63072000; includeSubDomains",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "strict-origin-when-cross-origin",
        },
        "body": body.decode() if text else base64.b64encode(body).decode(),
        "isBase64Encoded": not text,
    }


def _get(bucket: str, key: str) -> bytes | None:
    try:
        return bytes(aws.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read())
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    bucket = os.environ["CONSOLE_BUCKET"]
    path = str(event.get("rawPath") or "/").lstrip("/")
    key = path or "index.html"
    body = _get(bucket, key)
    if body is None and "." not in key.rsplit("/", 1)[-1]:
        key = "index.html"  # single-page app: unknown routes fall back to the shell
        body = _get(bucket, key)
    if body is None:
        return _response(404, b"not found", "text/plain", key)
    return _response(200, body, _content_type(key), key)
