"""Reglas de seguridad de cuentas: política de contraseñas y bloqueo por intentos."""
import re
import secrets
import string
from datetime import timedelta

from ..errors import ApiError

MAX_INTENTOS = 5
MINUTOS_BLOQUEO = 10
LONGITUD_MINIMA = 8


def validar_password(password: str, usuario=None):
    """Mínimo 8 caracteres, al menos una letra y un número, y distinta del DNI."""
    password = password or ""
    if len(password) < LONGITUD_MINIMA:
        raise ApiError("password_debil", f"La contraseña debe tener al menos {LONGITUD_MINIMA} caracteres.", 400)
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ApiError("password_debil", "La contraseña debe combinar letras y números.", 400)
    if usuario is not None and password == usuario.dni:
        raise ApiError("password_debil", "La contraseña no puede ser igual al DNI.", 400)


def generar_password_temporal(n=10):
    """Contraseña temporal legible (sin caracteres ambiguos) con letras y números."""
    letras = "".join(c for c in string.ascii_letters if c not in "lIO")
    digitos = "23456789"
    base = [secrets.choice(letras) for _ in range(n - 2)] + [secrets.choice(digitos) for _ in range(2)]
    secrets.SystemRandom().shuffle(base)
    return "".join(base)


def duracion_bloqueo():
    return timedelta(minutes=MINUTOS_BLOQUEO)
