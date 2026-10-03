"""Integración con Notion vía ``notion-client`` (API 2025-09-03).

Desde la versión 2025-09-03 de la API, las propiedades viven en el *data source*
de la base de datos: se resuelve ``NOTION_DATABASE_ID`` → ``data_source_id`` y
las páginas se crean con ``parent = {"data_source_id": ...}``.
"""

from __future__ import annotations

import logging
from typing import Any

from notion_client import Client
from notion_client.errors import NotionClientErrorBase

from biblio_demon.config import Settings
from biblio_demon.models import PaperMetadata
from biblio_demon.sync import SyncError

logger = logging.getLogger(__name__)

_RICH_TEXT_LIMIT = 2000  # caracteres por objeto rich_text
_OPTION_LIMIT = 100  # caracteres por opción de select / multi-select


def _option(name: str) -> dict[str, str]:
    """Opción de select válida: Notion no admite comas y limita a 100 caracteres."""
    return {"name": name.replace(",", " ")[:_OPTION_LIMIT].strip()}


def _rich_text(text: str) -> list[dict[str, Any]]:
    """Trocea texto largo en bloques de 2000 caracteres (límite de Notion)."""
    return [
        {"type": "text", "text": {"content": text[i : i + _RICH_TEXT_LIMIT]}}
        for i in range(0, len(text), _RICH_TEXT_LIMIT)
    ] or [{"type": "text", "text": {"content": ""}}]


class NotionSync:
    """Crea una página por artículo en la base de datos configurada."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if client is None:
            if not settings.notion_enabled:
                raise ValueError("Faltan NOTION_TOKEN / NOTION_DATABASE_ID")
            assert settings.notion_token is not None
            client = Client(auth=settings.notion_token.get_secret_value())
        self.client = client
        self.s = settings
        self._data_source_id: str | None = None
        self._schema: dict[str, Any] | None = None

    # ------------------------------------------------------------- esquema
    def _load_schema(self) -> None:
        if self._schema is not None:
            return
        db = self.client.databases.retrieve(database_id=self.s.notion_database_id)
        sources = db.get("data_sources") or []
        if not sources:
            raise SyncError("Notion: la base de datos no tiene data sources")
        if len(sources) > 1:
            logger.warning(
                "Notion: la base tiene %d data sources; se usa '%s'",
                len(sources),
                sources[0].get("name"),
            )
        self._data_source_id = sources[0]["id"]
        ds = self.client.data_sources.retrieve(data_source_id=self._data_source_id)
        self._schema = ds["properties"]

    def validate_schema(self) -> None:
        """Comprueba que existan las propiedades con el tipo esperado.

        Raises:
            SyncError: con la lista de problemas encontrados.
        """
        try:
            self._load_schema()
        except NotionClientErrorBase as exc:
            raise SyncError(f"Notion: no se pudo leer la base: {exc}") from exc
        assert self._schema is not None
        expected = {
            self.s.notion_prop_title: "title",
            self.s.notion_prop_doi: "url",
            self.s.notion_prop_year: "number",
            self.s.notion_prop_status: "status",
            self.s.notion_prop_authors: "rich_text",
            self.s.notion_prop_venue: "rich_text",
            self.s.notion_prop_citations: "number",
            self.s.notion_prop_subjects: "multi_select",
        }
        expected.pop("", None)
        problems = []
        for name, kind in expected.items():
            actual = self._schema.get(name, {}).get("type")
            if actual != kind:
                problems.append(f"'{name}' debe ser {kind} (es {actual or 'ausente'})")
        status_options = {
            o["name"]
            for o in self._schema.get(self.s.notion_prop_status, {})
            .get("status", {})
            .get("options", [])
        }
        if status_options and self.s.notion_status_value not in status_options:
            problems.append(
                f"la opción de estado '{self.s.notion_status_value}' no existe "
                f"(hay: {sorted(status_options)})"
            )
        if problems:
            raise SyncError("Notion: esquema inválido: " + "; ".join(problems))

    # ------------------------------------------------------------- páginas
    def build_properties(self, meta: PaperMetadata) -> dict[str, Any]:
        year = meta.year if meta.year else "s.f."
        title = f"{meta.title} ({meta.first_author_family}, {year})"
        props: dict[str, Any] = {
            self.s.notion_prop_title: {"title": _rich_text(title)},
            self.s.notion_prop_doi: {"url": meta.doi_url},
            self.s.notion_prop_year: {"number": meta.year},
            self.s.notion_prop_status: {"status": {"name": self.s.notion_status_value}},
            self.s.notion_prop_authors: {
                "rich_text": _rich_text(", ".join(meta.authors))
            },
        }
        # Opcionales: se omiten si la variable NOTION_PROP_* está vacía.
        if self.s.notion_prop_venue and meta.venue:
            props[self.s.notion_prop_venue] = {"select": _option(meta.venue)}
        if self.s.notion_prop_citations:
            props[self.s.notion_prop_citations] = {"number": meta.citation_count}
        if self.s.notion_prop_subjects and meta.subjects:
            props[self.s.notion_prop_subjects] = {
                "multi_select": [_option(s) for s in meta.subjects]
            }
        return props

    def find_existing(self, meta: PaperMetadata) -> str | None:
        """ID de una página existente con la misma URL de DOI."""
        assert self._data_source_id is not None
        response = self.client.data_sources.query(
            data_source_id=self._data_source_id,
            filter={
                "property": self.s.notion_prop_doi,
                "url": {"equals": meta.doi_url},
            },
            page_size=1,
        )
        results = response.get("results") or []
        return str(results[0]["id"]) if results else None

    def sync(self, meta: PaperMetadata) -> str:
        """Crea la página (o reutiliza la existente). Devuelve su ID.

        Raises:
            SyncError: ante cualquier fallo de la API.
        """
        try:
            self._load_schema()
            existing = self.find_existing(meta)
            if existing:
                logger.info("Notion: %s ya existe (%s)", meta.doi, existing)
                return existing
            page = self.client.pages.create(
                parent={
                    "type": "data_source_id",
                    "data_source_id": self._data_source_id,
                },
                properties=self.build_properties(meta),
            )
        except NotionClientErrorBase as exc:
            raise SyncError(f"Notion: {exc}") from exc
        logger.info("Notion: página creada %s para %s", page["id"], meta.doi)
        return str(page["id"])
