"""Capa de resolución: DOI → metadatos canónicos (Crossref, DataCite, Semantic Scholar).

Cadena de fallback: Crossref → DataCite → Semantic Scholar. Un 404 pasa
directamente a la siguiente fuente; los errores transitorios (429, 5xx, timeouts,
conexión) se reintentan con retroceso exponencial antes de pasar a la siguiente.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

import requests
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from biblio_demon import __version__
from biblio_demon.config import Settings
from biblio_demon.models import Author, ItemType, PaperMetadata

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
ARXIV_DOI_PREFIX = "10.48550/arxiv."

_CROSSREF_TYPES: dict[str, ItemType] = {
    "journal-article": "journalArticle",
    "proceedings-article": "conferencePaper",
    "posted-content": "preprint",
    "book-chapter": "bookSection",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "report": "report",
}
_DATACITE_TYPES: dict[str, ItemType] = {
    "JournalArticle": "journalArticle",
    "ConferencePaper": "conferencePaper",
    "Preprint": "preprint",
    "BookChapter": "bookSection",
    "Book": "book",
    "Report": "report",
}


class MetadataNotFound(Exception):
    """La fuente respondió 404 (o equivalente) para el DOI."""


class TransientHTTPError(Exception):
    """Error recuperable (429/5xx). ``retry_after`` en segundos si el servidor lo da."""

    def __init__(self, status: int, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


class ResolutionError(Exception):
    """Ninguna fuente pudo resolver el DOI."""

    def __init__(self, doi: str, reasons: dict[str, str]) -> None:
        detail = "; ".join(f"{src}: {why}" for src, why in reasons.items())
        super().__init__(f"No se pudo resolver {doi} ({detail})")
        self.doi = doi
        self.reasons = reasons


class MetadataSourceClient(Protocol):
    name: str

    def fetch(self, doi: str) -> PaperMetadata: ...


# --------------------------------------------------------------------------- utils
def clean_markup(text: str | None) -> str | None:
    """Quita etiquetas JATS/HTML, decodifica entidades y colapsa espacios."""
    if not text:
        return None
    cleaned = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip()
    cleaned = re.sub(r"^(abstract|resumen)\s*[:.]?\s+", "", cleaned, flags=re.I)
    return cleaned or None


def _str_or_none(value: Any) -> str | None:
    """Convierte a ``str`` limpio; vacío o ``None`` → ``None``."""
    text = str(value).strip() if value is not None else ""
    return text or None


def _unique(items: list[str]) -> list[str]:
    """Quita duplicados (sin distinguir mayúsculas) conservando el orden."""
    seen: set[str] = set()
    out: list[str] = []
    for item in (i.strip() for i in items):
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def split_full_name(name: str) -> Author:
    """Separa ``"Kaiming He"`` → given="Kaiming", family="He" (último token)."""
    parts = name.strip().split()
    if len(parts) <= 1:
        return Author(family=name.strip())
    return Author(family=parts[-1], given=" ".join(parts[:-1]))


def _retry_after(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _wait(retry_state: RetryCallState) -> float:
    """Respeta ``Retry-After`` si existe; si no, backoff exponencial con jitter."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, TransientHTTPError) and exc.retry_after is not None:
        return min(exc.retry_after, 60.0)
    return wait_exponential_jitter(initial=1, max=30)(retry_state)


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning("Reintento %d tras error: %s", retry_state.attempt_number, exc)


# ------------------------------------------------------------------------- clients
class _HttpSource:
    """Base con sesión HTTP compartida y reintentos."""

    name = "base"

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()
        # requests ya trae un User-Agent por defecto: hay que sobrescribirlo.
        self.session.headers["User-Agent"] = (
            f"BiblioDemon/{__version__} (mailto:{settings.crossref_email})"
        )
        self._get_json: Callable[..., dict[str, Any]] = retry(
            retry=retry_if_exception_type(
                (TransientHTTPError, requests.ConnectionError, requests.Timeout)
            ),
            stop=stop_after_attempt(settings.http_max_attempts),
            wait=_wait,
            before_sleep=_log_retry,
            reraise=True,
        )(self._get_json_once)

    def _get_json_once(
        self, url: str, params: dict[str, str] | None = None, **headers: str
    ) -> dict[str, Any]:
        response = self.session.get(
            url, params=params, headers=headers, timeout=self.settings.http_timeout
        )
        if response.status_code == 404:
            raise MetadataNotFound(f"{self.name}: 404")
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientHTTPError(response.status_code, _retry_after(response))
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data


