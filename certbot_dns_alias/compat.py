"""Centralize host compatibility while inheriting Certbot's public lifecycle.

DNSAuthenticator handles Certbot 3's domain and Certbot 5's identifier APIs.
Keep that behavior in the host instead of copying private implementations.
"""

import sys

from certbot.plugins.dns_common import DNSAuthenticator

if sys.version_info < (3, 10):
    # Python 3.9's stdlib entry_points() does not accept the group keyword.
    import importlib_metadata as metadata
else:
    from importlib import metadata


def certbot_major_version() -> int:
    """Read the actual installed host version."""
    return int(metadata.version("certbot").split(".", 1)[0])


__all__ = ["DNSAuthenticator", "certbot_major_version", "metadata"]
