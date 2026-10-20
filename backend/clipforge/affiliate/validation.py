"""Affiliate link checks (spec link security): https only, known host, never fetched."""

from collections.abc import Sequence

import httpx

from clipforge.security.url_guard import host_allowed
from clipforge.settings_schema import hostname_entry


class LinkRejected(ValueError):
    """The message never echoes the URL: its query may hold tracking tokens."""


def destination_host(domain: str) -> str:
    try:
        host = hostname_entry(domain)
    except ValueError:
        raise LinkRejected("destination_domain must be a plain hostname") from None
    if host.startswith("*."):
        raise LinkRejected("destination_domain must be a plain hostname")
    return host


def check_affiliate_url(url: str, destination_domain: str, merchant_domains: Sequence[str]) -> str:
    """Returns the URL unchanged, tracking parameters included, or raises LinkRejected."""
    try:
        parsed = httpx.URL(url.strip())
    except httpx.InvalidURL:
        raise LinkRejected("affiliate_url is not a valid URL") from None
    if parsed.scheme != "https":
        raise LinkRejected("affiliate_url must use https://")
    if parsed.userinfo or parsed.port not in (None, 443):
        raise LinkRejected("affiliate_url must not carry credentials or a port")
    try:
        host = hostname_entry(parsed.host)
    except ValueError:
        raise LinkRejected("affiliate_url host must be a public hostname, not an IP") from None
    if host != destination_host(destination_domain) and not host_allowed(host, merchant_domains):
        raise LinkRejected(
            "affiliate_url host must equal destination_domain or match a merchant domain"
        )
    return url.strip()
