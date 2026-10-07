"""Check real SDKs at offline HTTP boundaries with Certbot's logging setup."""

from __future__ import annotations

import json
import logging
import shutil
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import requests
from certbot import errors
from certbot._internal import log as certbot_log
from cloudflare import Cloudflare

from certbot_dns_alias.providers.aliyun import AliyunDNSProvider
from certbot_dns_alias.providers.cloudflare import CloudflareDNSProvider
from certbot_dns_alias.providers.safety import quiet_sdk_call
from certbot_dns_alias.providers.tencent import TencentDNSProvider

SECRETS = ("ACCESS_ID_MARKER", "ACCESS_SECRET_MARKER", "SESSION_TOKEN_MARKER", "TXT_VALUE_MARKER")


@pytest.fixture
def certbot_logging(monkeypatch):
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(certbot_log.util, "atexit_register", lambda callback: None)
    certbot_log.pre_arg_parse_setup()
    memory = next(handler for handler in root.handlers if handler not in handlers)
    try:
        yield memory
    finally:
        for handler in list(root.handlers):
            if handler not in handlers:
                root.removeHandler(handler)
                handler.close()
        memory.target.close()
        shutil.rmtree(Path(memory.target.path).parent, ignore_errors=True)
        root.setLevel(level)


@pytest.fixture(params=["aliyun", "tencent", "cloudflare"])
def real_provider(request, monkeypatch):
    name = request.param
    replies = []
    calls = []
    monkeypatch.setenv("DEBUG", "sdk")
    monkeypatch.setenv("CLOUDFLARE_LOG", "debug")

    def response_for(call):
        calls.append(call)
        response = replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    if name == "cloudflare":

        def client(**kwargs):
            return Cloudflare(
                **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(response_for))
            )

        monkeypatch.setattr("certbot_dns_alias.providers.cloudflare.Cloudflare", client)
        provider = CloudflareDNSProvider(SECRETS[1], zone_ids={"example.com": "a" * 32})
    else:

        def send(session, prepared, **kwargs):
            return response_for(prepared)

        def sdk_request(session, *args, **kwargs):
            return response_for(kwargs)

        monkeypatch.setattr(requests.Session, "send", send)
        monkeypatch.setattr(requests.Session, "request", sdk_request)
        if name == "aliyun":
            provider = AliyunDNSProvider(SECRETS[0], SECRETS[1], security_token=SECRETS[2])
        else:
            provider = TencentDNSProvider(SECRETS[0], SECRETS[1], token=SECRETS[2])
    try:
        yield provider, replies, calls
    finally:
        if name == "cloudflare":
            provider.client.close()


def sdk_response(provider, operation, outcome):
    if outcome == "transport":
        exception = httpx.ReadTimeout if provider.name == "cloudflare" else requests.ReadTimeout
        return exception(" ".join(SECRETS))
    status = 403 if outcome == "http" else 200
    if provider.name == "cloudflare":
        body = {
            "success": outcome == "success",
            "errors": []
            if outcome == "success"
            else [{"code": 10000, "message": " ".join(SECRETS)}],
            "messages": [],
            "result": {
                "id": "123",
                "type": "TXT",
                "name": "host.example.com",
                "content": SECRETS[3],
            },
        }
        if operation in {"zones", "records"}:
            body["result"] = []
            body["result_info"] = {"page": 1, "count": 0, "total_count": 0}
        return httpx.Response(status, json=body)
    if outcome == "http":
        body = {"Code": "UnauthorizedOperation", "Message": " ".join(SECRETS)}
    elif provider.name == "aliyun":
        body = {
            "zones": {"TotalCount": 0, "PageNumber": 1, "PageSize": 100, "Domains": {"Domain": []}},
            "records": {
                "TotalCount": 0,
                "PageNumber": 1,
                "PageSize": 100,
                "DomainRecords": {"Record": []},
            },
            "create": {"RecordId": "123"},
            "delete": {"RecordId": "123"},
        }[operation]
    else:
        body = {
            "zones": {"DomainCountInfo": {"DomainTotal": 0}, "DomainList": []},
            "records": {"RecordCountInfo": {"TotalCount": 0, "ListCount": 0}, "RecordList": []},
            "create": {"RecordId": 123},
            "delete": {},
        }[operation]
    if provider.name == "tencent":
        if outcome == "http":
            body = {"Error": body}
        body = {"Response": {**body, "RequestId": "audit-id"}}
        # Tencent API failures normally arrive inside an HTTP 200 envelope.
        status = 200
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    response.headers["content-type"] = "application/json"
    response.raw = SimpleNamespace(version=11)
    response.reason = "Forbidden" if status == 403 else "OK"
    return response


