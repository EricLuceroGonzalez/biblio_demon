"""Operaciones de sistema de archivos: hash, archivado, revisión manual."""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Hash SHA-256 del contenido del archivo."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def unique_destination(directory: Path, filename: str, file_hash: str) -> Path:
    """Ruta libre en ``directory``: añade sufijo de hash y, si hace falta, timestamp."""
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    target = directory / f"{stem}-{file_hash[:8]}{suffix}"
    if not target.exists():
        return target
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return directory / f"{stem}-{file_hash[:8]}-{stamp}{suffix}"


def move_file(src: Path, dst: Path) -> Path:
    """Mueve ``src`` a ``dst`` sin sobrescribir (funciona entre volúmenes)."""
    if dst.exists():
        raise FileExistsError(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    logger.info("Movido %s → %s", src.name, dst)
    return dst


def quarantine(src: Path, directory: Path, reason: str) -> Path:
    """Mueve ``src`` a ``directory`` y deja al lado un ``.txt`` con el motivo."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / src.name
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        target = directory / f"{src.stem}-{stamp}{src.suffix}"
    move_file(src, target)
    note = target.with_suffix(target.suffix + ".txt")
    note.write_text(
        f"{datetime.now().isoformat(timespec='seconds')}\n{reason}\n", encoding="utf-8"
    )
    logger.warning("%s → %s: %s", src.name, directory.name, reason)
    return target
