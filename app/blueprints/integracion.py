"""API de clientes para el grupo pareado (ida y vuelta) y su panel de administración.

API expuesta al otro grupo (servidor a servidor, con clave en la cabecera X-API-Key o
Authorization: Bearer <clave>; las claves están en INTEGRACION_API_KEYS):
  GET  /api/integracion/estructura                         contrato: campos y clave única
  GET  /api/integracion/clientes?desde=&pagina=&por_pagina= nuestros clientes (paginado)
  GET  /api/integracion/clientes/<tipo>/<numero>           un cliente
  POST /api/integracion/clientes                           el otro grupo nos envía sus clientes

Panel (personal con el permiso integracion.gestionar):
  GET  /api/integracion/estado
  POST /api/integracion/importar               lee la API del otro grupo y la carga
  POST /api/integracion/importar-archivo       carga un JSON con clientes
  GET  /api/integracion/lotes
  GET  /api/integracion/lotes/<id>?estado=      lote con sus registros de la tabla stage
  GET  /api/integracion/traza?direccion=

Toda llamada a la API expuesta queda en la traza (quién, cuándo, resultado, registros).
"""

import time
from datetime import datetime
from math import ceil
from urllib.parse import urlencode

from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import limiter
from ..services import integracion as integ
from ..services import procedimientos as sp
from .auth import _usuario_actual, require_permiso

bp = Blueprint("integracion", __name__)
PERMISO = "integracion.gestionar"

CAMPOS = [
    {"campo": "clave", "tipo": "texto", "descripcion": "tipo_documento-numero_documento (identificador único)"},
    {"campo": "tipo_documento", "tipo": "texto", "descripcion": "DNI, CE, RUC o PASAPORTE"},
    {
        "campo": "codigo_tipo_documento",
        "tipo": "texto",
        "descripcion": "Catálogo 06 SUNAT: 1 DNI, 4 CE, 6 RUC, 7 pasaporte",
    },
    {"campo": "numero_documento", "tipo": "texto", "descripcion": "Número de documento sin espacios"},
    {"campo": "nombres", "tipo": "texto", "descripcion": "Nombres (personas)"},
    {"campo": "apellidos", "tipo": "texto", "descripcion": "Apellidos (personas)"},
    {"campo": "razon_social", "tipo": "texto", "descripcion": "Razón social (empresas con RUC)"},
    {"campo": "nombre_completo", "tipo": "texto", "descripcion": "Razón social o nombres y apellidos"},
    {"campo": "email", "tipo": "texto", "descripcion": "Correo electrónico (opcional)"},
    {"campo": "telefono", "tipo": "texto", "descripcion": "Teléfono o celular (opcional)"},
    {"campo": "direccion", "tipo": "texto", "descripcion": "Dirección (opcional)"},
    {"campo": "fecha_registro", "tipo": "fecha ISO-8601", "descripcion": "Alta en nuestro sistema"},
    {"campo": "fecha_actualizacion", "tipo": "fecha ISO-8601", "descripcion": "Último cambio (para ?desde=)"},
]


def _limite():
    return current_app.config["LIMITE_INTEGRACION"]


def _ip():
    return request.remote_addr


# ------------------------------------------------------------------ autenticación por clave


def _clave_recibida():
    clave = request.headers.get("X-API-Key")
    if not clave:
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            clave = auth[7:]
    return (clave or "").strip()


def _sistema_autorizado():
    """Valida la clave del sistema que llama. Deja la traza de los intentos rechazados."""
    g.integ_inicio = time.monotonic()
    if not integ.claves_entrantes():
        integ.trazar(
            "entrada", None, request.method, request.url, 503, False, mensaje="API sin claves configuradas", ip=_ip()
        )
        raise ApiError("integracion_no_configurada", "La API de clientes no está habilitada.", 503)
    sistema = integ.sistema_por_clave(_clave_recibida())
    if not sistema:
        integ.trazar(
            "entrada", None, request.method, request.url, 401, False, mensaje="Clave inválida o ausente", ip=_ip()
        )
        raise ApiError("clave_invalida", "Clave de API inválida o ausente (cabecera X-API-Key).", 401)
    sp.exigir()
    return sistema


