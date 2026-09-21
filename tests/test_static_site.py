"""The HTTPS console proxy: files from the bucket, SPA fallback, binary safety."""

from __future__ import annotations

import base64
import json
from typing import Any

import boto3
import pytest
from moto import mock_aws

from beacon import static_site

BUCKET = "beacon-console-test"


@pytest.fixture()
def bucket(monkeypatch: Any) -> Any:
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("CONSOLE_BUCKET", BUCKET)
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key="index.html", Body=b"<html>beacon</html>")
        s3.put_object(Bucket=BUCKET, Key="assets/app.js", Body=b"console.log(1)")
        s3.put_object(Bucket=BUCKET, Key="config.json", Body=b'{"voiceUrl":"x"}')
        s3.put_object(Bucket=BUCKET, Key="favicon.png", Body=b"\x89PNG\r\n\x1a\n")
        yield s3


def _get(path: str) -> dict[str, Any]:
    return static_site.handler(
        {"rawPath": path, "requestContext": {"http": {"method": "GET"}}}, None
    )


def test_root_serves_index_with_no_cache_and_security_headers(bucket: Any) -> None:
    r = _get("/")
    assert r["statusCode"] == 200 and r["body"] == "<html>beacon</html>"
    assert r["headers"]["content-type"].startswith("text/html")
    assert "no-store" in r["headers"]["cache-control"]
    assert r["headers"]["strict-transport-security"].startswith("max-age=")
    assert r["isBase64Encoded"] is False


def test_assets_are_immutable_and_js_typed(bucket: Any) -> None:
    r = _get("/assets/app.js")
    assert r["headers"]["content-type"].startswith("application/javascript")
    assert "immutable" in r["headers"]["cache-control"]


def test_config_json_is_never_cached(bucket: Any) -> None:
    r = _get("/config.json")
    assert json.loads(r["body"])["voiceUrl"] == "x"
    assert "no-store" in r["headers"]["cache-control"]


def test_binary_files_are_base64(bucket: Any) -> None:
    r = _get("/favicon.png")
    assert r["isBase64Encoded"] is True
    assert base64.b64decode(r["body"]).startswith(b"\x89PNG")


def test_unknown_routes_fall_back_to_the_spa_shell_but_missing_files_404(
    bucket: Any,
) -> None:
    assert _get("/analytics")["body"] == "<html>beacon</html>"
    assert _get("/assets/missing.js")["statusCode"] == 404
