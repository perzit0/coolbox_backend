"""Reglas de seguridad de cuentas: política de contraseñas y bloqueo por intentos.

Los límites se configuran en el entorno: LOGIN_MAX_INTENTOS, LOGIN_MINUTOS_BLOQUEO
y PASSWORD_LONGITUD_MINIMA.
"""

import re
import secrets
import string
from datetime import timedelta

from flask import current_app

from ..errors import ApiError


def max_intentos():
    return current_app.config.get("LOGIN_MAX_INTENTOS", 5)


def minutos_bloqueo():
    return current_app.config.get("LOGIN_MINUTOS_BLOQUEO", 10)


def validar_password(password: str, usuario=None):
    """Longitud mínima, al menos una letra y un número, y distinta del DNI."""
    password = password or ""
    minimo = current_app.config.get("PASSWORD_LONGITUD_MINIMA", 8)
    if len(password) < minimo:
        raise ApiError("password_debil", f"La contraseña debe tener al menos {minimo} caracteres.", 400)
    if len(password) > 128:
        raise ApiError("password_debil", "La contraseña no puede superar los 128 caracteres.", 400)
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ApiError("password_debil", "La contraseña debe combinar letras y números.", 400)
    if usuario is not None and password == usuario.dni:
        raise ApiError("password_debil", "La contraseña no puede ser igual al DNI.", 400)


def generar_password_temporal(n=12):
    """Contraseña temporal legible (sin caracteres ambiguos) con letras y números."""
    letras = "".join(c for c in string.ascii_letters if c not in "lIO")
    digitos = "23456789"
    base = [secrets.choice(letras) for _ in range(n - 2)] + [secrets.choice(digitos) for _ in range(2)]
    secrets.SystemRandom().shuffle(base)
    return "".join(base)


def duracion_bloqueo():
    return timedelta(minutes=minutos_bloqueo())
