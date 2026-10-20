import pytest

from clipforge.affiliate.validation import LinkRejected, check_affiliate_url, destination_host

TRACKED = "https://shop.example.com/p/123?tag=abc-20&utm_source=clipforge#frag"


def test_valid_url_is_returned_unchanged_with_tracking_params() -> None:
    assert check_affiliate_url(TRACKED, "shop.example.com", []) == TRACKED


def test_merchant_allowlist_admits_redirect_hosts() -> None:
    url = "https://go.partner.net/r/abc?sub=1"

    assert check_affiliate_url(url, "shop.example.com", ["*.partner.net"]) == url
    assert check_affiliate_url(url, "shop.example.com", ["go.partner.net"]) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.example.com/p/1",
        "javascript:alert(1)",
        "data:text/html,hi",
        "ftp://shop.example.com/p",
        "https://10.0.0.1/p",
        "https://127.0.0.1/p",
        "https://[::1]/p",
        "https://192.168.0.5/p",
        "https://8.8.8.8/p",
        "https://localhost/p",
        "https://user:pw@shop.example.com/p",
        "https://shop.example.com:8443/p",
        "https://other.example.com/p",
        "https://evil.com/shop.example.com",
        "https://shop.example.com.evil.com/p",
        "//shop.example.com/p",
        "shop.example.com/p",
        "https:///p",
        " ",
    ],
)
def test_unsafe_or_mismatched_urls_are_rejected(url: str) -> None:
    with pytest.raises(LinkRejected):
        check_affiliate_url(url, "shop.example.com", ["go.partner.net"])


@pytest.mark.parametrize(
    "domain", ["10.0.0.1", "localhost", "https://shop.example.com", "shop.example.com/x", "*.x.com"]
)
def test_destination_domain_must_be_a_plain_hostname(domain: str) -> None:
    with pytest.raises(LinkRejected):
        destination_host(domain)


def test_destination_domain_is_normalized() -> None:
    assert destination_host(" Shop.Example.COM. ") == "shop.example.com"
