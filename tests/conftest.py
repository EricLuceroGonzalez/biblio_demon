"""Fixtures compartidas por todos los tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from biblio_demon.config import Settings
from biblio_demon.models import Author, PaperMetadata

FILLER = ["Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do."] * 6


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        inbox_path=inbox,
        archive_path=tmp_path / "archive",
        log_dir=tmp_path / "logs",
        crossref_email="test@example.org",
        http_max_attempts=2,
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def make_pdf() -> Callable[..., Path]:
    """Crea un PDF de una o varias páginas con las líneas indicadas."""

    def _make(
        path: Path,
        *pages: list[str],
        link: str | None = None,
        xmp_doi: str | None = None,
    ) -> Path:
        doc = pymupdf.open()
        for lines in pages or ([],):
            page = doc.new_page()
            y = 72
            for line in lines:
                page.insert_text((50, y), line, fontsize=10)
                y += 14
        if link:
            doc[0].insert_link(
                {
                    "kind": pymupdf.LINK_URI,
                    "from": pymupdf.Rect(50, 700, 300, 720),
                    "uri": link,
                }
            )
        if xmp_doi:
            doc.set_xml_metadata(
                '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
                'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                '<rdf:Description xmlns:prism="http://prismstandard.org/namespaces/'
                f'basic/2.0/"><prism:doi>{xmp_doi}</prism:doi></rdf:Description>'
                "</rdf:RDF></x:xmpmeta>"
            )
        doc.save(path)
        doc.close()
        return path

    return _make


@pytest.fixture
def resnet_meta() -> PaperMetadata:
    return PaperMetadata(
        doi="10.1109/cvpr.2016.90",
        title="Deep Residual Learning for Image Recognition",
        creators=[
            Author(family="He", given="Kaiming"),
            Author(family="Zhang", given="Xiangyu"),
            Author(family="Ren", given="Shaoqing"),
        ],
        year=2016,
        venue="2016 IEEE Conference on Computer Vision and Pattern Recognition",
        item_type="conferencePaper",
        source="crossref",
    )
