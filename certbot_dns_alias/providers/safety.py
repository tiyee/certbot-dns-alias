"""Keep sensitive SDK diagnostics out of the host's logs and tracebacks."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from certbot import errors

_sdk_call_active: ContextVar[bool] = ContextVar("dns_alias_sdk_call_active", default=False)
_SDK_LOG_PREFIXES = (
    "alibabacloud-tea",
    "darabonba-core",
    "tencentcloud_sdk_common",
    "cloudflare",
    "httpx",
    "httpcore",
    "urllib3",
)


def _sdk_logger(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in _SDK_LOG_PREFIXES)


class _SDKLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not (_sdk_call_active.get() and _sdk_logger(record.name))


_LOG_FILTER = _SDKLogFilter()


@contextmanager
def quiet_sdk_call() -> Iterator[None]:
    """Suppress SDK/transport diagnostics only in this operation's context.

    SDK DEBUG logs include credentials and request bodies. Filter before records
    reach Certbot's buffered/file handlers, without changing logger levels or
    suppressing other threads' requests. Handler filters also cover child loggers
    initialized lazily by a transport during the call.
    """
    loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.copy().values()]
    for logger in loggers:
        if isinstance(logger, logging.Logger):
            if _sdk_logger(logger.name):
                logger.addFilter(_LOG_FILTER)
            if logger is logging.getLogger() or _sdk_logger(logger.name):
                for handler in logger.handlers:
                    handler.addFilter(_LOG_FILTER)
    token = _sdk_call_active.set(True)
    try:
        yield
    finally:
        _sdk_call_active.reset(token)


class ProviderAPIError(errors.PluginError):
    """Retain a safe API code without retaining the SDK exception as a cause."""

    def __init__(self, provider: str, operation: str, code: object) -> None:
        self.code = (
            code
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code)
            else "UnknownError"
        )
        super().__init__(f"{provider} {operation} failed ({self.code})")