class CrossrefClient(_HttpSource):
    """Cliente de ``api.crossref.org`` (polite pool vía ``mailto`` en User-Agent)."""

    name = "crossref"
    BASE_URL = "https://api.crossref.org/works/"

    def fetch(self, doi: str) -> PaperMetadata:
        msg = self._get_json(self.BASE_URL + quote(doi, safe="/"))["message"]
        return self.parse(doi, msg)

    @staticmethod
    def parse(doi: str, msg: dict[str, Any]) -> PaperMetadata:
        creators: list[Author] = []
        for a in msg.get("author", []):
            if a.get("family"):
                creators.append(Author(family=a["family"], given=a.get("given", "")))
            elif a.get("name"):
                creators.append(Author(family=a["name"]))
        year: int | None = None
        for key in ("issued", "published-print", "published-online", "created"):
            parts = (msg.get(key) or {}).get("date-parts") or [[None]]
            if parts[0] and parts[0][0]:
                year = int(parts[0][0])
                break
        titles = msg.get("title") or []
        venues = msg.get("container-title") or []
        return PaperMetadata(
            doi=doi,
            title=clean_markup(titles[0] if titles else None) or "",
            creators=creators,
            year=year,
            abstract=clean_markup(msg.get("abstract")),
            venue=clean_markup(venues[0]) if venues else None,
            volume=_str_or_none(msg.get("volume")),
            issue=_str_or_none(msg.get("issue")),
            pages=_str_or_none(msg.get("page")),
            subjects=_unique(msg.get("subject") or []),
            citation_count=msg.get("is-referenced-by-count"),
            item_type=_CROSSREF_TYPES.get(msg.get("type", ""), "journalArticle"),
            source="crossref",
        )


class DataCiteClient(_HttpSource):
    """Cliente de ``api.datacite.org`` (Zenodo, arXiv 10.48550, figshare, tesis…)."""

    name = "datacite"
    BASE_URL = "https://api.datacite.org/dois/"

    def fetch(self, doi: str) -> PaperMetadata:
        attrs = self._get_json(self.BASE_URL + quote(doi, safe="/"))["data"][
            "attributes"
        ]
        return self.parse(doi, attrs)

    @staticmethod
    def parse(doi: str, attrs: dict[str, Any]) -> PaperMetadata:
        creators: list[Author] = []
        for c in attrs.get("creators", []):
            if c.get("familyName"):
                creators.append(
                    Author(family=c["familyName"], given=c.get("givenName", ""))
                )
            elif c.get("nameType") == "Organizational" or "," not in c.get("name", ""):
                creators.append(Author(family=c.get("name", "")))
            else:  # "Apellido, Nombre"
                family, _, given = c["name"].partition(",")
                creators.append(Author(family=family.strip(), given=given.strip()))
        abstract = next(
            (
                d.get("description")
                for d in attrs.get("descriptions", [])
                if d.get("descriptionType") == "Abstract"
            ),
            None,
        )
        titles = attrs.get("titles") or [{}]
        container_info = attrs.get("container") or {}
        container = container_info.get("title")
        first, last = container_info.get("firstPage"), container_info.get("lastPage")
        pages = f"{first}-{last}" if first and last else _str_or_none(first)
        rtype = (attrs.get("types") or {}).get("resourceTypeGeneral", "")
        year = attrs.get("publicationYear")
        return PaperMetadata(
            doi=doi,
            title=clean_markup(titles[0].get("title")) or "",
            creators=creators,
            year=int(year) if year else None,
            abstract=clean_markup(abstract),
            venue=container or attrs.get("publisher") or None,
            volume=_str_or_none(container_info.get("volume")),
            issue=_str_or_none(container_info.get("issue")),
            pages=pages,
            subjects=_unique(
                [s.get("subject", "") for s in attrs.get("subjects") or []]
            ),
            citation_count=attrs.get("citationCount"),
            item_type=_DATACITE_TYPES.get(
                rtype, "preprint" if doi.startswith(ARXIV_DOI_PREFIX) else "report"
            ),
            source="datacite",
        )


