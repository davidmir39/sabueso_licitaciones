"""
src/logger.py — Sabueso de Licitaciones
==========================================
Fábrica centralizada de loggers.

Un único punto donde se configura el formato, la rotación de ficheros
y los handlers. Cualquier módulo obtiene su logger con:

    from src.logger import get_logger
    logger = get_logger(__name__)

Nunca configures logging directamente en otros módulos.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

import config

_LOG_CONFIGURED: set[str] = set()


def get_logger(name: str) -> logging.Logger:
    """
    Devuelve un logger nombrado, configurado con:
      · StreamHandler        → consola (stdout)
      · RotatingFileHandler  → logs/sabueso.log (rotación por tamaño)

    Idempotente: llamarlo N veces con el mismo nombre no duplica handlers.
    """
    logger = logging.getLogger(name)

    if name in _LOG_CONFIGURED:
        return logger

    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    logger.setLevel(level)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # — Consola —
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(level)
    logger.addHandler(sh)

    # — Fichero rotativo —
    fh = logging.handlers.RotatingFileHandler(
        filename=config.LOG_DIR / "sabueso.log",
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.setLevel(level)
    logger.addHandler(fh)

    logger.propagate = False
    _LOG_CONFIGURED.add(name)
    return logger
