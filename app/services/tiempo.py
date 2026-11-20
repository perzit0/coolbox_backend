"""Zona horaria del negocio (ZONA_HORARIA_HORAS; Perú es UTC-5 todo el año)."""

from datetime import timedelta, timezone

from flask import current_app


def zona_local():
    return timezone(timedelta(hours=current_app.config.get("ZONA_HORARIA_HORAS", -5)))


def a_local(dt):
    """Convierte una fecha (UTC; SQLite la devuelve sin zona) a la hora local."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zona_local())


def fecha_local(dt):
    return a_local(dt).date()
