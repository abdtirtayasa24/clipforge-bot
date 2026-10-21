from collections.abc import Iterable
from typing import Any

import httpcore
import httpx
import pytest

from clipforge.security.url_guard import (
    GuardedBackend,
    GuardedTransport,
    UrlRejected,
    check_url,
    host_allowed,
    is_public_address,
    is_youtube_host,
)

pytestmark = pytest.mark.security

ALLOW = ["media.example.com", "*.cdn.example.org"]


def resolver(*addresses: str) -> Any:
    async def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "https://media.example.com/video.mp4",
        "https://MEDIA.example.com./video.mp4",
        "https://media.example.com:443/v.mp4?token=abc",
        "https://edge.cdn.example.org/v.mp4",
        "https://a.b.cdn.example.org/v.mp4",
    ],
)
def test_allowlisted_https_urls_pass(url: str) -> None:
    assert check_url(url, ALLOW).scheme == "https"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://media.example.com/v.mp4", "https"),
        ("file:///etc/passwd", "https"),
        ("ftp://media.example.com/v.mp4", "https"),
        ("https://user:pass@media.example.com/v.mp4", "credentials"),
        ("https://media.example.com:8443/v.mp4", "port"),
        ("https://127.0.0.1/v.mp4", "permitted"),
        ("https://10.0.0.5/v.mp4", "permitted"),
        ("https://[::1]/v.mp4", "permitted"),
        ("https://169.254.169.254/latest/meta-data", "permitted"),
        ("https://other.example.com/v.mp4", "permitted"),
        ("https://cdn.example.org/v.mp4", "permitted"),
        ("https://cdn.example.org.evil.net/v.mp4", "permitted"),
        ("https://evilcdn.example.org/v.mp4", "permitted"),
        ("https://www.youtube.com/watch?v=abc", "YouTube"),
        ("https://youtu.be/abc", "YouTube"),
        ("https://m.youtube.com/watch?v=abc", "YouTube"),
        ("https://rr1---sn-abc.googlevideo.com/videoplayback", "YouTube"),
        ("not a url", "https"),
        ("https:///v.mp4", "host"),
    ],
)
def test_unsafe_or_unpermitted_urls_are_rejected(url: str, reason: str) -> None:
    with pytest.raises(UrlRejected, match=reason):
        check_url(url, ALLOW)


def test_youtube_is_rejected_even_when_allowlisted() -> None:
    with pytest.raises(UrlRejected, match="YouTube"):
        check_url("https://www.youtube.com/watch?v=abc", ["www.youtube.com", "*.youtube.com"])


def test_empty_allowlist_disables_url_ingestion() -> None:
    with pytest.raises(UrlRejected, match="disabled"):
        check_url("https://media.example.com/v.mp4", [])


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("cdn.example.com", True),
        ("a.cdn.example.com", True),
        ("example.com", False),
        ("example.com.evil.net", False),
        ("badexample.com", False),
    ],
)
def test_wildcard_matches_subdomains_only(host: str, allowed: bool) -> None:
    assert host_allowed(host, ["*.example.com"]) is allowed


@pytest.mark.parametrize(
    ("host", "youtube"),
    [
        ("youtube.com", True),
        ("www.youtube.com", True),
        ("youtu.be", True),
        ("rr3---sn-x.googlevideo.com", True),
        ("youtube-nocookie.com", True),
        ("notyoutube.com", False),
        ("youtube.com.evil.net", False),
    ],
)
def test_youtube_hosts(host: str, youtube: bool) -> None:
    assert is_youtube_host(host) is youtube


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104 - test data, nothing binds
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_non_public_addresses(address: str) -> None:
    assert not is_public_address(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses(address: str) -> None:
    assert is_public_address(address)


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.hosts: list[str] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.hosts.append(host)
        return httpcore.AsyncMockStream([])


@pytest.mark.parametrize(
    "addresses", [("127.0.0.1",), ("8.8.8.8", "10.0.0.1"), ("169.254.169.254",), ()]
)
async def test_backend_refuses_hosts_resolving_to_non_public_addresses(
    addresses: tuple[str, ...],
) -> None:
    inner = RecordingBackend()
    backend = GuardedBackend(resolve=resolver(*addresses), inner=inner)

    with pytest.raises(UrlRejected):
        await backend.connect_tcp("media.example.com", 443)

    assert inner.hosts == []


async def test_backend_connects_to_the_vetted_address() -> None:
    inner = RecordingBackend()
    backend = GuardedBackend(resolve=resolver("8.8.8.8"), inner=inner)

    await backend.connect_tcp("media.example.com", 443)

    assert inner.hosts == ["8.8.8.8"]


async def test_transport_applies_the_guard_on_every_connection() -> None:
    transport = GuardedTransport(resolve=resolver("127.0.0.1"))

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UrlRejected):
            await client.get("https://media.example.com/v.mp4")
