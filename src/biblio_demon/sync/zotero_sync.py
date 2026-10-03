"""Integración con la Zotero Web API vía ``pyzotero``.

El PDF se adjunta como *linked file* (ruta absoluta local): no consume la cuota
de almacenamiento de Zotero. El enlace solo resuelve en la máquina donde vive
``ARCHIVE_PATH``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx2
from pyzotero import zotero
from pyzotero.errors import PyZoteroError

from biblio_demon.config import Settings
from biblio_demon.models import PaperMetadata
from biblio_demon.sync import SyncError

logger = logging.getLogger(__name__)

# Campo de Zotero donde va ``venue`` según el tipo de elemento.
_VENUE_FIELD = {
    "journalArticle": "publicationTitle",
    "conferencePaper": "proceedingsTitle",
    "preprint": "repository",
    "bookSection": "bookTitle",
    "report": "institution",
    "book": "publisher",
}
ZOTERO_ERRORS: tuple[type[Exception], ...] = (PyZoteroError, httpx2.HTTPError)


class ZoteroSync:
    """Crea elementos en Zotero y les adjunta el PDF archivado como enlace."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if client is None:
            if not settings.zotero_enabled:
                raise ValueError("Faltan ZOTERO_USER_ID / ZOTERO_API_KEY")
            assert settings.zotero_api_key is not None
            client = zotero.Zotero(
                settings.zotero_user_id,
                settings.zotero_library_type,
                settings.zotero_api_key.get_secret_value(),
            )
        self.zot = client
        self.collection_key = settings.zotero_collection_key

    # ---------------------------------------------------------------- consultas
    def find_existing(self, doi: str) -> str | None:
        """Clave de un elemento (no adjunto/nota) cuyo campo DOI coincide."""
        try:
            results = self.zot.items(q=doi, qmode="everything", limit=25)
        except ZOTERO_ERRORS as exc:
            raise SyncError(f"Zotero: búsqueda de {doi} falló: {exc}") from exc
        for item in results:
            data = item.get("data", {})
            if data.get("itemType") in {"attachment", "note"}:
                continue
            if str(data.get("DOI", "")).strip().lower() == doi.lower():
                return str(item["key"])
        return None

    def _has_linked_pdf(self, parent_key: str, pdf_path: Path) -> bool:
        for child in self.zot.children(parent_key):
            data = child.get("data", {})
            if data.get("linkMode") == "linked_file" and data.get("path") == str(
                pdf_path
            ):
                return True
        return False

    # -------------------------------------------------------------- construcción
    def build_item(self, meta: PaperMetadata) -> dict[str, Any]:
        """Payload del elemento a partir de la plantilla oficial del tipo."""
        item: dict[str, Any] = self.zot.item_template(meta.item_type)
        item["title"] = meta.title
        item["creators"] = [
            (
                {"creatorType": "author", "firstName": a.given, "lastName": a.family}
                if a.given
                else {"creatorType": "author", "name": a.family}
            )
            for a in meta.creators
        ]
        if meta.year:
            item["date"] = str(meta.year)
        optional = {
            "DOI": meta.doi,
            "url": meta.doi_url,
            "abstractNote": meta.abstract or "",
            _VENUE_FIELD.get(meta.item_type, ""): meta.venue or "",
            "volume": meta.volume or "",
            "issue": meta.issue or "",
            "pages": meta.pages or "",
        }
        # Solo se rellenan los campos que existen en la plantilla del tipo
        # (p. ej. conferencePaper no tiene "issue").
        for field, value in optional.items():
            if field in item and value:
                item[field] = value
        if meta.subjects:
            item["tags"] = [{"tag": subject} for subject in meta.subjects]
        for field, value in optional.items():
            if field in item and value:
                item[field] = value
        if self.collection_key:
            item["collections"] = [self.collection_key]
        return item

    def _create(self, payload: list[dict[str, Any]], parent: str | None = None) -> str:
        response = self.zot.create_items(payload, parentid=parent)
        if response.get("failed"):
            raise SyncError(f"Zotero rechazó el elemento: {response['failed']}")
        return str(response["successful"]["0"]["key"])

    def _attach_linked_pdf(self, parent_key: str, pdf_path: Path) -> str:
        attachment: dict[str, Any] = self.zot.item_template("attachment", "linked_file")
        attachment.update(
            {
                "title": "PDF",
                "path": str(pdf_path),
                "contentType": "application/pdf",
            }
        )
        return self._create([attachment], parent=parent_key)

    # -------------------------------------------------------------------- API
    def sync(self, meta: PaperMetadata, pdf_path: Path) -> str:
        """Crea (o reutiliza) el elemento y le enlaza el PDF. Devuelve la clave.

        Raises:
            SyncError: ante cualquier fallo de la API.
        """
        pdf_path = pdf_path.resolve()
        try:
            key = self.find_existing(meta.doi)
            if key:
                logger.info("Zotero: %s ya existe (%s)", meta.doi, key)
            else:
                key = self._create([self.build_item(meta)])
                logger.info("Zotero: creado %s para %s", key, meta.doi)
            if not self._has_linked_pdf(key, pdf_path):
                att = self._attach_linked_pdf(key, pdf_path)
                logger.info("Zotero: PDF enlazado (%s)", att)
            return key
        except ZOTERO_ERRORS as exc:
            raise SyncError(f"Zotero: {exc}") from exc

    def check(self) -> str:
        """Verifica credenciales; devuelve una descripción de los permisos."""
        try:
            info = self.zot.key_info()
        except ZOTERO_ERRORS as exc:
            raise SyncError(f"Zotero: credenciales inválidas: {exc}") from exc
        access = info.get("access", {}).get("user", {})
        if not access.get("write"):
            raise SyncError("Zotero: la API key no tiene permiso de escritura")
        return f"Zotero OK (usuario {info.get('userID')}, permisos {access})"
