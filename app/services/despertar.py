"""Despierta la API de facturación (servicio gratuito de Render que se duerme tras ~15 min).

Cuando alguien abre el sistema o la tienda, el frontend llama a /api/despertar; el backend
hace un GET a /up de la API de facturación en segundo plano, sin enviar datos. Así, cuando
llegue la primera venta, la API ya está despierta. Como mucho un aviso cada 4 minutos.
"""

import logging
import threading
import time

import requests

log = logging.getLogger(__name__)

_candado = threading.Lock()
_ultimo = 0.0
INTERVALO = 240  # segundos entre avisos


def _avisar(url):
    inicio = time.monotonic()
    try:
        resp = requests.get(url, timeout=90, headers={"User-Agent": "coolbox-backend/despertar"})
        log.info("Despertar facturación: HTTP %s en %.1f s", resp.status_code, time.monotonic() - inicio)
    except requests.RequestException as exc:
        log.warning("Despertar facturación: sin respuesta (%s)", type(exc).__name__)


def despertar(config):
    """Lanza el aviso en segundo plano si toca. Devuelve True si se envió ahora."""
    global _ultimo
    base = (config.get("SUNAT_API_URL") or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        return False
    with _candado:
        ahora = time.monotonic()
        if ahora - _ultimo < INTERVALO:
            return False
        _ultimo = ahora
    threading.Thread(target=_avisar, args=(f"{base}/up",), daemon=True).start()
    return True
