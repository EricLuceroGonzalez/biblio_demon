"""Punto de entrada: ``uv run python -m biblio_demon``."""

import logging

from biblio_demon.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging(log_filename="biblio_demon.log")
    logger.info("biblio-demon iniciado")


if __name__ == "__main__":
    main()
