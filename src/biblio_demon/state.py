"""Estado persistente en SQLite: qué etapas completó cada DOI.

Permite idempotencia (no duplicar en Zotero/Notion) y reanudar con ``retry``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from biblio_demon.models import PaperMetadata

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    doi            TEXT PRIMARY KEY,
    sha256         TEXT NOT NULL,
    archive_path   TEXT NOT NULL,
    metadata_json  TEXT NOT NULL,
    zotero_key     TEXT,
    notion_page_id TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS files (
    sha256       TEXT PRIMARY KEY,
    doi          TEXT NOT NULL REFERENCES papers(doi),
    archive_path TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass(frozen=True)
class PaperRecord:
    doi: str
    sha256: str
    archive_path: Path
    metadata: PaperMetadata
    zotero_key: str | None
    notion_page_id: str | None


class StateStore:
    """Acceso a la base de estado. Abre una conexión por operación (thread-safe)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _to_record(row: sqlite3.Row) -> PaperRecord:
        return PaperRecord(
            doi=row["doi"],
            sha256=row["sha256"],
            archive_path=Path(row["archive_path"]),
            metadata=PaperMetadata.model_validate_json(row["metadata_json"]),
            zotero_key=row["zotero_key"],
            notion_page_id=row["notion_page_id"],
        )

    def get_by_doi(self, doi: str) -> PaperRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM papers WHERE doi = ?", (doi,)).fetchone()
        return self._to_record(row) if row else None

    def get_by_hash(self, sha256: str) -> PaperRecord | None:
        """Registro del artículo al que pertenece un archivo con este hash."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT p.* FROM papers p JOIN files f ON f.doi = p.doi "
                "WHERE f.sha256 = ?",
                (sha256,),
            ).fetchone()
        return self._to_record(row) if row else None

    def record_archived(
        self, meta: PaperMetadata, sha256: str, archive_path: Path
    ) -> None:
        """Registra un PDF archivado.

        Si el DOI ya existía (otra versión del PDF), se conserva el registro
        original y sus claves de sincronización; el archivo nuevo se añade a
        ``files`` para detectar duplicados exactos.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO papers (doi, sha256, archive_path, metadata_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(doi) DO NOTHING
                """,
                (meta.doi, sha256, str(archive_path), meta.model_dump_json()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO files (sha256, doi, archive_path) "
                "VALUES (?, ?, ?)",
                (sha256, meta.doi, str(archive_path)),
            )

    def set_zotero_key(self, doi: str, key: str) -> None:
        self._set(doi, "zotero_key", key)

    def set_notion_page_id(self, doi: str, page_id: str) -> None:
        self._set(doi, "notion_page_id", page_id)

    def _set(self, doi: str, column: str, value: str) -> None:
        if column not in {"zotero_key", "notion_page_id"}:
            raise ValueError(column)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE papers SET {column} = ?, updated_at = datetime('now') "
                "WHERE doi = ?",
                (value, doi),
            )

    def pending(self, zotero: bool, notion: bool) -> list[PaperRecord]:
        """Registros a los que les falta alguna de las sincronizaciones activas."""
        clauses = []
        if zotero:
            clauses.append("zotero_key IS NULL")
        if notion:
            clauses.append("notion_page_id IS NULL")
        if not clauses:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM papers WHERE {' OR '.join(clauses)} ORDER BY created_at"
            ).fetchall()
        return [self._to_record(r) for r in rows]
