"""Respaldo local de comprobantes: el agente de la PC copia PDF, XML y CDR a una carpeta
organizada (tipo/año/mes/día) para no depender del servidor ni de la nube.

Autenticación servidor a servidor con la cabecera X-API-Key = RESPALDO_API_KEY.
  GET  /api/respaldo/comprobantes?desde=<id>&limite=   índice de archivos (por páginas)
  GET  /api/respaldo/comprobantes/<id>                el archivo (cabecera X-Contenido-SHA256)
  POST /api/respaldo/latido                           estado de la copia local de la PC
"""

import hmac
from hashlib import sha256

from flask import Blueprint, current_app, jsonify, request, send_file

from ..errors import ApiError
from ..extensions import db, limiter
from ..services import archivos
from ..services import procedimientos as sp

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
