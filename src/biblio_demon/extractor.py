"""Extracción determinista de identificadores (DOI / arXiv) desde un PDF.

No se infiere ningún metadato del cuerpo del PDF: solo se buscan identificadores
persistentes, que luego se resuelven contra las APIs canónicas.

Orden de prioridad de las fuentes:
1. Metadatos embebidos (XMP y diccionario Info del PDF).
2. Hipervínculos a ``doi.org`` en las primeras páginas.
3. Texto de las primeras páginas.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

import pymupdf

logger = logging.getLogger(__name__)

# Sin ``\b`` final: un DOI puede terminar en ``)`` u otro carácter no-palabra.
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>{}|\\^`]+", re.IGNORECASE)
DOI_URL_RE = re.compile(r"(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
ARXIV_RE = re.compile(
    r"(?:arxiv(?:\.org/abs/|:)\s*)"
    r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)
_TRAILING_PUNCT = ".,;:'"
_PAIRS = {")": "(", "]": "["}

CandidateSource = Literal["metadata", "link", "text"]
_SOURCE_RANK: dict[CandidateSource, int] = {"metadata": 0, "link": 1, "text": 2}


class PdfReadError(Exception):
    """El PDF no se pudo abrir o leer."""


@dataclass(frozen=True)
class DoiCandidate:
    """DOI candidato con su procedencia."""

    doi: str
    source: CandidateSource
    page: int | None  # 0-indexada; None para metadatos embebidos
    count: int = 1


@dataclass
class ExtractionResult:
    """Resultado de inspeccionar un PDF."""

    candidates: list[DoiCandidate] = field(default_factory=list)
    arxiv_ids: list[str] = field(default_factory=list)
    first_page_text: str = ""

    @property
    def has_identifier(self) -> bool:
        return bool(self.candidates or self.arxiv_ids)


def sanitize_doi(raw: str) -> str | None:
    """Normaliza un DOI extraído.

    Decodifica ``%xx``, elimina puntuación final accidental y paréntesis o
    corchetes de cierre desbalanceados, y lo pasa a minúsculas (los DOIs no
    distinguen mayúsculas).

    Returns:
        El DOI limpio, o ``None`` si no queda un DOI válido.
    """
    doi = unquote(raw.strip())
    changed = True
    while changed and doi:
        changed = False
        if doi[-1] in _TRAILING_PUNCT:
            doi = doi[:-1]
            changed = True
        elif doi[-1] in _PAIRS and doi.count(doi[-1]) > doi.count(_PAIRS[doi[-1]]):
            doi = doi[:-1]
            changed = True
    match = DOI_RE.fullmatch(doi)
    return match.group(0).lower() if match else None


def _join_broken_lines(text: str) -> str:
    """Une DOIs partidos por salto de línea tras ``/``, ``.``, ``-`` o ``_``."""
    return re.sub(r"(?<=[/._\-])\s*\n\s*", "", text)


def find_dois(text: str) -> list[str]:
    """Devuelve los DOIs (sanitizados, en orden de aparición) presentes en ``text``.

    Primero los encontrados tal cual; después las variantes que solo aparecen al
    unir líneas partidas. Una unión errónea (DOI + palabra de la línea siguiente)
    da un DOI inexistente que la capa de resolución descarta.
    """
    found: list[str] = []
    for chunk in (text, _join_broken_lines(text)):
        for raw in DOI_RE.findall(chunk):
            doi = sanitize_doi(raw)
            if doi and doi not in found:
                found.append(doi)
    return found


def find_arxiv_ids(text: str) -> list[str]:
    """Devuelve identificadores arXiv (sin versión) en orden de aparición."""
    ids: list[str] = []
    for arxiv_id in ARXIV_RE.findall(text):
        if arxiv_id not in ids:
            ids.append(arxiv_id)
    return ids


def extract_identifiers(pdf_path: Path, max_pages: int = 3) -> ExtractionResult:
    """Inspecciona metadatos, enlaces y texto de las primeras ``max_pages`` páginas.

    Raises:
        PdfReadError: si el archivo no es un PDF legible.
    """
    try:
        doc = pymupdf.open(pdf_path)
    except (
        pymupdf.FileDataError,
        pymupdf.FileNotFoundError,
        pymupdf.EmptyFileError,
        RuntimeError,
    ) as exc:
        raise PdfReadError(f"No se pudo abrir {pdf_path.name}: {exc}") from exc

    result = ExtractionResult()
    texts: list[str] = []
    counts: Counter[str] = Counter()
    best: dict[str, tuple[CandidateSource, int | None]] = {}

    def add(doi: str, source: CandidateSource, page: int | None, n: int = 1) -> None:
        counts[doi] += n
        if doi not in best:
            best[doi] = (source, page)

    try:
        if doc.needs_pass:
            raise PdfReadError(f"{pdf_path.name} está protegido con contraseña")

        embedded = [doc.get_xml_metadata() or ""]
        embedded += [str(v) for v in (doc.metadata or {}).values() if v]
        for chunk in embedded:
            for doi in find_dois(chunk):
                add(doi, "metadata", None)

        for page_no in range(min(max_pages, doc.page_count)):
            page = doc[page_no]
            for link in page.get_links():
                uri = link.get("uri") or ""
                m = DOI_URL_RE.search(uri)
                if m and (doi := sanitize_doi(m.group(1))):
                    add(doi, "link", page_no)
            text = page.get_text("text")
            texts.append(text)
            lowered = text.lower()
            for doi in find_dois(text):
                add(doi, "text", page_no, max(1, lowered.count(doi)))
    except RuntimeError as exc:  # errores internos de MuPDF al leer páginas
        raise PdfReadError(f"Error leyendo {pdf_path.name}: {exc}") from exc
    finally:
        doc.close()

    result.first_page_text = texts[0] if texts else ""
    result.arxiv_ids = find_arxiv_ids("\n".join(texts))
    candidates = [
        DoiCandidate(doi=d, source=src, page=pg, count=counts[d])
        for d, (src, pg) in best.items()
    ]
    # Prioridad: metadatos embebidos → página más temprana → enlace sobre texto
    # → más repeticiones. Así un DOI de la bibliografía (pág. 3) nunca gana a uno
    # de la portada.
    candidates.sort(
        key=lambda c: (
            c.page if c.page is not None else -1,
            _SOURCE_RANK[c.source],
            -c.count,
        )
    )
    result.candidates = candidates
    logger.debug(
        "%s: candidatos DOI=%s arXiv=%s",
        pdf_path.name,
        [c.doi for c in candidates],
        result.arxiv_ids,
    )
    return result
