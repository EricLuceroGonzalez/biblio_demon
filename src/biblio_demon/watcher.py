"""Vigilancia de ``INBOX_PATH`` con watchdog y cola de procesamiento serializada."""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

import pymupdf
from watchdog.events import (
    FileCreatedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from biblio_demon.pipeline import Pipeline

logger = logging.getLogger(__name__)


def is_candidate_pdf(path: Path) -> bool:
    """``.pdf`` visible (ignora temporales tipo ``._x.pdf`` de macOS)."""
    return path.suffix.lower() == ".pdf" and not path.name.startswith(".")


def wait_until_stable(
    path: Path, checks: int = 3, interval: float = 1.0, timeout: float = 600.0
) -> bool:
    """Espera a que el archivo deje de cambiar y sea un PDF abrible.

    El tamaño y la fecha de modificación deben repetirse ``checks`` sondeos
    seguidos; después se intenta abrir con PyMuPDF.

    Returns:
        ``True`` si el archivo está listo; ``False`` si desapareció o expiró.
    """
    deadline = time.monotonic() + timeout
    last: tuple[int, float] | None = None
    stable = 0
    while time.monotonic() < deadline:
        try:
            st = path.stat()
        except FileNotFoundError:
            return False
        current = (st.st_size, st.st_mtime)
        stable = stable + 1 if current == last and st.st_size > 0 else 0
        last = current
        if stable >= checks:
            try:
                with pymupdf.open(path) as doc:
                    _ = doc.page_count
                return True
            except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError):
                stable = 0  # aún incompleto (p. ej. xref al final sin escribir)
        time.sleep(interval)
    logger.error("%s no se estabilizó en %.0f s", path.name, timeout)
    return False


class InboxHandler(FileSystemEventHandler):
    """Encola PDFs nuevos o movidos dentro de la bandeja (solo nivel superior)."""

    def __init__(self, inbox: Path, work_queue: queue.Queue[Path]) -> None:
        self.inbox = inbox
        self.queue = work_queue

    def _enqueue(self, raw: str | bytes) -> None:
        path = Path(raw.decode() if isinstance(raw, bytes) else raw)
        if path.parent == self.inbox and is_candidate_pdf(path):
            logger.debug("Encolado %s", path.name)
            self.queue.put(path)

    def on_created(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileCreatedEvent):
            self._enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileMovedEvent):
            self._enqueue(event.dest_path)


class InboxWatcher:
    """Observer de watchdog + un hilo trabajador que procesa de uno en uno."""

    def __init__(self, pipeline: Pipeline, dry_run: bool = False) -> None:
        self.pipeline = pipeline
        self.s = pipeline.s
        self.dry_run = dry_run
        self.queue: queue.Queue[Path] = queue.Queue()
        self._stop = threading.Event()
        self._pending: set[Path] = set()
        self._lock = threading.Lock()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                path = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            with self._lock:
                if path in self._pending:
                    self.queue.task_done()
                    continue  # ya se está esperando/procesando
                self._pending.add(path)
            try:
                if wait_until_stable(
                    path, self.s.stable_checks, self.s.stable_interval
                ):
                    result = self.pipeline.process(path, dry_run=self.dry_run)
                    logger.info(
                        "%s → %s %s",
                        path.name,
                        result.outcome.value,
                        result.destination.name if result.destination else "",
                    )
            except Exception:  # el demonio no debe morir por un archivo
                logger.exception("Error inesperado procesando %s", path.name)
            finally:
                with self._lock:
                    self._pending.discard(path)
                self.queue.task_done()

    def scan_existing(self) -> None:
        """Encola los PDFs que ya estaban en la bandeja al arrancar."""
        for path in sorted(self.s.inbox_path.iterdir()):
            if path.is_file() and is_candidate_pdf(path):
                self.queue.put(path)

    def run(self) -> None:
        """Bloquea hasta Ctrl+C / SIGTERM."""
        observer = Observer()
        observer.schedule(
            InboxHandler(self.s.inbox_path, self.queue),
            str(self.s.inbox_path),
            recursive=False,
        )
        worker = threading.Thread(target=self._worker, name="pipeline", daemon=True)
        worker.start()
        observer.start()
        self.scan_existing()
        logger.info("Vigilando %s (dry_run=%s)", self.s.inbox_path, self.dry_run)
        try:
            while not self._stop.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Interrupción recibida, deteniendo…")
        finally:
            observer.stop()
            observer.join()
            self._stop.set()
            worker.join(timeout=30)

    def stop(self) -> None:
        self._stop.set()
