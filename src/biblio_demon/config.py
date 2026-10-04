"""Configuración del demonio, cargada desde variables de entorno o ``.env``."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Settings(BaseSettings):
    """Variables de configuración validadas.

    Las credenciales de Zotero y Notion son opcionales: si faltan, esa etapa de
    sincronización se omite (útil para ``--dry-run`` o para usar solo una de las
    dos integraciones).
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Rutas -------------------------------------------------------------
    inbox_path: Path
    archive_path: Path
    state_db_path: Path | None = None
    log_dir: Path = Path("logs")
    log_level: str = "INFO"

    # --- Resolución de metadatos ------------------------------------------
    crossref_email: str
    semantic_scholar_api_key: SecretStr | None = None
    http_timeout: float = 20.0
    http_max_attempts: int = Field(default=5, ge=1)
    max_doi_candidates: int = Field(default=5, ge=1)
    verify_title: bool = True
    title_match_threshold: int = Field(default=80, ge=0, le=100)

    # --- Nombre de archivo -------------------------------------------------
    filename_title_words: int = Field(default=6, ge=1)
    drop_leading_stopwords: bool = True

    # --- Watcher -----------------------------------------------------------
    stable_checks: int = Field(default=3, ge=1)
    stable_interval: float = Field(default=1.0, gt=0)

    # --- Zotero ------------------------------------------------------------
    zotero_user_id: str | None = None
    zotero_api_key: SecretStr | None = None
    zotero_library_type: Literal["user", "group"] = "user"
    zotero_collection_key: str | None = None
    # False: solo metadatos, sin adjuntar el PDF (útil si los PDFs se mueven a mano)
    zotero_attach_pdf: bool = False
    # --- Notion ------------------------------------------------------------
    notion_token: SecretStr | None = None
    notion_database_id: str | None = None
    notion_prop_title: str = "Name"
    notion_prop_doi: str = "DOI"
    notion_prop_year: str = "Año"
    notion_prop_status: str = "Estado"
    notion_prop_authors: str = "Autores"
    notion_status_value: str = "Inbox"
    # Opcionales: deja la variable vacía para no usar la propiedad.
    notion_prop_venue: str = "Revista"
    notion_prop_citations: str = "Citas"
    notion_prop_subjects: str = "Áreas"

    @field_validator("crossref_email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        if not _EMAIL_RE.match(value):
            raise ValueError(f"CROSSREF_EMAIL no parece un email válido: {value!r}")
        return value

    @field_validator("inbox_path", "archive_path", "log_dir", "state_db_path")
    @classmethod
    def _expand(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value is not None else None

    @property
    def manual_review_path(self) -> Path:
        """Carpeta para PDFs que requieren revisión manual."""
        return self.inbox_path / "manual_review"

    @property
    def duplicates_path(self) -> Path:
        """Carpeta para PDFs idénticos (mismo hash) a uno ya archivado."""
        return self.inbox_path / "duplicates"

    @property
    def state_db(self) -> Path:
        """Ruta de la base SQLite de estado."""
        return self.state_db_path or self.archive_path / ".biblio_demon.sqlite3"

    @property
    def zotero_enabled(self) -> bool:
        return bool(self.zotero_user_id and self.zotero_api_key)

    @property
    def notion_enabled(self) -> bool:
        return bool(self.notion_token and self.notion_database_id)

    def ensure_dirs(self) -> None:
        """Crea las carpetas de trabajo si no existen."""
        if not self.inbox_path.is_dir():
            raise FileNotFoundError(f"INBOX_PATH no existe: {self.inbox_path}")
        for path in (
            self.archive_path,
            self.manual_review_path,
            self.duplicates_path,
            self.log_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuración (cacheada)."""
    print("Cargando configuración desde variables de entorno o .env...")
    return Settings()  # type: ignore[call-arg]  # campos requeridos vienen del entorno
