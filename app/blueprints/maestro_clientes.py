"""Maestro de clientes: mantenimiento para el personal.

GET  /api/maestro-clientes?q=&origen=&activo=&pagina=&por_pagina=
GET  /api/maestro-clientes/resumen
GET  /api/maestro-clientes/<id>
POST /api/maestro-clientes                {tipo_documento, nro_documento, nombres, apellidos, razon_social, ...}
PUT  /api/maestro-clientes/<id>
POST /api/maestro-clientes/<id>/estado    {activo}

Todas las lecturas y escrituras pasan por procedimientos almacenados.
"""

from math import ceil

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models_integracion import ORIGENES
from ..services import procedimientos as sp
from .auth import require_permiso

bp = Blueprint("maestro_clientes", __name__)
PERMISO = "clientes.gestionar"


def _datos(data):
    return (
        data.get("tipo_documento"),
        data.get("nro_documento"),
        data.get("nombres"),
        data.get("apellidos"),
        data.get("razon_social"),
        data.get("email"),
        data.get("telefono"),
        data.get("direccion"),
    )


def _obtener(cliente_id):
    encontrado = sp.columna("sp_cliente_obtener", "cliente", cliente_id)
    if not encontrado:
        raise ApiError("cliente_no_encontrado", "El cliente no existe.", 404)
    return encontrado[0]


@bp.get("/maestro-clientes")
@jwt_required()
def listar():
    require_permiso(PERMISO)
    sp.exigir()
    pagina = max(request.args.get("pagina", 1, type=int) or 1, 1)
    por_pagina = min(max(request.args.get("por_pagina", 25, type=int) or 25, 1), 200)
    origen = request.args.get("origen") if request.args.get("origen") in ORIGENES else None
    activo = {"1": True, "true": True, "0": False, "false": False}.get((request.args.get("activo") or "").lower())
    filas = sp.filas(
        "sp_clientes_listar", request.args.get("q") or None, origen, activo, por_pagina, (pagina - 1) * por_pagina
    )
    total = filas[0]["total"] if filas else 0
    return jsonify(
        clientes=[f["cliente"] for f in filas],
        total=total,
        pagina=pagina,
        total_paginas=ceil(total / por_pagina) if total else 0,
    )


@bp.get("/maestro-clientes/resumen")
@jwt_required()
def resumen():
    require_permiso(PERMISO)
    sp.exigir()
    return jsonify(resumen=sp.filas("sp_clientes_resumen"))


@bp.get("/maestro-clientes/<int:cliente_id>")
@jwt_required()
def obtener(cliente_id):
    require_permiso(PERMISO)
    sp.exigir()
    return jsonify(cliente=_obtener(cliente_id))


@bp.post("/maestro-clientes")
@jwt_required()
def crear():
    require_permiso(PERMISO)
    sp.exigir()
    cliente_id = sp.valor("sp_cliente_guardar", None, *_datos(request.get_json(silent=True) or {}))
    db.session.commit()
    return jsonify(cliente=_obtener(cliente_id), mensaje="Cliente registrado."), 201


@bp.put("/maestro-clientes/<int:cliente_id>")
@jwt_required()
def editar(cliente_id):
    require_permiso(PERMISO)
    sp.exigir()
    sp.valor("sp_cliente_guardar", cliente_id, *_datos(request.get_json(silent=True) or {}))
    db.session.commit()
    return jsonify(cliente=_obtener(cliente_id), mensaje="Cliente actualizado.")


@bp.post("/maestro-clientes/<int:cliente_id>/estado")
@jwt_required()
def cambiar_estado(cliente_id):
    require_permiso(PERMISO)
    sp.exigir()
    activo = bool((request.get_json(silent=True) or {}).get("activo"))
    sp.valor("sp_cliente_estado", cliente_id, activo)
    db.session.commit()
    return jsonify(cliente=_obtener(cliente_id), mensaje="Cliente activado." if activo else "Cliente desactivado.")
