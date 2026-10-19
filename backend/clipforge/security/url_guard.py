"""Permitted-URL checks and an SSRF-safe HTTP transport for media downloads (ADR-018)."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

import httpcore
import httpx

YOUTUBE_DOMAINS = ("youtube.com", "youtu.be", "googlevideo.com", "youtube-nocookie.com")
HTTPS_PORT = 443

Resolver = Callable[[str, int], Awaitable[list[str]]]


class UrlRejected(Exception):
    pass


def _normalize_host(host: str) -> str:
    return host.lower().rstrip(".")


def is_youtube_host(host: str) -> bool:
    host = _normalize_host(host)
    return any(host == domain or host.endswith(f".{domain}") for domain in YOUTUBE_DOMAINS)


def host_allowed(host: str, allowlist: Iterable[str]) -> bool:
    """Exact hosts, or `*.suffix` entries that match subdomains of the suffix only."""
    host = _normalize_host(host)
    for entry in allowlist:
        entry = _normalize_host(entry)
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and len(host) > len(entry) - 1:
                return True
        elif host == entry:
            return True
    return False


def check_url(url: str, allowlist: Sequence[str]) -> httpx.URL:
    """Validates a source URL or redirect target; raises UrlRejected with the reason."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise UrlRejected("not a valid URL") from None
    if parsed.scheme != "https":
        raise UrlRejected("only https:// URLs are allowed")
    if parsed.userinfo:
        raise UrlRejected("URLs with embedded credentials are not allowed")
    if parsed.port not in (None, HTTPS_PORT):
        raise UrlRejected("only the default https port is allowed")
    if not parsed.host:
        raise UrlRejected("URL has no host")
    if is_youtube_host(parsed.host):
        raise UrlRejected("YouTube URLs cannot be ingested; upload authorized files instead")
    if not allowlist:
        raise UrlRejected("URL ingestion is disabled until permitted_download_domains is set")
    if not host_allowed(parsed.host, allowlist):
        raise UrlRejected(f"host {parsed.host} is not in permitted_download_domains")
    return parsed


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve_host(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves each connection itself and only dials public addresses.

    Checking at connect time, not before the request, closes the DNS-rebinding window; TLS still
    verifies the certificate against the URL's hostname.
    """

    def __init__(
        self,
        *,
        resolve: Resolver = resolve_host,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._resolve = resolve
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await self._resolve(host, port)
        except OSError:
            raise UrlRejected(f"cannot resolve {host}") from None
        if not addresses or not all(is_public_address(address) for address in addresses):
            raise UrlRejected(f"{host} does not resolve to public addresses only")
        return await self._inner.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *, resolve: Resolver = resolve_host) -> None:
        super().__init__(trust_env=False)
        # httpx (pinned) exposes no network-backend option, so the pool is replaced.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=2,
            network_backend=GuardedBackend(resolve=resolve),
        )
