from biblio_demon.models import Author, PaperMetadata
from biblio_demon.naming import (
    build_filename,
    family_slug,
    sanitize_filename,
    title_slug,
    to_ascii,
)


def _meta(title: str, families: list[str], year: int | None = 2020) -> PaperMetadata:
    return PaperMetadata(
        doi="10.1000/x",
        title=title,
        creators=[Author(family=f) for f in families],
        year=year,
        source="crossref",
    )


def test_three_or_more_authors(resnet_meta) -> None:
    assert build_filename(resnet_meta) == (
        "(2016)-Deep_Residual_Learning_for_Image_Recognition-(He-EtAl).pdf"
    )


def test_one_and_two_authors() -> None:
    assert build_filename(_meta("Short title", ["Müller"])) == (
        "(2020)-Short_title-(Muller).pdf"
    )
    assert build_filename(_meta("Short", ["Ørsted", "van der Berg"])) == (
        "(2020)-Short-(Orsted-vanderBerg).pdf"
    )


def test_leading_stopwords_and_word_limit() -> None:
    title = "The Theory of Everything: A Unified Approach to Physics"
    assert title_slug(title, 6) == "Theory_of_Everything_A_Unified_Approach"
    assert title_slug(title, 3, drop_leading_stopwords=False) == "The_Theory_of"


def test_no_year_and_no_authors() -> None:
    assert build_filename(_meta("X", [], year=None)) == "(nd)-X-(Anon).pdf"


def test_ascii_and_sanitize() -> None:
    assert to_ascii("Gödel, Straße, Łukasz") == "Godel, Strasse, Lukasz"
    assert family_slug("O'Neil-Smith") == "ONeilSmith"
    assert sanitize_filename('a/b:c\\d?e*f"g') == "a_b_c_d_e_f_g"


def test_long_titles_are_truncated() -> None:
    meta = _meta(" ".join(["Supercalifragilisticexpialidocious"] * 20), ["He"])
    name = build_filename(meta, n_words=20)
    assert len(name.encode()) <= 200
    assert name.endswith("-(He).pdf")
