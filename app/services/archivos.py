"""Gestor documental de los comprobantes electrónicos (recomendación del profesor).

Una vez que la SUNAT acepta un comprobante, todo lo que entrega la API de facturación
(PDF, XML firmado y CDR) se descarga una sola vez y queda bajo control del sistema:

    comprobantes/
      boletas/2026/11/17/B001-000037.pdf
      boletas/2026/11/17/B001-000037.xml
      boletas/2026/11/17/B001-000037-CDR.zip
      facturas/...   notas_credito/...

- Carpeta local (COMPROBANTES_DIR): es la que se lee al pulsar PDF, XML o CDR; no se
  vuelve a consultar la API, así un corte de la SUNAT no impide ver lo ya emitido.
- Nube (Supabase Storage, bucket privado): copia de cada archivo con la misma ruta. El
  disco de Render se borra en cada despliegue; si falta un archivo local, se vuelve a
  bajar de la nube y se guarda otra vez en la carpeta.
- Índice (tabla comprobante_archivo): tipo, serie, cliente, origen, tamaño y huella
  SHA-256 de cada archivo, para encontrarlo por fecha o cliente y verificar que no fue
  alterado. Se escribe con el procedimiento sp_archivo_registrar.

Si la nube no está configurada, todo funciona solo con la carpeta local.
"""

import hashlib
import io
import logging
import re
import zipfile
from pathlib import Path
from urllib.parse import quote

import requests
from flask import current_app

from . import procedimientos as sp
from .tiempo import a_local

log = logging.getLogger("coolbox.archivos")

CARPETAS = {"boleta": "boletas", "factura": "facturas", "nota_credito": "notas_credito"}
EXTENSION = {"pdf": "pdf", "xml": "xml", "cdr": "zip"}
MIME = {"pdf": "application/pdf", "xml": "application/xml", "cdr": "application/zip"}

_buckets_listos = set()


def _cfg(clave, defecto=""):
    return current_app.config.get(clave, defecto)


def base():
    return Path(_cfg("COMPROBANTES_DIR") or "comprobantes").resolve()


def huella(contenido):
    return hashlib.sha256(contenido).hexdigest()


def ruta_relativa(tipo, fecha, serie, numero, formato, respaldo):
    """<tipo>/<AAAA>/<MM>/<DD>/<serie-numero>[-CDR].<ext> con la fecha local de emisión."""
    local = a_local(fecha)
    nombre = re.sub(r"[^A-Za-z0-9_-]", "", f"{serie}-{numero}") or re.sub(r"[^A-Za-z0-9_-]", "", respaldo or "doc")
    sufijo = "-CDR" if formato == "cdr" else ""
    return f"{CARPETAS[tipo]}/{local:%Y}/{local:%m}/{local:%d}/{nombre}{sufijo}.{EXTENSION[formato]}"


def _dentro(path):
    """Solo rutas dentro de la carpeta de comprobantes (una ruta manipulada no sale de ella)."""
    raiz = base()
    archivo = Path(path).resolve()
    return archivo if raiz in archivo.parents else None


def resolver(ruta):
    """Archivo local de una ruta guardada, si existe. Acepta también las rutas antiguas
    (comprobantes/boletas/AAAA-MM/DD/...), guardadas relativas a la carpeta del backend."""
    if not ruta:
        return None
    for candidato in (base() / ruta, Path(ruta)):
        archivo = _dentro(candidato)
        if archivo and archivo.is_file():
            return archivo
    return None


# ------------------------------------------------------------------ nube (Supabase Storage)
def nube_activa():
    return bool(_cfg("SUPABASE_URL") and _cfg("SUPABASE_SERVICE_KEY"))


def _cabeceras_nube(extra=None):
    clave = _cfg("SUPABASE_SERVICE_KEY")
    cabeceras = {"apikey": clave, "Authorization": f"Bearer {clave}"}
    cabeceras.update(extra or {})
    return cabeceras


def _url_nube(sufijo):
    return _cfg("SUPABASE_URL").rstrip("/") + "/storage/v1" + sufijo