@pytest.mark.parametrize("operation", ["zones", "records", "create", "delete"])
@pytest.mark.parametrize("outcome", ["success", "http", "transport"])
def test_real_sdk_diagnostics_are_private(
    real_provider, certbot_logging, caplog, operation, outcome
):
    provider, replies, calls = real_provider
    if provider.name == "cloudflare" and operation == "zones":
        provider._zone_ids = None
    replies.append(sdk_response(provider, operation, outcome))

    def invoke():
        if operation == "zones":
            return provider.list_zones()
        if operation == "records":
            return provider.list_txt_records("example.com", "host")
        if operation == "create":
            return provider.create_txt_record("example.com", "host", SECRETS[3], 600)
        return provider.delete_txt_record("example.com", "123")

    with caplog.at_level(logging.DEBUG):
        if outcome == "success":
            invoke()
        else:
            with pytest.raises(errors.PluginError) as caught:
                invoke()
            formatted = "".join(
                traceback.format_exception(
                    type(caught.value), caught.value, caught.value.__traceback__
                )
            )
            assert all(secret not in formatted for secret in SECRETS)
            logging.getLogger("certbot_dns_alias.tests").warning(
                "Safe provider error",
                exc_info=(type(caught.value), caught.value, caught.value.__traceback__),
            )
        logging.getLogger("certbot_dns_alias.tests").info("Host diagnostics remain available")
    # Flush after leaving the suppression context to exercise Certbot's buffering.
    certbot_logging.flush(force=True)
    certbot_logging.target.flush()
    contents = Path(certbot_logging.target.path).read_text()
    assert "Host diagnostics remain available" in contents
    assert all(secret not in contents + caplog.text for secret in SECRETS)
    assert len(calls) == 1


def test_suppression_is_scoped_and_preserves_other_threads(caplog):
    sdk = logging.getLogger("cloudflare._base_client")
    original_level = sdk.level
    with caplog.at_level(logging.DEBUG):
        sdk.debug("Before plugin operation")
        with quiet_sdk_call():
            sdk.debug("Sensitive plugin operation")
            with quiet_sdk_call():
                sdk.debug("Sensitive nested operation")
            logging.getLogger("certbot_dns_alias.tests").info("Unrelated host operation")
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(sdk.debug, "Another thread's operation").result()
        sdk.debug("After plugin operation")
    assert "Sensitive" not in caplog.text
    for message in ["Before", "After", "Unrelated host", "Another thread"]:
        assert message in caplog.text
    assert sdk.level == original_level


def test_sdk_handlers_and_lazily_created_transport_loggers_are_filtered(caplog):
    sdk = logging.getLogger("tencentcloud_sdk_common")
    buffer = StringIO()
    handler = logging.StreamHandler(buffer)
    sdk.addHandler(handler)
    try:
        with caplog.at_level(logging.DEBUG):
            with quiet_sdk_call():
                sdk.debug("Sensitive SDK-owned handler")
                logging.getLogger("httpcore.audit.lazy").debug("Sensitive lazy transport")
            sdk.debug("Normal SDK diagnostics")
        assert "Sensitive" not in buffer.getvalue() + caplog.text
        assert "Normal SDK diagnostics" in buffer.getvalue()
    finally:
        sdk.removeHandler(handler)
        handler.close()
