"""Safe-outbound-network guard.

Every outbound HTTP request this project makes (market data, LLM calls) must go
through `validate_public_http_url` first. Rules:

- only http/https schemes are allowed;
- the host must not be localhost or resolve to loopback/private/reserved space.

This blocks accidental requests to internal infrastructure via malformed or
hostile config values (e.g. an LLM base_url pointing at 169.254.169.254).
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

_ALLOWED_SCHEMES = ("http", "https")

# transient upstream failures worth one more attempt before giving up
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_RETRY_DELAYS = (4.0, 10.0)


class UnsafeURL(ValueError):
    """Raised when a URL fails the outbound safety check."""


def validate_public_http_url(url: str) -> str:
    """Validate `url` and return it unchanged if it is safe to call.

    Raises UnsafeURL otherwise. DNS resolution is attempted to catch hostnames
    that point at private/loopback space; an unresolvable host is also rejected.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURL("empty URL")

    parsed = urlparse(url.strip())
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURL(f"scheme {parsed.scheme!r} not allowed (http/https only)")

    host = (parsed.hostname or "").strip()
    if not host:
        raise UnsafeURL("URL has no host")

    if host.lower() in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        raise UnsafeURL(f"host {host!r} is not a public host")

    try:
        addrinfos = socket.getaddrinfo(host, parsed.port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise UnsafeURL(f"cannot resolve host {host!r}: {exc}") from exc

    for info in addrinfos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeURL(f"host {host!r} resolves to non-public address {ip}")

    return url


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-runs the full public-host validation on every redirect hop.

    Blocks silent redirects into private/loopback space. Residual DNS-rebinding
    (IP changing between validation and connect) is out of scope for stdlib
    urllib; callers with hard requirements should pin resolved IPs themselves.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        try:
            validate_public_http_url(newurl)
        except UnsafeURL as exc:
            raise UnsafeURL(
                f"redirect to unsafe URL blocked: {newurl!r} ({exc})"
            ) from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(
    url: str,
    *,
    timeout: float = 30.0,
    allowed_hosts: tuple[str, ...] | set[str] | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 0,
):
    """Validate then open `url`; redirects are re-validated; optional host allowlist.

    Returns the response object from `opener.open(...)` — use as a context manager.
    `retries` re-attempts transient upstream failures (HTTP 5xx, network and
    timeout errors) with backoff so a one-cycle API blip does not kill a run;
    only a persistently failing endpoint still raises. Default 0 = old behavior.
    """
    validate_public_http_url(url)
    if allowed_hosts is not None:
        host = (urlparse(url).hostname or "").lower()
        allow = {h.lower() for h in allowed_hosts}
        if host not in allow:
            raise UnsafeURL(f"host {host!r} not in allowed hosts {sorted(allow)}")

    opener = urllib.request.build_opener(_ValidatingRedirectHandler())
    request = urllib.request.Request(url, data=data, headers=headers or {})
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(_RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)])
        try:
            return opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRYABLE_STATUS or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
    raise RuntimeError("unreachable")