def _asegurar_bucket():
    bucket = _cfg("COMPROBANTES_BUCKET") or "comprobantes"
    llave = (_cfg("SUPABASE_URL"), bucket)
    if llave in _buckets_listos:
        return bucket
    resp = requests.post(
        _url_nube("/bucket"),
        json={"id": bucket, "name": bucket, "public": False},
        headers=_cabeceras_nube(),
        timeout=15,
    )
    if resp.status_code < 300 or resp.status_code == 409 or "exist" in resp.text.lower():
        _buckets_listos.add(llave)
    else:
        log.warning("No se pudo crear el bucket %s: %s %s", bucket, resp.status_code, resp.text[:200])
    return bucket


def subir(ruta, contenido, formato):
    if not nube_activa():
        return False
    try:
        bucket = _asegurar_bucket()
        resp = requests.post(
            _url_nube(f"/object/{bucket}/{quote(ruta)}"),
            data=contenido,
            headers=_cabeceras_nube({"Content-Type": MIME[formato], "x-upsert": "true"}),
            timeout=30,
        )
        if resp.status_code >= 300:
            log.warning("Nube: no se subió %s: %s %s", ruta, resp.status_code, resp.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        log.warning("Nube: no se subió %s: %s", ruta, exc)
        return False


def bajar(ruta):
    if not nube_activa():
        return None
    try:
        bucket = _cfg("COMPROBANTES_BUCKET") or "comprobantes"
        resp = requests.get(_url_nube(f"/object/{bucket}/{quote(ruta)}"), headers=_cabeceras_nube(), timeout=30)
        return resp.content if resp.status_code == 200 else None
    except requests.RequestException as exc:
        log.warning("Nube: no se bajó %s: %s", ruta, exc)
        return None


# ------------------------------------------------------------------ guardar y obtener
def _registrar(ruta, formato, contenido, en_nube, meta):
    if not sp.disponibles():
        return
    datos = dict(meta or {})
    datos.update(
        {"ruta": ruta, "formato": formato, "bytes": len(contenido), "sha256": huella(contenido), "en_nube": en_nube}
    )
    sp.valor("sp_archivo_registrar", sp.json_param(datos))


def guardar(ruta, contenido, formato, meta=None):
    """Escribe el archivo en la carpeta local, lo copia a la nube y lo registra en el índice."""
    destino = base() / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(contenido)
    en_nube = subir(ruta, contenido, formato)
    _registrar(ruta, formato, contenido, en_nube, meta)
    return ruta


def _huella_registrada(ruta):
    if not sp.disponibles():
        return None
    fila = sp.fila("sp_archivo_obtener", ruta)
    return fila.get("sha256") if fila else None


def obtener(ruta):
    """Archivo local listo para entregar. Si no está en la carpeta (disco nuevo de Render),
    se baja de la nube, se comprueba su huella SHA-256 y se guarda otra vez en la carpeta."""
    archivo = resolver(ruta)
    if archivo or not ruta:
        return archivo
    contenido = bajar(ruta)
    if contenido is None:
        return None
    esperada = _huella_registrada(ruta)
    if esperada and esperada != huella(contenido):
        log.error("Nube: la huella de %s no coincide con el índice; no se entrega.", ruta)
        return None
    destino = _dentro(base() / ruta)
    if not destino:
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(contenido)
    return destino


def verificar(ruta):
    """Compara la huella del archivo local y de la nube con la del índice."""
    esperada = _huella_registrada(ruta)
    archivo = resolver(ruta)
    local = huella(archivo.read_bytes()) if archivo else None
    nube = bajar(ruta) if nube_activa() else None
    nube_huella = huella(nube) if nube is not None else None
    return {
        "sha256": esperada,
        "local": "sin_copia" if local is None else ("integro" if local == esperada else "alterado"),
        "nube": "no_configurada"
        if not nube_activa()
        else ("sin_copia" if nube_huella is None else ("integro" if nube_huella == esperada else "alterado")),
    }


def leer_cdr(contenido):
    """Código y descripción de la respuesta de la SUNAT dentro de la CDR (R-*.xml)."""
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
            xml = next((zf.read(n) for n in zf.namelist() if n.lower().endswith(".xml")), b"").decode("utf-8", "ignore")
    except (zipfile.BadZipFile, KeyError):
        return None, None
    codigo = re.search(r"<cbc:ResponseCode[^>]*>([^<]+)</cbc:ResponseCode>", xml)
    descripcion = re.search(r"<cbc:Description[^>]*>([^<]+)</cbc:Description>", xml)
    return (codigo.group(1).strip() if codigo else None), (descripcion.group(1).strip() if descripcion else None)
