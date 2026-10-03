from pathlib import Path
from typing import Any

from biblio_demon.metadata import ResolutionError
from biblio_demon.models import PaperMetadata
from biblio_demon.pipeline import Outcome, Pipeline, title_score
from biblio_demon.state import StateStore
from biblio_demon.sync import SyncError

FILLER = ["Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do."] * 6
RESNET_PAGE = [
    "Deep Residual Learning for Image Re-",
    "cognition",
    "Kaiming He  Xiangyu Zhang  Shaoqing Ren",
    *FILLER,
    "doi: 10.1109/CVPR.2016.90",
]


class FakeResolver:
    def __init__(self, known: dict[str, PaperMetadata]) -> None:
        self.known = known
        self.calls: list[str] = []

    def resolve(self, doi: str) -> PaperMetadata:
        self.calls.append(doi)
        if doi in self.known:
            return self.known[doi]
        raise ResolutionError(doi, {"crossref": "no encontrado"})

    @staticmethod
    def arxiv_doi(arxiv_id: str) -> str:
        return f"10.48550/arxiv.{arxiv_id}"


class FakeZotero:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.synced: list[tuple[str, Path]] = []

    def sync(self, meta: PaperMetadata, pdf: Path) -> str:
        if self.fail:
            raise SyncError("Zotero: caído")
        self.synced.append((meta.doi, pdf))
        return "ZKEY1234"

    def build_item(self, meta: PaperMetadata) -> dict[str, Any]:
        return {"title": meta.title}


class FakeNotion:
    def __init__(self) -> None:
        self.synced: list[str] = []

    def sync(self, meta: PaperMetadata) -> str:
        self.synced.append(meta.doi)
        return "page-1"

    def build_properties(self, meta: PaperMetadata) -> dict[str, Any]:
        return {}


def _pipeline(settings, resnet_meta, zotero=None, notion=None) -> Pipeline:
    return Pipeline(
        settings,
        resolver=FakeResolver({resnet_meta.doi: resnet_meta}),  # type: ignore[arg-type]
        state=StateStore(settings.state_db),
        zotero_sync=zotero or FakeZotero(),  # type: ignore[arg-type]
        notion_sync=notion or FakeNotion(),  # type: ignore[arg-type]
    )


def test_happy_path(settings, resnet_meta, make_pdf) -> None:
    pdf = make_pdf(settings.inbox_path / "download.pdf", RESNET_PAGE)
    zotero, notion = FakeZotero(), FakeNotion()
    result = _pipeline(settings, resnet_meta, zotero, notion).process(pdf)

    assert result.outcome is Outcome.ARCHIVED
    expected = settings.archive_path / (
        "(2016)-Deep_Residual_Learning_for_Image_Recognition-(He-EtAl).pdf"
    )
    assert result.destination == expected and expected.exists()
    assert not pdf.exists()
    assert zotero.synced == [(resnet_meta.doi, expected)]
    assert notion.synced == [resnet_meta.doi]
    record = StateStore(settings.state_db).get_by_doi(resnet_meta.doi)
    assert record and record.zotero_key == "ZKEY1234"
    assert record.notion_page_id == "page-1"


def test_exact_duplicate(settings, resnet_meta, make_pdf, tmp_path) -> None:
    original = make_pdf(tmp_path / "orig.pdf", RESNET_PAGE)
    data = original.read_bytes()
    pipeline = _pipeline(settings, resnet_meta)
    (settings.inbox_path / "a.pdf").write_bytes(data)
    assert pipeline.process(settings.inbox_path / "a.pdf").outcome is Outcome.ARCHIVED
    (settings.inbox_path / "b.pdf").write_bytes(data)
    result = pipeline.process(settings.inbox_path / "b.pdf")
    assert result.outcome is Outcome.DUPLICATE
    assert (settings.duplicates_path / "b.pdf").exists()


def test_new_version_same_doi_gets_hash_suffix(settings, resnet_meta, make_pdf):
    pipeline = _pipeline(settings, resnet_meta)
    first = pipeline.process(make_pdf(settings.inbox_path / "a.pdf", RESNET_PAGE))
    second = pipeline.process(
        make_pdf(settings.inbox_path / "b.pdf", [*RESNET_PAGE, "accepted version"])
    )
    assert first.destination and second.destination
    assert first.destination != second.destination
    assert second.destination.stem.startswith(first.destination.stem + "-")


def test_no_doi_goes_to_manual_review(settings, resnet_meta, make_pdf) -> None:
    pdf = make_pdf(settings.inbox_path / "notes.pdf", ["Just notes", *FILLER])
    result = _pipeline(settings, resnet_meta).process(pdf)
    assert result.outcome is Outcome.MANUAL_REVIEW
    moved = settings.manual_review_path / "notes.pdf"
    assert moved.exists()
    assert "no se encontró DOI" in moved.with_suffix(".pdf.txt").read_text()


def test_corrupt_pdf_goes_to_manual_review(settings, resnet_meta) -> None:
    bad = settings.inbox_path / "bad.pdf"
    bad.write_bytes(b"garbage")
    result = _pipeline(settings, resnet_meta).process(bad)
    assert result.outcome is Outcome.MANUAL_REVIEW


def test_reference_doi_rejected_by_title_check(settings, resnet_meta, make_pdf):
    pdf = make_pdf(
        settings.inbox_path / "other.pdf",
        ["A Paper About Bananas", *FILLER],
        ["References", "[1] He et al. 10.1109/CVPR.2016.90"],
    )
    result = _pipeline(settings, resnet_meta).process(pdf)
    assert result.outcome is Outcome.MANUAL_REVIEW
    assert "no aparece en la portada" in result.reasons[0]


def test_falls_through_to_second_candidate(settings, resnet_meta, make_pdf):
    pdf = make_pdf(
        settings.inbox_path / "x.pdf", [*RESNET_PAGE, "funding 10.9999/unknown"]
    )
    pipeline = _pipeline(settings, resnet_meta)
    assert pipeline.process(pdf).outcome is Outcome.ARCHIVED


def test_dry_run_does_not_touch_anything(settings, resnet_meta, make_pdf) -> None:
    pdf = make_pdf(settings.inbox_path / "a.pdf", RESNET_PAGE)
    zotero = FakeZotero()
    result = _pipeline(settings, resnet_meta, zotero).process(pdf, dry_run=True)
    assert result.outcome is Outcome.DRY_RUN
    assert pdf.exists()
    assert not any(settings.archive_path.glob("*.pdf"))
    assert zotero.synced == []


def test_sync_failure_is_retried(settings, resnet_meta, make_pdf) -> None:
    pdf = make_pdf(settings.inbox_path / "a.pdf", RESNET_PAGE)
    failing, notion = FakeZotero(fail=True), FakeNotion()
    result = _pipeline(settings, resnet_meta, failing, notion).process(pdf)
    assert result.outcome is Outcome.SYNC_PENDING
    assert result.destination and result.destination.exists()

    zotero = FakeZotero()
    retry_notion = FakeNotion()
    pipeline = _pipeline(settings, resnet_meta, zotero, retry_notion)
    assert pipeline.retry_pending() == 0
    assert len(zotero.synced) == 1
    assert retry_notion.synced == []  # Notion ya estaba hecho


def test_title_score_handles_hyphenation() -> None:
    page = "Deep Residual Learning for Image Re-\ncognition\nKaiming He"
    assert title_score("Deep Residual Learning for Image Recognition", page) == 100
    assert title_score("Attention Is All You Need", page) < 80
