"""Orquestación del procesamiento de un PDF, independiente del watcher."""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from biblio_demon.config import Settings
from biblio_demon.extractor import PdfReadError, extract_identifiers
from biblio_demon.metadata import MetadataResolver, ResolutionError
from biblio_demon.models import PaperMetadata
from biblio_demon.naming import build_filename, to_ascii
from biblio_demon.state import PaperRecord, StateStore
from biblio_demon.storage import move_file, quarantine, sha256_file, unique_destination
from biblio_demon.sync import SyncError
from biblio_demon.sync.notion_sync import NotionSync
from biblio_demon.sync.zotero_sync import ZoteroSync

logger = logging.getLogger(__name__)

MIN_TEXT_FOR_VERIFICATION = 200  # caracteres; menos = PDF escaneado o sin texto


class Outcome(enum.StrEnum):
    ARCHIVED = "archived"  # archivado y sincronizado con todo lo activo
    SYNC_PENDING = "sync_pending"  # archivado; alguna sincronización falló
    DUPLICATE = "duplicate"
    MANUAL_REVIEW = "manual_review"
    DRY_RUN = "dry_run"
    SKIPPED = "skipped"


@dataclass
class ProcessResult:
    outcome: Outcome
    source: Path
    destination: Path | None = None
    metadata: PaperMetadata | None = None
    reasons: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    text = re.sub(r"-\s*\n\s*", "", text)  # deshace guionado de fin de línea
    return " ".join(re.findall(r"[a-z0-9]+", to_ascii(text).lower()))


def title_score(title: str, page_text: str) -> float:
    """Similitud (0-100) entre el título de la API y el texto de la portada."""
    return float(fuzz.partial_ratio(_normalize(title), _normalize(page_text)))


