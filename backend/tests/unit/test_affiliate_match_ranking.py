import uuid
from decimal import Decimal

from clipforge.affiliate.matcher import ClipText, Product, cosine, rank

CLIP = ClipText(
    title="Best trail running shoes for beginners",
    topic="trail running gear",
    keywords=["running shoes", "trail"],
    transcript="I ran fifty kilometers in these shoes and my feet were fine",
    language="id",
)


def product(name: str, tags: list[str], **overrides: object) -> Product:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": name,
        "merchant": "Shop",
        "category": None,
        "tags": tags,
        "language_code": None,
        "region_code": None,
    }
    return Product(**(values | overrides))  # type: ignore[arg-type]


def test_relevant_product_is_suggested_and_unrelated_excluded() -> None:
    shoes = product("Trail shoes", ["running shoes", "trail"])
    lamp = product("Desk lamp", ["lighting", "desk"])

    [suggestion] = rank([lamp, shoes], CLIP, region="ID", threshold=0.7)

    assert suggestion.product_id == shoes.id
    assert suggestion.score == Decimal("1.0")
    assert suggestion.is_primary
    assert suggestion.reason.startswith("Related")
    assert "featured" not in suggestion.reason.lower()


def test_no_match_gives_no_suggestions() -> None:
    assert rank([product("Desk lamp", ["lighting"])], CLIP, region="ID", threshold=0.7) == []
    assert rank([], CLIP, region="ID", threshold=0.7) == []


def test_language_and_region_filters() -> None:
    wrong_language = product("Shoes EN", ["running shoes"], language_code="en")
    wrong_region = product("Shoes US", ["running shoes"], region_code="US")
    same = product("Shoes ID", ["running shoes"], language_code="id-ID", region_code="ID")

    suggestions = rank([wrong_language, wrong_region, same], CLIP, region="ID", threshold=0.7)

    assert [s.product_id for s in suggestions] == [same.id]


def test_partial_keyword_overlap_falls_below_the_threshold() -> None:
    socks = product("Socks", ["running", "socks", "wool", "winter"])

    assert rank([socks], CLIP, region="ID", threshold=0.7) == []
    assert rank([socks], CLIP, region="ID", threshold=0.25)[0].score == Decimal("0.25")


def test_at_most_three_ranked_by_similarity_with_one_primary() -> None:
    products = [product(f"Shoe {i}", ["trail"]) for i in range(5)]
    similarities = {p.id: 0.71 + i / 100 for i, p in enumerate(products)}

    suggestions = rank(products, CLIP, region="ID", threshold=0.7, similarities=similarities)

    assert [s.product_id for s in suggestions] == [p.id for p in reversed(products[2:])]
    assert [s.is_primary for s in suggestions] == [True, False, False]
    assert "semantic similarity 0.75" in suggestions[0].reason


def test_embedding_similarity_below_threshold_excludes_tag_matches() -> None:
    shoes = product("Trail shoes", ["trail"])

    assert rank([shoes], CLIP, region="ID", threshold=0.7, similarities={shoes.id: 0.4}) == []


def test_cosine() -> None:
    assert cosine([1, 0], [1, 0]) == 1
    assert cosine([1, 0], [0, 1]) == 0
    assert cosine([0, 0], [1, 1]) == 0
