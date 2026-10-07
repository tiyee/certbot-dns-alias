"""Validate complete collections before routing zones or reusing TXT records."""

from __future__ import annotations

import re

from certbot import errors

from certbot_dns_alias.dns import normalize_name


class PageTracker:
    """Count unique, unfiltered items against a stable API total."""

    def __init__(self, label: str, page_size: int, *, fixed_pages: bool = False) -> None:
        self.label = label
        self.page_size = page_size
        self.fixed_pages = fixed_pages
        self.total: int | None = None
        self.seen: set[str] = set()

    def accept(self, total: object, identities: list[str]) -> bool:
        if type(total) is not int or total < 0:
            raise errors.PluginError(f"{self.label} returned an invalid pagination total")
        if self.total is not None and total != self.total:
            raise errors.PluginError(f"{self.label} changed its pagination total")
        self.total = total
        count = len(identities)
        remaining = total - len(self.seen)
        if count > self.page_size or count > remaining:
            raise errors.PluginError(f"{self.label} returned too many items")
        if (not count and remaining) or (
            self.fixed_pages and count != min(self.page_size, remaining)
        ):
            raise errors.PluginError(f"{self.label} returned an incomplete list")
        page = set(identities)
        if len(page) != count or self.seen.intersection(page):
            raise errors.PluginError(f"{self.label} returned duplicate items")
        self.seen.update(page)
        return len(self.seen) == total


def zone_identity(value: object) -> str:
    """Recognize case, trailing-dot and IDN variants as the same zone."""
    if isinstance(value, str):
        try:
            return normalize_name(value)
        except errors.PluginError:
            pass
    raise errors.PluginError("DNS provider returned an invalid zone identity")


def record_identity(value: object, *, integer: bool = False) -> str:
    """Validate the providers' positive numeric IDs without accepting booleans."""
    if type(value) is int and value > 0:
        return str(value)
    if not integer and isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        digits = value.lstrip("0")
        if digits:
            return digits
    raise errors.PluginError("DNS provider returned an invalid record ID")
