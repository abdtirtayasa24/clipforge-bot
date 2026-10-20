"""Copyable caption/description draft (spec clip output contract); publishing stays manual."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ExportLink:
    name: str
    merchant: str
    url: str
    is_primary: bool


@dataclass(frozen=True)
class CaptionDraft:
    text: str
    links: list[ExportLink]
    disclosure: str | None


def caption_draft(
    *,
    title: str,
    hook_line: str,
    description: str,
    links: Sequence[ExportLink],
    disclosure: str,
    affiliate_enabled: bool,
) -> CaptionDraft:
    """`links` must be approved links only; with the toggle off none are exported.

    Products are "related", never "featured": they are recommendations, not in the clip.
    """
    exported = sorted(links, key=lambda link: not link.is_primary) if affiliate_enabled else []
    blocks = [block for block in (title, hook_line, description) if block]
    if exported:
        lines = [f"- {link.name} ({link.merchant}): {link.url}" for link in exported]
        blocks += ["Related products:\n" + "\n".join(lines), disclosure]
    return CaptionDraft(
        text="\n\n".join(blocks), links=exported, disclosure=disclosure if exported else None
    )
