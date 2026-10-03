"""Logging a archivo y consola. Llamar una sola vez, en el entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def _to_level(value: str | int) -> int:
    if isinstance(value, str):
        return getattr(logging, value.upper(), logging.INFO)
    return value


def setup_logging(
    log_dir: str | Path = "logs",
    log_filename: str = "app.log",
    level: str | int = "INFO",
    console_level: str | int | None = None,
) -> logging.Logger:
    """Inicializa el root logger con handlers de archivo y consola.

    Args:
        log_dir: Carpeta del log (se crea si no existe).
        log_filename: Nombre del archivo, sin ruta.
        level: Nivel mínimo para el archivo.
        console_level: Nivel para consola (por defecto, igual que ``level``).
    """
    file_level = _to_level(level)
    cons_level = _to_level(console_level) if console_level is not None else file_level

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path / log_filename, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(cons_level)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(min(file_level, cons_level))
    root.handlers.clear()  # evita duplicados si se llama dos veces
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    return root