def _trazar_ok(sistema, registros, mensaje="Correcto", estado=200, lote_id=None):
    ms = int((time.monotonic() - g.get("integ_inicio", time.monotonic())) * 1000)
    integ.trazar(
        "entrada", sistema, request.method, request.url, estado, True, 1, ms, registros, mensaje, _ip(), lote_id
    )


# ------------------------------------------------------------------ API expuesta (ida)


@bp.get("/integracion/estructura")
@limiter.limit(_limite)
def estructura():
    sistema = _sistema_autorizado()
    _trazar_ok(sistema, None, "Consulta de estructura")
    return jsonify(
        sistema=current_app.config.get("INTEGRACION_SISTEMA"),
        clave_unica=["tipo_documento", "numero_documento"],
        campos=CAMPOS,
        consulta={
            "listado": "GET /api/integracion/clientes?pagina=1&por_pagina=100&desde=2026-11-01T00:00:00Z",
            "uno": "GET /api/integracion/clientes/<tipo_documento>/<numero_documento>",
            "envio": 'POST /api/integracion/clientes  {"clientes": [ ... ]}',
            "autenticacion": "Cabecera X-API-Key: <clave> (o Authorization: Bearer <clave>)",
        },
    )


@bp.get("/integracion/clientes")
@limiter.limit(_limite)
def exportar():
    sistema = _sistema_autorizado()
    pagina = max(request.args.get("pagina", 1, type=int) or 1, 1)
    por_pagina = min(max(request.args.get("por_pagina", 100, type=int) or 100, 1), 500)
    desde = None
    if request.args.get("desde"):
        try:
            desde = datetime.fromisoformat(request.args["desde"].replace("Z", "+00:00"))
        except ValueError:
            raise ApiError("fecha_invalida", "El parámetro desde debe ser una fecha ISO-8601.", 400)
    filas = sp.filas("sp_clientes_exportar", desde, por_pagina, (pagina - 1) * por_pagina)
    total = filas[0]["total"] if filas else 0
    paginas = ceil(total / por_pagina) if total else 0
    siguiente = None
    if pagina < paginas:
        args = request.args.to_dict()
        args.update(pagina=pagina + 1, por_pagina=por_pagina)
        siguiente = request.base_url + "?" + urlencode(args)
    clientes = [f["cliente"] for f in filas]
    _trazar_ok(sistema, len(clientes), f"Página {pagina} de {paginas or 1}")
    return jsonify(
        sistema=current_app.config.get("INTEGRACION_SISTEMA"),
        clave_unica=["tipo_documento", "numero_documento"],
        total=total,
        pagina=pagina,
        por_pagina=por_pagina,
        total_paginas=paginas,
        siguiente=siguiente,
        clientes=clientes,
    )


@bp.get("/integracion/clientes/<tipo>/<numero>")
@limiter.limit(_limite)
def exportar_uno(tipo, numero):
    sistema = _sistema_autorizado()
    encontrado = sp.columna("sp_cliente_buscar", "cliente", tipo, numero)
    if not encontrado:
        integ.trazar("entrada", sistema, "GET", request.url, 404, False, mensaje="Cliente no encontrado", ip=_ip())
        raise ApiError("cliente_no_encontrado", "No existe un cliente con ese documento.", 404)
    c = encontrado[0]
    _trazar_ok(sistema, 1)
    return jsonify(
        cliente={
            "clave": c["clave"],
            "tipo_documento": c["tipo_documento"],
            "codigo_tipo_documento": c["codigo_sunat"],
            "numero_documento": c["nro_documento"],
            "nombres": c["nombres"],
            "apellidos": c["apellidos"],
            "razon_social": c["razon_social"],
            "nombre_completo": c["nombre_completo"],
            "email": c["email"],
            "telefono": c["telefono"],
            "direccion": c["direccion"],
            "fecha_registro": c["fecha_registro"],
            "fecha_actualizacion": c["fecha_actualizacion"],
        }
    )


