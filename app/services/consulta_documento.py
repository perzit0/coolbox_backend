"""Consulta del nombre del cliente por su documento (RENIEC para DNI, SUNAT para RUC).

Usa un servicio externo configurable (CONSULTA_DNI_URL, CONSULTA_RUC_URL y CONSULTA_TOKEN),
por ejemplo Decolecta, que da un token gratuito. El formato de respuesta varía según el
proveedor, por eso se aceptan los nombres de campo más comunes. Si no hay token o el
servicio no responde, la caja escribe el nombre a mano.
"""

import logging
import re

import requests
from flask import current_app

from ..errors import ApiError

log = logging.getLogger("coolbox.consulta_documento")


def _nombre_persona(d):
    completo = d.get("full_name") or d.get("nombre_completo") or d.get("nombreCompleto")
    if completo:
        return completo
    partes = [
        d.get("first_name") or d.get("nombres"),
        d.get("first_last_name") or d.get("apellidoPaterno") or d.get("apellido_paterno"),
        d.get("second_last_name") or d.get("apellidoMaterno") or d.get("apellido_materno"),
    ]
    return " ".join(p for p in partes if p)


def _nombre_empresa(d):
    return d.get("razon_social") or d.get("razonSocial") or d.get("nombre") or ""


def consultar(numero):
    """{'documento', 'tipo', 'nombre', 'fuente'} o ApiError si no se puede consultar."""
    numero = re.sub(r"\D", "", str(numero or ""))
    if len(numero) not in (8, 11):
        raise ApiError("documento_invalido", "Solo se consultan DNI (8 dígitos) o RUC (11).", 400)
    cfg = current_app.config
    if not cfg.get("CONSULTA_TOKEN"):
        raise ApiError(
            "consulta_no_configurada", "La consulta a RENIEC/SUNAT no está configurada: escriba el nombre.", 503
        )
    es_ruc = len(numero) == 11
    url = (cfg["CONSULTA_RUC_URL"] if es_ruc else cfg["CONSULTA_DNI_URL"]).format(numero=numero)
    try:
        resp = requests.get(
            url,
            timeout=cfg.get("CONSULTA_TIMEOUT", 8),
            headers={"Authorization": f"Bearer {cfg['CONSULTA_TOKEN']}", "Accept": "application/json"},
        )
    except requests.RequestException as exc:
        log.warning("Consulta de %s sin respuesta: %s", "RUC" if es_ruc else "DNI", exc)
        raise ApiError("consulta_sin_respuesta", "El servicio de consulta no respondió: escriba el nombre.", 503) from None
    if resp.status_code == 404:
        raise ApiError("documento_no_encontrado", "No se encontró el documento. Verifíquelo o escriba el nombre.", 404)
    if resp.status_code >= 400:
        log.warning("Consulta de documento respondió %s", resp.status_code)
        raise ApiError("consulta_sin_respuesta", "El servicio de consulta no está disponible: escriba el nombre.", 503)
    try:
        datos = resp.json()
    except ValueError:
        raise ApiError("consulta_sin_respuesta", "El servicio de consulta respondió en un formato inesperado.", 503) from None
    datos = datos.get("data", datos) if isinstance(datos, dict) else {}
    nombre = (_nombre_empresa(datos) if es_ruc else _nombre_persona(datos)).strip()
    if not nombre:
        raise ApiError("documento_no_encontrado", "No se encontró el documento. Verifíquelo o escriba el nombre.", 404)
    return {
        "documento": numero,
        "tipo": "RUC" if es_ruc else "DNI",
        "nombre": nombre[:180],
        "fuente": "SUNAT" if es_ruc else "RENIEC",
    }
