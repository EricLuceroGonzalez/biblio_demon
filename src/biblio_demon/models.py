"""Modelos de datos compartidos entre etapas del pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

ItemType = Literal[
    "journalArticle", "conferencePaper", "preprint", "bookSection", "book", "report"
]
MetadataSource = Literal["crossref", "datacite", "semantic_scholar"]


class Author(BaseModel):
    """Autor. En autores institucionales el nombre completo va en ``family``."""

    family: str
    given: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.given} {self.family}".strip()


class PaperMetadata(BaseModel):
    """Esquema normalizado de metadatos canónicos de un artículo."""

    doi: str = Field(pattern=r"^10\.\d{4,9}/\S+$")
    title: str = Field(min_length=1)
    creators: list[Author] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1000, le=2100)
    abstract: str | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    subjects: list[str] = Field(default_factory=list)
    citation_count: int | None = Field(default=None, ge=0)
    item_type: ItemType = "journalArticle"
    source: MetadataSource

    @computed_field  # type: ignore[prop-decorator]
    @property
    def authors(self) -> list[str]:
        """Nombres completos de los autores, en orden."""
        return [a.full_name for a in self.creators]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def first_author_family(self) -> str:
        """Apellido del primer autor tal como lo da la API (con diacríticos)."""
        return self.creators[0].family if self.creators else "Anon"

    @property
    def doi_url(self) -> str:
        return f"https://doi.org/{self.doi}"
