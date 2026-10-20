from clipforge.affiliate.caption import ExportLink, caption_draft

DISCLOSURE = "Affiliate link — I may earn a commission from qualifying purchases."
SHOES = ExportLink("Trail shoes", "Shop", "https://shop.example.com/p?tag=a-20", is_primary=False)
LAMP = ExportLink("Desk lamp", "Lights", "https://lights.example.com/l", is_primary=True)


def draft(links: list[ExportLink], *, enabled: bool = True) -> str:
    return caption_draft(
        title="My title",
        hook_line="Wait for it",
        description="From: Source video",
        links=links,
        disclosure=DISCLOSURE,
        affiliate_enabled=enabled,
    ).text


def test_caption_lists_approved_links_primary_first_with_disclosure() -> None:
    text = draft([SHOES, LAMP])

    assert text.startswith("My title\n\nWait for it\n\nFrom: Source video")
    assert text.index(LAMP.url) < text.index(SHOES.url)
    assert SHOES.url in text, "tracking parameters are kept"
    assert "Related products" in text
    assert "featured" not in text.lower()
    assert text.rstrip().endswith(DISCLOSURE)


def test_no_links_means_no_disclosure_and_no_link_section() -> None:
    result = caption_draft(
        title="My title",
        hook_line="",
        description="",
        links=[],
        disclosure=DISCLOSURE,
        affiliate_enabled=True,
    )

    assert result.text == "My title"
    assert result.links == []
    assert result.disclosure is None


def test_toggle_off_exports_no_affiliate_urls() -> None:
    result = caption_draft(
        title="My title",
        hook_line="Wait for it",
        description="",
        links=[SHOES],
        disclosure=DISCLOSURE,
        affiliate_enabled=False,
    )

    assert "https://" not in result.text
    assert DISCLOSURE not in result.text
    assert result.links == []
