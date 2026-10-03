"""Punto de entrada CLI: ``uv run biblio-demon <comando>``.

Comandos:
    run [--dry-run]            Demonio: vigila INBOX_PATH (alias: --daemon).
    process PDF... [--dry-run] Procesa archivos concretos y termina.
    retry                      Reintenta sincronizaciones pendientes.
    check-config               Valida configuración, credenciales y esquema de Notion.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from types import FrameType

from pydantic import ValidationError

from biblio_demon import __version__
from biblio_demon.config import Settings, get_settings
from biblio_demon.logging_setup import setup_logging
from biblio_demon.pipeline import Outcome, Pipeline
from biblio_demon.sync import SyncError

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="biblio-demon",
        description="Pipeline local de catalogación de PDFs científicos vía DOI.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--daemon", action="store_true", help="equivalente a 'run' (modo demonio)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resuelve metadatos pero no mueve archivos ni escribe en Zotero/Notion",
    )
    # --dry-run también se acepta después del subcomando.
    dry = argparse.ArgumentParser(add_help=False)
    dry.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="vigilar INBOX_PATH", parents=[dry])
    proc = sub.add_parser("process", help="procesar PDFs concretos", parents=[dry])
    proc.add_argument("files", nargs="+", type=Path)
    sub.add_parser("retry", help="reintentar sincronizaciones pendientes")
    sub.add_parser("check-config", help="validar configuración y credenciales")
    return parser


def _handle_sigterm(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt  # launchd/systemd envían SIGTERM al detener


def _check_config(settings: Settings, pipeline: Pipeline) -> int:
    ok = True
    print(f"INBOX_PATH:   {settings.inbox_path}")
    print(f"ARCHIVE_PATH: {settings.archive_path}")
    print(f"Estado:       {settings.state_db}")
    if pipeline.zotero:
        print("===" * 10, "Zotero", "===" * 10)
        try:
            print(pipeline.zotero.check())
        except SyncError as exc:
            ok = False
            print(f"X |  {exc}")
    else:
        print("- Zotero desactivado (faltan credenciales)")
    if pipeline.notion:
        print("===" * 10, "Notion", "===" * 10)
        try:
            pipeline.notion.validate_schema()
            print("Notion OK (esquema válido)")
        except SyncError as exc:
            ok = False
            print(f"X |  {exc}")
    else:
        print("===" * 20)
        print("- Notion desactivado (faltan credenciales)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = "run" if args.daemon else args.command
    if command is None:
        _build_parser().print_help()
        return 2

    try:
        settings = get_settings()
    except ValidationError as exc:
        print(f"\nConfiguración inválida:\n{exc}", file=sys.stderr)
        return 2

    setup_logging(
        log_dir=settings.log_dir,
        log_filename="biblio_demon.log",
        level=settings.log_level,
    )
    try:
        settings.ensure_dirs()
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2

    pipeline = Pipeline(settings)

    if command == "check-config":
        return _check_config(settings, pipeline)

    if command == "retry":
        failures = pipeline.retry_pending()
        logger.info("Reintento terminado; %d registros siguen pendientes", failures)
        return 1 if failures else 0

    if command == "process":
        exit_code = 0
        for pdf in args.files:
            result = pipeline.process(pdf.expanduser().resolve(), dry_run=args.dry_run)
            print(f"{pdf.name}: {result.outcome.value}", end="")
            print(f" → {result.destination}" if result.destination else "")
            for reason in result.reasons:
                print(f"    {reason}")
            if result.outcome in {Outcome.MANUAL_REVIEW, Outcome.SYNC_PENDING}:
                exit_code = 1
        return exit_code

    from biblio_demon.watcher import InboxWatcher  # import diferido (watchdog)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    if not args.dry_run:
        pipeline.retry_pending()
    InboxWatcher(pipeline, dry_run=args.dry_run).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