class SemanticScholarClient(_HttpSource):
    """Cliente de Semantic Scholar Graph API (fallback)."""

    name = "semantic_scholar"
    BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/DOI:"
    FIELDS = (
        "title,authors,year,abstract,venue,publicationTypes,journal,"
        "citationCount,fieldsOfStudy,s2FieldsOfStudy"
    )

    def _headers(self) -> dict[str, str]:
        key = self.settings.semantic_scholar_api_key
        return {"x-api-key": key.get_secret_value()} if key else {}

    def fetch(self, doi: str) -> PaperMetadata:
        data = self._get_json(
            self.BASE_URL + quote(doi, safe="/"),
            {"fields": self.FIELDS},
            **self._headers(),
        )
        return self.parse(doi, data)

    def fetch_extras(self, doi: str) -> dict[str, Any]:
        """Áreas y citas, en un solo intento (enriquecimiento opcional)."""
        data = self._get_json_once(
            self.BASE_URL + quote(doi, safe="/"),
            {"fields": "citationCount,fieldsOfStudy,s2FieldsOfStudy"},
            **self._headers(),
        )
        return {
            "subjects": self.subjects(data),
            "citation_count": data.get("citationCount"),
        }

    @staticmethod
    def subjects(data: dict[str, Any]) -> list[str]:
        fields = list(data.get("fieldsOfStudy") or [])
        fields += [f.get("category", "") for f in data.get("s2FieldsOfStudy") or []]
        return _unique(fields)

    @staticmethod
    def parse(doi: str, data: dict[str, Any]) -> PaperMetadata:
        types = data.get("publicationTypes") or []
        journal = data.get("journal") or {}
        item_type: ItemType = (
            "conferencePaper" if "Conference" in types else "journalArticle"
        )
        return PaperMetadata(
            doi=doi,
            title=clean_markup(data.get("title")) or "",
            creators=[split_full_name(a["name"]) for a in data.get("authors", [])],
            year=data.get("year"),
            abstract=clean_markup(data.get("abstract")),
            venue=data.get("venue") or journal.get("name") or None,
            volume=_str_or_none(journal.get("volume")),
            pages=_str_or_none(journal.get("pages")),
            subjects=SemanticScholarClient.subjects(data),
            citation_count=data.get("citationCount"),
            item_type=item_type,
            source="semantic_scholar",
        )


# ------------------------------------------------------------------------ resolver
class MetadataResolver:
    """Resuelve un DOI probando las fuentes en orden."""

    def __init__(
        self,
        settings: Settings,
        sources: list[MetadataSourceClient] | None = None,
        enricher: SemanticScholarClient | None = None,
    ) -> None:
        if sources is None:
            session = requests.Session()
            enricher = SemanticScholarClient(settings, session)
            sources = [
                CrossrefClient(settings, session),
                DataCiteClient(settings, session),
                enricher,
            ]
        self.sources = sources
        self.enricher = enricher

    def resolve(self, doi: str) -> PaperMetadata:
        """Devuelve metadatos normalizados del primer origen que responda.

        Raises:
            ResolutionError: si todas las fuentes fallan.
        """
        reasons: dict[str, str] = {}
        for source in self.sources:
            try:
                meta = source.fetch(doi)
            except MetadataNotFound:
                reasons[source.name] = "no encontrado"
                logger.info("%s: %s no encontrado", source.name, doi)
                continue
            except (
                TransientHTTPError,
                requests.RequestException,
                ValueError,  # JSON inválido o validación de pydantic
                KeyError,
            ) as exc:
                reasons[source.name] = f"{type(exc).__name__}: {exc}"
                logger.warning("%s falló para %s: %s", source.name, doi, exc)
                continue
            logger.info("DOI %s resuelto vía %s: %s", doi, source.name, meta.title)
            return self._enrich(meta)
        raise ResolutionError(doi, reasons)

    def _enrich(self, meta: PaperMetadata) -> PaperMetadata:
        """Completa áreas temáticas con Semantic Scholar si la fuente no las dio.

        Crossref ya no publica ``subject`` para la mayoría de los DOIs. Es un
        paso opcional: si Semantic Scholar falla, se sigue sin áreas.
        """
        if meta.subjects or self.enricher is None:
            return meta
        try:
            extras = self.enricher.fetch_extras(meta.doi)
        except (
            MetadataNotFound,
            TransientHTTPError,
            requests.RequestException,
            ValueError,
        ) as exc:
            logger.info("Sin áreas temáticas para %s: %s", meta.doi, exc)
            return meta
        update: dict[str, Any] = {"subjects": extras["subjects"]}
        if meta.citation_count is None:
            update["citation_count"] = extras["citation_count"]
        return meta.model_copy(update=update)

    @staticmethod
    def arxiv_doi(arxiv_id: str) -> str:
        """DOI que arXiv registra en DataCite para un identificador."""
        return f"{ARXIV_DOI_PREFIX}{arxiv_id.lower()}"