class Pipeline:
    """Ejecuta extracción → resolución → archivado → Zotero → Notion."""

    def __init__(
        self,
        settings: Settings,
        resolver: MetadataResolver | None = None,
        state: StateStore | None = None,
        zotero_sync: ZoteroSync | None = None,
        notion_sync: NotionSync | None = None,
    ) -> None:
        self.s = settings
        self.resolver = resolver or MetadataResolver(settings)
        self.state = state or StateStore(settings.state_db)
        self.zotero = zotero_sync or (
            ZoteroSync(settings) if settings.zotero_enabled else None
        )
        self.notion = notion_sync or (
            NotionSync(settings) if settings.notion_enabled else None
        )

    # ------------------------------------------------------------- resolución
    def _resolve(self, pdf_path: Path) -> tuple[PaperMetadata | None, list[str]]:
        """Metadatos del primer candidato válido, o los motivos de fallo."""
        extraction = extract_identifiers(pdf_path)
        if not extraction.has_identifier:
            return None, ["no se encontró DOI ni identificador arXiv"]

        dois = [c.doi for c in extraction.candidates][: self.s.max_doi_candidates]
        for arxiv_id in extraction.arxiv_ids:
            arxiv_doi = self.resolver.arxiv_doi(arxiv_id)
            if arxiv_doi not in dois:
                dois.append(arxiv_doi)

        verify = (
            self.s.verify_title
            and len(extraction.first_page_text.strip()) >= MIN_TEXT_FOR_VERIFICATION
        )
        if self.s.verify_title and not verify:
            logger.warning(
                "%s: portada sin texto suficiente; se omite la verificación de título",
                pdf_path.name,
            )

        reasons: list[str] = []
        for doi in dois:
            try:
                meta = self.resolver.resolve(doi)
            except ResolutionError as exc:
                reasons.append(str(exc))
                continue
            if verify:
                score = title_score(meta.title, extraction.first_page_text)
                if score < self.s.title_match_threshold:
                    reasons.append(
                        f"{doi}: el título '{meta.title}' no aparece en la portada "
                        f"(similitud {score:.0f} < {self.s.title_match_threshold})"
                    )
                    continue
            return meta, reasons
        return None, reasons

    # ---------------------------------------------------------------- sync
    def sync_record(self, record: PaperRecord) -> list[str]:
        """Ejecuta las sincronizaciones pendientes de un registro. Devuelve errores."""
        errors: list[str] = []
        if self.zotero and not record.zotero_key:
            try:
                key = self.zotero.sync(record.metadata, record.archive_path)
                self.state.set_zotero_key(record.doi, key)
            except SyncError as exc:
                logger.error("%s", exc)
                errors.append(str(exc))
        if self.notion and not record.notion_page_id:
            try:
                page_id = self.notion.sync(record.metadata)
                self.state.set_notion_page_id(record.doi, page_id)
            except SyncError as exc:
                logger.error("%s", exc)
                errors.append(str(exc))
        return errors

    def retry_pending(self) -> int:
        """Reintenta sincronizaciones pendientes. Devuelve cuántas siguen fallando."""
        failures = 0
        for record in self.state.pending(
            zotero=self.zotero is not None, notion=self.notion is not None
        ):
            logger.info("Reintentando sincronización de %s", record.doi)
            if self.sync_record(record):
                failures += 1
        return failures

    # -------------------------------------------------------------- principal
    def process(self, pdf_path: Path, dry_run: bool = False) -> ProcessResult:
        """Procesa un PDF de la bandeja de entrada de principio a fin.

        Nunca lanza excepciones por fallos esperables: los PDFs problemáticos
        terminan en ``manual_review/`` y el resultado indica el motivo.
        """
        print(f"Procesando {pdf_path.name} ...", end="", flush=True)
        if not pdf_path.is_file():
            return ProcessResult(Outcome.SKIPPED, pdf_path, reasons=["ya no existe"])

        def to_review(reasons: list[str]) -> ProcessResult:
            dest = None
            if not dry_run:
                dest = quarantine(
                    pdf_path, self.s.manual_review_path, "\n".join(reasons)
                )
            else:
                logger.warning(
                    "[dry-run] %s → manual_review: %s", pdf_path.name, reasons
                )
            return ProcessResult(Outcome.MANUAL_REVIEW, pdf_path, dest, reasons=reasons)

        file_hash = sha256_file(pdf_path)
        if known := self.state.get_by_hash(file_hash):
            reason = f"idéntico a {known.archive_path.name} ({known.doi})"
            dest = None
            if not dry_run:
                dest = quarantine(pdf_path, self.s.duplicates_path, reason)
            return ProcessResult(
                Outcome.DUPLICATE, pdf_path, dest, known.metadata, [reason]
            )

        try:
            meta, reasons = self._resolve(pdf_path)
        except PdfReadError as exc:
            return to_review([str(exc)])
        if meta is None:
            return to_review(reasons)

        filename = build_filename(
            meta, self.s.filename_title_words, self.s.drop_leading_stopwords
        )
        destination = unique_destination(self.s.archive_path, filename, file_hash)

        if dry_run:
            logger.info("[dry-run] %s → %s", pdf_path.name, destination)
            if self.zotero:
                logger.info("[dry-run] Zotero: %s", self.zotero.build_item(meta))
            if self.notion:
                logger.info("[dry-run] Notion: %s", self.notion.build_properties(meta))
            return ProcessResult(Outcome.DRY_RUN, pdf_path, destination, meta)

        previous = self.state.get_by_doi(meta.doi)
        move_file(pdf_path, destination)
        self.state.record_archived(meta, file_hash, destination)
        if previous:
            logger.warning(
                "%s ya estaba archivado como %s; nueva versión guardada como %s",
                meta.doi,
                previous.archive_path.name,
                destination.name,
            )

        record = self.state.get_by_doi(meta.doi)
        assert record is not None
        errors = self.sync_record(record)
        outcome = Outcome.SYNC_PENDING if errors else Outcome.ARCHIVED
        return ProcessResult(outcome, pdf_path, destination, meta, errors)
