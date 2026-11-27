"""Cifrado simétrico (Fernet: AES-128 + HMAC) para los tokens de las APIs de otras empresas.

La llave se deriva de CIFRADO_CLAVE o, si no existe, de SECRET_KEY. Si esa variable cambia,
los tokens guardados ya no se pueden leer y hay que volver a ingresarlos en el panel.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


def _fernet():
    secreto = current_app.config.get("CIFRADO_CLAVE") or current_app.config["SECRET_KEY"]
    llave = hashlib.sha256(b"coolbox-integracion:" + secreto.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(llave))


def cifrar(texto):
    return _fernet().encrypt(texto.encode()).decode() if texto else None


def descifrar(token):
    """Texto original, o None si no hay valor o la llave cambió."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def huella(texto):
    return hashlib.sha256(texto.encode()).hexdigest()
