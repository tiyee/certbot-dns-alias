"""DNS name handling and bounded CNAME resolution."""

from __future__ import annotations

import logging
import re
import time

import dns.exception
import dns.name
import dns.rdatatype
import dns.resolver
from certbot import errors

logger = logging.getLogger(__name__)


def normalize_name(value: str) -> str:
    """Return an absolute, lower-case ASCII DNS name without its trailing dot."""
    try:
        name = dns.name.from_text(value.strip()).canonicalize()
        if name == dns.name.root or any(
            not re.fullmatch(rb"[a-z0-9_-]+", label) for label in name.labels[:-1]
        ):
            raise ValueError("invalid label")
        return name.to_text(omit_final_dot=True)
    except (dns.exception.DNSException, UnicodeError, ValueError) as exc:
        raise errors.PluginError(f"Invalid DNS name: {value!r}") from exc


def relative_name(fqdn: str, zone: str) -> str:
    """Return a provider's host record, using @ for the zone apex."""
    fqdn, zone = normalize_name(fqdn), normalize_name(zone)
    if fqdn == zone:
        return "@"
    if fqdn.endswith("." + zone):
        return fqdn[: -len(zone) - 1]
    raise errors.PluginError(f"DNS name {fqdn} is outside zone {zone}")


class CnameResolver:
    """Follow individual CNAME links, including links in negative DNS answers."""

    def __init__(
        self,
        resolver: dns.resolver.Resolver | None = None,
        *,
        max_depth: int = 8,
        timeout: float = 10,
        retries: int = 2,
    ) -> None:
        if max_depth < 1 or timeout <= 0 or retries < 0:
            raise errors.PluginError("Invalid CNAME resolver limits")
        self.resolver = resolver if resolver is not None else dns.resolver.Resolver()
        self.max_depth = max_depth
        self.timeout = timeout
        self.retries = retries

    def resolve(self, fqdn: str, *, require_cname: bool = False) -> str:
        original = current = normalize_name(fqdn)
        visited = {current}
        # One terminal query beyond the allowed number of links is necessary:
        # a chain of exactly max_depth links must still be accepted.
        for depth in range(self.max_depth + 1):
            target = self._target(current)
            if target is None:
                if require_cname and current == original:
                    raise errors.PluginError(f"CNAME delegation required for {original}")
                return current
            if target in visited:
                raise errors.PluginError(f"CNAME loop detected for {original} at {target}")
            if depth == self.max_depth:
                raise errors.PluginError(
                    f"CNAME chain exceeds {self.max_depth} links for {original}"
                )
            logger.debug("CNAME delegation: %s -> %s", current, target)
            visited.add(target)
            current = target
        raise AssertionError("unreachable")

    def _target(self, current: str) -> str | None:
        for attempt in range(self.retries + 1):
            try:
                answer = self.resolver.resolve(
                    current + ".",
                    "CNAME",
                    lifetime=self.timeout,
                    search=False,
                    raise_on_no_answer=False,
                )
                responses = [answer.response]
            except dns.resolver.NXDOMAIN as exc:
                # NXDOMAIN can refer to the CNAME's destination rather than
                # the queried name. Preserve the CNAME in the answer section.
                responses = list(exc.responses().values())
            except dns.resolver.NoAnswer as exc:
                responses = [exc.response()]
            except (dns.exception.Timeout, dns.resolver.NoNameservers) as exc:
                if attempt == self.retries:
                    raise errors.PluginError(
                        f"Unable to resolve CNAME for {current} after {attempt + 1} attempts "
                        f"({type(exc).__name__}); check DNS connectivity and resolvers"
                    ) from exc
                time.sleep(min(2**attempt, 4))
                continue
            except dns.exception.DNSException as exc:
                raise errors.PluginError(f"CNAME lookup failed for {current}") from exc

            owner = dns.name.from_text(current + ".")
            for response in responses:
                for rrset in response.answer:
                    if rrset.name == owner and rrset.rdtype == dns.rdatatype.CNAME:
                        if len(rrset) != 1:
                            raise errors.PluginError(f"Invalid CNAME RRset for {current}")
                        return normalize_name(rrset[0].target.to_text())
            return None
        raise AssertionError("unreachable")