@bp.post("/integracion/clientes")
@limiter.limit(_limite)
def recibir():
    """El otro grupo nos envía sus clientes: van a la tabla stage y de ahí al maestro."""
    sistema = _sistema_autorizado()
    cuerpo = request.get_json(silent=True)
    registros = integ.extraer_registros(cuerpo)
    lote = integ.recibir(sistema, "recepcion", registros, url=request.base_url)
    _trazar_ok(sistema, lote["total"], lote.get("mensaje") or "Recibido", 201, lote["id"])
    rechazados = [
        {"fila": r["fila"], "documento": r["nro_documento"], "motivo": r["mensaje"]}
        for r in sp.columna("sp_stage_listar", "registro", lote["id"], "rechazado")
    ]
    return (
        jsonify(
            lote=lote["id"],
            recibidos=lote["total"],
            insertados=lote["insertados"],
            actualizados=lote["actualizados"],
            sin_cambios=lote["sin_cambios"],
            duplicados=lote["duplicados"],
            rechazados=lote["rechazados"],
            detalle_rechazados=rechazados,
        ),
        201,
    )


# ------------------------------------------------------------------ panel (vuelta)


@bp.get("/integracion/estado")
@jwt_required()
def estado():
    require_permiso(PERMISO)
    datos = integ.socio()
    url = datos["url"]
    disponible = sp.disponibles()
    return jsonify(
        disponible=disponible,
        sistema=current_app.config.get("INTEGRACION_SISTEMA"),
        socio={
            "nombre": datos["nombre"],
            "url": integ._sin_query(url),
            "configurado": bool(url),
            "tiene_clave": datos["tiene_clave"],
        },
        nuestra_api={
            "url": request.host_url.rstrip("/") + "/api/integracion/clientes",
            "sistemas_con_clave": sorted(integ.claves_entrantes()),
        },
        reintentos=current_app.config.get("INTEGRACION_REINTENTOS"),
        resumen=sp.filas("sp_clientes_resumen") if disponible else [],
        ultimo_lote=(sp.columna("sp_lotes_listar", "lote", 1) or [None])[0] if disponible else None,
    )


@bp.post("/integracion/importar")
@jwt_required()
def importar():
    require_permiso(PERMISO)
    lote = integ.importar_socio(_usuario_actual().id)
    return jsonify(lote=lote), (201 if lote["estado"] == "completado" else 502)


@bp.post("/integracion/importar-archivo")
@jwt_required()
def importar_archivo():
    require_permiso(PERMISO)
    data = request.get_json(silent=True) or {}
    origen = (data.get("origen") or integ.socio()["nombre"]).strip()[:80]
    registros = integ.extraer_registros(data.get("registros") if "registros" in data else data)
    lote = integ.recibir(origen, "archivo", registros, _usuario_actual().id)
    return jsonify(lote=lote), 201


@bp.get("/integracion/lotes")
@jwt_required()
def lotes():
    require_permiso(PERMISO)
    sp.exigir()
    return jsonify(lotes=sp.columna("sp_lotes_listar", "lote", request.args.get("limite", 30, type=int)))


@bp.get("/integracion/lotes/<int:lote_id>")
@jwt_required()
def lote(lote_id):
    require_permiso(PERMISO)
    sp.exigir()
    encontrado = sp.columna("sp_lote_obtener", "lote", lote_id)
    if not encontrado:
        raise ApiError("lote_no_encontrado", "El lote no existe.", 404)
    estado_fila = request.args.get("estado") or None
    return jsonify(lote=encontrado[0], registros=sp.columna("sp_stage_listar", "registro", lote_id, estado_fila))


@bp.get("/integracion/traza")
@jwt_required()
def traza():
    require_permiso(PERMISO)
    sp.exigir()
    direccion = request.args.get("direccion") if request.args.get("direccion") in ("entrada", "salida") else None
    return jsonify(traza=sp.columna("sp_traza_listar", "traza", direccion, request.args.get("limite", 100, type=int)))
