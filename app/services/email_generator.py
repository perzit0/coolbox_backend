"""Generación automática del correo institucional de Coolbox.

Regla acordada con el equipo:
- Primera letra del primer nombre + primer apellido completo + primera
  letra del segundo apellido, todo en minúsculas y sin tildes.
- Si el correo ya existe, se agrega un contador (1, 2, 3, ...) al final del
  local part hasta encontrar uno libre.

Ejemplo:
    "Juan Daniel Pérez Lozano" -> "jperezl@coolbox.com.pe"
    Duplicado -> "jperezl1@coolbox.com.pe"
"""
import unicodedata
import re


def _slug(text: str) -> str:
    """Minúsculas, sin tildes, solo letras a-z."""
    text = text or ""
    # Descomponer acentos y quitar marcas
    normalizado = unicodedata.normalize("NFKD", text)
    sin_acentos = "".join(c for c in normalizado if not unicodedata.combining(c))
    solo_letras = re.sub(r"[^A-Za-z]", "", sin_acentos)
    return solo_letras.lower()


def _primer_token(text: str) -> str:
    """Devuelve la primera palabra normalizada del texto."""
    if not text:
        return ""
    partes = [p for p in re.split(r"\s+", text.strip()) if p]
    return _slug(partes[0]) if partes else ""


def local_part(nombres: str, apellido_paterno: str, apellido_materno: str) -> str:
    """Construye la parte local del correo (antes del @)."""
    n = _primer_token(nombres)
    ap = _slug(apellido_paterno)  # apellido paterno completo
    am = _primer_token(apellido_materno)  # letra inicial

    inicial_nombre = n[0] if n else ""
    inicial_materno = am[0] if am else ""

    base = f"{inicial_nombre}{ap}{inicial_materno}".strip()
    if not base:
        base = "usuario"
    return base


def build_email(nombres: str, apellido_paterno: str, apellido_materno: str,
                dominio: str, existe_email) -> str:
    """Genera un correo único.

    `existe_email(email) -> bool` es un callable que consulta si ya existe
    un usuario con ese correo. Devuelve el primer email disponible.
    """
    base = local_part(nombres, apellido_paterno, apellido_materno)
    candidato = f"{base}@{dominio}"
    if not existe_email(candidato):
        return candidato

    contador = 1
    while True:
        candidato = f"{base}{contador}@{dominio}"
        if not existe_email(candidato):
            return candidato
        contador += 1
        if contador > 9999:
            raise RuntimeError("No fue posible generar un correo único.")
