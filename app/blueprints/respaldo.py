"""Respaldo local de comprobantes: el agente de la PC copia PDF, XML y CDR a una carpeta
organizada (tipo/año/mes/día) para no depender del servidor ni de la nube.

Autenticación servidor a servidor con la cabecera X-API-Key = RESPALDO_API_KEY.
  GET  /api/respaldo/comprobantes?desde=<id>&limite=   índice de archivos (por páginas)
  GET  /api/respaldo/comprobantes/<id>                el archivo (cabecera X-Contenido-SHA256)
  POST /api/respaldo/latido                           estado de la copia local de la PC
  POST /api/respaldo/devolver?ruta=<ruta>             la PC devuelve un archivo que el servidor perdió
"""

import hmac
import re
from hashlib import sha256

from flask import Blueprint, current_app, jsonify, request, send_file

from ..errors import ApiError
from ..extensions import db, limiter
from ..services import archivos
from ..services import procedimientos as sp
from ..services import sunat as sunat_svc

bp = Blueprint("respaldo", __name__)


def _autorizar():
    esperada = current_app.config.get("RESPALDO_API_KEY") or ""
    if len(esperada) < 32:
        raise ApiError("respaldo_no_configurado", "El respaldo local no está habilitado (falta RESPALDO_API_KEY).", 503)
    recibida = request.headers.get("X-API-Key") or ""
    if not hmac.compare_digest(sha256(recibida.encode()).digest(), sha256(esperada.encode()).digest()):
        raise ApiError("clave_invalida", "Clave de respaldo inválida.", 401)
    sp.exigir()


@bp.get("/respaldo/comprobantes")
@limiter.limit("120 per minute")
def indice():
    _autorizar()
    desde = max(request.args.get("desde", 0, type=int) or 0, 0)
    limite = min(max(request.args.get("limite", 500, type=int) or 500, 1), 1000)
    filas = sp.columna("sp_respaldo_archivos", "archivo", desde, limite)
    for f in filas:  # ¿el servidor lo puede entregar? Si no, la PC se lo devuelve
        f["en_servidor"] = bool(f.get("en_nube") or archivos.resolver(f["ruta"]))
    return jsonify(archivos=filas, siguiente=filas[-1]["id"] if len(filas) == limite else None)


@bp.get("/respaldo/comprobantes/<int:archivo_id>")
@limiter.limit("600 per minute")
def archivo(archivo_id):
    _autorizar()
    fila = sp.fila("sp_archivo_por_id", archivo_id)
    if not fila:
        raise ApiError("archivo_no_encontrado", "El archivo no existe en el índice.", 404)
    ruta = archivos.obtener(fila["ruta"])  # carpeta del servidor o, si no está, la nube
    if not ruta:
        raise ApiError("archivo_no_disponible", "El archivo no está en el servidor ni en la nube por ahora.", 404)
    resp = send_file(ruta, as_attachment=True, download_name=ruta.name, max_age=0)
    resp.headers["X-Contenido-SHA256"] = fila["sha256"]
    return resp


@bp.post("/respaldo/latido")
@limiter.limit("60 per minute")
def latido():
    _autorizar()
    datos = request.get_json(silent=True) or {}
    campos = ("equipo", "carpeta", "archivos", "bytes", "descargados", "pendientes", "errores")
    sp.valor("sp_respaldo_latido", sp.json_param({k: datos.get(k) for k in campos}))
    db.session.commit()
    return jsonify(ok=True)


# <tipo>/<AAAA>/<MM>/<DD>/<serie-numero>/<serie-numero>[-CDR].<ext>
RUTA_DEVOLVER = re.compile(
    r"^(boletas|facturas|notas_credito)/\d{4}/\d{2}/\d{2}/([A-Z0-9]{4})-(\d{1,8})/\2-\3(-CDR)?\.(pdf|xml|zip)$"
)
FORMATO_POR_EXT = {"pdf": "pdf", "xml": "xml", "zip": "cdr"}


