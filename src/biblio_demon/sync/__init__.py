"""Integraciones de sincronización (Zotero, Notion)."""

from __future__ import annotations


class SyncError(Exception):
    """Fallo al sincronizar con un servicio externo."""
