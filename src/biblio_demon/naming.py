"""Construcción del nombre de archivo canónico.

Formato: ``(YYYY)-Primeras_X_Palabras_Titulo-(PrimerAutor-EtAl).pdf``

- 1 autor:   ``(He)``
- 2 autores: ``(He-Zhang)``
- 3 o más:   ``(He-EtAl)``
"""

from __future__ import annotations

import re
import unicodedata

from biblio_demon.models import PaperMetadata

# Letras que NFKD no descompone en ASCII.
_SPECIAL = str.maketrans(
    {
        "ß": "ss",
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
        "þ": "th",
        "Þ": "Th",
        "ı": "i",
    }
)
_ILLEGAL = re.compile(r'[/\\:?*"<>|\x00-\x1f]')
_WORD = re.compile(r"[A-Za-z0-9]+")
LEADING_STOPWORDS = frozenset(
    {"a", "an", "the", "on", "of", "el", "la", "los", "las", "un", "una", "sobre"}
)
MAX_FILENAME_BYTES = 200  # margen frente al límite de 255 bytes de APFS/ext4


def to_ascii(text: str) -> str:
    """Translitera a ASCII eliminando diacríticos (``Müller`` → ``Muller``)."""
    normalized = unicodedata.normalize("NFKD", text.translate(_SPECIAL))
    return normalized.encode("ascii", "ignore").decode("ascii")


def sanitize_filename(name: str) -> str:
    """Sustituye caracteres ilegales en Unix/macOS por ``_``."""
    return _ILLEGAL.sub("_", name).strip(" .")


def title_slug(
    title: str,
    n_words: int,
    drop_leading_stopwords: bool = True,
    separator: str = "-",
) -> str:
    """Primeras ``n_words`` palabras del título, en ASCII, unidas por ``separator``."""
    words = _WORD.findall(to_ascii(title))
    if drop_leading_stopwords:
        while len(words) > 1 and words[0].lower() in LEADING_STOPWORDS:
            words.pop(0)
    return separator.join(words[:n_words]) or "Untitled"


def family_slug(family: str) -> str:
    """Apellido en ASCII sin espacios ni signos (``van der Berg`` → ``vanderBerg``)."""
    return "".join(_WORD.findall(to_ascii(family))) or "Anon"


def authors_slug(meta: PaperMetadata) -> str:
    families = [family_slug(a.family) for a in meta.creators]
    if not families:
        return "Anon"
    if len(families) == 1:
        return families[0]
    if len(families) == 2:
        return f"{families[0]}-{families[1]}"
    return f"{families[0]}-EtAl"


def build_filename(
    meta: PaperMetadata,
    n_words: int = 6,
    drop_leading_stopwords: bool = True,
    separator: str = "-",
) -> str:
    """Nombre de archivo final (con ``.pdf``) para unos metadatos."""
    year = str(meta.year) if meta.year else "nd"
    authors = authors_slug(meta)
    words = n_words
    while True:
        slug = title_slug(meta.title, words, drop_leading_stopwords, separator)
        name = sanitize_filename(f"({year})-{slug}-({authors}).pdf")
        if len(name.encode()) <= MAX_FILENAME_BYTES or words == 1:
            return name
        words -= 1
