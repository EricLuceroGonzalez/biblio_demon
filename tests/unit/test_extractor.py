from pathlib import Path

import pytest

from biblio_demon.extractor import (
    PdfReadError,
    extract_identifiers,
    find_arxiv_ids,
    find_dois,
    sanitize_doi,
)

FILLER = ["Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do."] * 6


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1109/CVPR.2016.90.", "10.1109/cvpr.2016.90"),
        ("10.1000/xyz123),", "10.1000/xyz123"),
        ("10.1002/(SICI)1097-4636(199601)30:1<", None),  # '<' no es válido
        ("10.1002/(SICI)1097-4636(199601)30", "10.1002/(sici)1097-4636(199601)30"),
        ("10.1016/j.cell.2020.01.001;", "10.1016/j.cell.2020.01.001"),
        ("10.1000%2Fabc", "10.1000/abc"),
        ("10.12/abc", None),  # registrante demasiado corto
    ],
)
def test_sanitize_doi(raw: str, expected: str | None) -> None:
    assert sanitize_doi(raw) == expected


def test_find_dois_in_parentheses_and_urls() -> None:
    text = "See (doi:10.1000/abc.def). Also https://doi.org/10.5555/XYZ-1"
    assert find_dois(text) == ["10.1000/abc.def", "10.5555/xyz-1"]


def test_find_dois_joins_broken_lines() -> None:
    text = "DOI: 10.1016/j.cell.\n2020.01.001\nReceived 2019"
    dois = find_dois(text)
    assert dois[0] == "10.1016/j.cell"  # tal cual primero
    assert "10.1016/j.cell.2020.01.001" in dois


def test_find_arxiv_ids() -> None:
    text = "arXiv:1706.03762v7 [cs.CL] and arxiv.org/abs/hep-th/9901001"
    assert find_arxiv_ids(text) == ["1706.03762", "hep-th/9901001"]


def test_extract_prioritises_first_page_over_references(tmp_path: Path, make_pdf):
    pdf = make_pdf(
        tmp_path / "a.pdf",
        ["My Title", *FILLER, "doi:10.1000/own"],
        [*FILLER],
        ["References", "[1] 10.2000/cited", "[2] 10.2000/cited"],
    )
    result = extract_identifiers(pdf)
    assert [c.doi for c in result.candidates] == ["10.1000/own", "10.2000/cited"]
    assert result.candidates[1].count == 2
    assert "My Title" in result.first_page_text


def test_extract_metadata_and_links_first(tmp_path: Path, make_pdf) -> None:
    pdf = make_pdf(
        tmp_path / "b.pdf",
        ["text 10.3000/text"],
        link="https://doi.org/10.4000/LINK",
        xmp_doi="10.5000/xmp",
    )
    result = extract_identifiers(pdf)
    assert [(c.doi, c.source) for c in result.candidates] == [
        ("10.5000/xmp", "metadata"),
        ("10.4000/link", "link"),
        ("10.3000/text", "text"),
    ]


def test_only_first_three_pages(tmp_path: Path, make_pdf) -> None:
    pdf = make_pdf(tmp_path / "c.pdf", ["x"], ["x"], ["x"], ["10.1000/page4"])
    assert not extract_identifiers(pdf).has_identifier


def test_corrupt_pdf(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(PdfReadError):
        extract_identifiers(bad)