def _documento_de(ruta, serie, numero, formato):
    """Comprobante aceptado cuya ruta esperada para ese formato es exactamente `ruta`."""
    from .comprobantes import _documentos  # import diferido: evita el ciclo entre blueprints

    for doc, documento in _documentos():
        if doc.comprobante_serie != serie or str(doc.comprobante_numero or "").lstrip("0") != numero.lstrip("0"):
            continue
        esperada = archivos.ruta_relativa(
            documento, doc.fecha, doc.comprobante_serie, doc.comprobante_numero, formato, doc.codigo
        )
        if esperada == ruta:
            return doc, documento
    return None, None


def _contenido_valido(contenido, formato, serie, numero):
    if formato == "pdf":
        return contenido[:5] == b"%PDF-"
    if formato == "xml":
        texto = contenido[:200_000].decode("utf-8", "ignore")
        ids = re.findall(r"<cbc:ID>\s*([A-Z0-9]{4})-0*(\d+)\s*</cbc:ID>", texto)
        return any(s == serie and n == numero.lstrip("0") for s, n in ids)
    codigo, _ = archivos.leer_cdr(contenido)  # CDR: zip con la respuesta de la SUNAT de ese comprobante
    return codigo is not None and f"{serie}-{numero.lstrip('0')}" in _ids_cdr(contenido)


def _ids_cdr(contenido):
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
        nombres = " ".join(zf.namelist())
        texto = " ".join(
            zf.read(i).decode("utf-8", "ignore")
            for i in zf.infolist()
            if i.filename.lower().endswith(".xml") and i.file_size <= archivos.MAX_XML_CDR
        )
    return {f"{s}-{n.lstrip('0')}" for s, n in re.findall(r"([A-Z0-9]{4})-0*(\d+)", nombres + " " + texto)}


@bp.post("/respaldo/devolver")
@limiter.limit("120 per minute")
def devolver():
    """La PC devuelve un archivo que el servidor ya no tiene (Render borra su disco en cada
    despliegue). Se acepta solo si:
    - la ruta ya está en el índice y el contenido tiene exactamente su huella SHA-256, o
    - la ruta es la esperada de un comprobante aceptado que no tiene ese archivo, y el
      contenido corresponde a ese comprobante (XML y CDR con su serie-número, PDF válido)."""
    _autorizar()
    ruta = (request.args.get("ruta") or "").strip()
    contenido = request.get_data(cache=False)
    if not contenido or len(contenido) > sunat_svc.MAX_ARCHIVO:
        raise ApiError("archivo_invalido", "Archivo vacío o demasiado grande.", 400)
    coincide = RUTA_DEVOLVER.match(ruta)
    if not coincide:
        raise ApiError("ruta_invalida", "La ruta no tiene el formato de un comprobante.", 400)
    _, serie, numero, _, ext = coincide.groups()
    formato = FORMATO_POR_EXT[ext]

    fila = sp.fila("sp_archivo_obtener", ruta)
    if fila:
        if archivos.huella(contenido) != fila["sha256"]:
            raise ApiError("huella_distinta", "El archivo no coincide con el registrado.", 409)
        if fila.get("en_nube") and archivos.resolver(ruta):
            return jsonify(ok=True, accion="ya_estaba")
        archivos.guardar(ruta, contenido, formato, {
            "tipo": fila["tipo"], "serie": fila["serie"], "numero": fila["numero"],
            "fecha_emision": str(fila["fecha_emision"]), "cliente_documento": fila.get("cliente_documento"),
            "cliente_nombre": fila.get("cliente_nombre"), "origen": fila.get("origen"),
            "origen_codigo": fila.get("origen_codigo"),
        })
        db.session.commit()
        return jsonify(ok=True, accion="restaurado")

    doc, documento = _documento_de(ruta, serie, numero, formato)
    if doc is None:
        raise ApiError("sin_comprobante", "Ningún comprobante aceptado usa esa ruta.", 404)
    columna = sunat_svc.FORMATOS[formato][0]
    actual = getattr(doc, columna)
    if actual and sp.fila("sp_archivo_obtener", actual) and archivos.obtener(actual):
        raise ApiError("ya_tiene_archivo", "El comprobante ya tiene ese archivo.", 409)
    if not _contenido_valido(contenido, formato, serie, numero):
        raise ApiError("no_corresponde", "El contenido no corresponde a ese comprobante.", 422)
    archivos.guardar(ruta, contenido, formato, sunat_svc._meta(doc, documento))
    setattr(doc, columna, ruta)
    db.session.commit()
    return jsonify(ok=True, accion="importado")
