"""Autorizaciones con PIN de supervisor en la caja del cajero.

GET  /api/autorizaciones/catalogo     -> acciones y motivos
GET  /api/autorizaciones/supervisores -> supervisores activos que pueden autorizar
POST /api/autorizaciones              -> valida el PIN y devuelve un token de un solo uso
GET  /api/autorizaciones              -> bitácora (caja.supervisar). Filtros: desde, hasta, caja_id, resultado
"""

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import limiter
from ..models import Autorizacion, Usuario
from ..services import autorizacion as svc
from ..services.caja import caja_abierta_de
from .auth import _usuario_actual, _usuario_actual_o_404, require_permiso

bp = Blueprint("autorizaciones", __name__)


@bp.get("/autorizaciones/catalogo")
@jwt_required()
def catalogo():
    _usuario_actual_o_404()  # solo personal de la tienda (no sesiones de clientes)
    return jsonify(
        acciones=[{"codigo": k, "nombre": v} for k, v in svc.ACCIONES.items()],
        motivos=[{"codigo": k, "nombre": v} for k, v in svc.MOTIVOS.items()],
        token_segundos=svc.token_segundos(),
    )


@bp.get("/autorizaciones/supervisores")
@jwt_required()
def supervisores():
    try:
        require_permiso("caja.operar")
    except ApiError:
        require_permiso("pedidos.gestionar")  # devoluciones de pedidos online
    yo = _usuario_actual()
    candidatos = Usuario.query.filter_by(estado="activo").order_by(Usuario.nombres).all()
    lista = [
        {
            "id": u.id,
            "nombre": u.nombre_completo,
            "tiene_pin": bool(u.pin_hash),
            "rol": next(
                (r.nombre for r in u.roles if not r.es_admin and any(p.codigo == "caja.autorizar" for p in r.permisos)),
                "Administrador",
            ),
        }
        for u in candidatos
        if u.id != yo.id and u.puede_autorizar
    ]
    # Primero los Supervisores de Caja, luego los administradores
    lista.sort(key=lambda s: (s["rol"] == "Administrador", not s["tiene_pin"], s["nombre"]))
    return jsonify(supervisores=lista)


@bp.post("/autorizaciones")
@jwt_required()
@limiter.limit(lambda: current_app.config["LIMITE_LOGIN"])
def solicitar():
    data = request.get_json(silent=True) or {}
    accion = str(data.get("accion") or "").upper()
    try:
        require_permiso("caja.operar")
    except ApiError:
        # Devolución de un pedido online: la atiende el personal de pedidos, sin caja.
        if accion != "DEVOLUCION":
            raise
        require_permiso("pedidos.gestionar")
    usuario = _usuario_actual()
    caja = caja_abierta_de(usuario)
    # Reimpresión y devolución online no mueven efectivo de la gaveta: no exigen caja abierta
    # (la devolución en caja sí la exige en su propio endpoint).
    if not caja and accion not in ("REIMPRESION", "DEVOLUCION"):
        raise ApiError("caja_cerrada", "Debe tener su caja abierta para solicitar autorizaciones.", 409)
    registro, token = svc.solicitar(usuario, caja, data)
    return (
        jsonify(
            autorizacion=registro.to_dict(),
            token=token,
            expira_en=registro.expira_en.isoformat() if registro.expira_en else None,
            supervisor_nombre=registro.supervisor.nombre_completo if registro.supervisor else None,
        ),
        201,
    )


@bp.get("/autorizaciones")
@jwt_required()
def bitacora():
    require_permiso("caja.supervisar")
    from .ventas import rango_fechas

    inicio, fin = rango_fechas(request.args.get("desde"), request.args.get("hasta"))
    query = Autorizacion.query
    if inicio:
        query = query.filter(Autorizacion.fecha >= inicio)
    if fin:
        query = query.filter(Autorizacion.fecha < fin)
    caja_id = request.args.get("caja_id", type=int)
    if caja_id:
        query = query.filter(Autorizacion.caja_id == caja_id)
    resultado = request.args.get("resultado")
    if resultado:
        query = query.filter(Autorizacion.resultado == resultado)
    registros = query.order_by(Autorizacion.fecha.desc()).limit(300).all()
    return jsonify(autorizaciones=[a.to_dict() for a in registros], acciones=svc.ACCIONES, motivos=svc.MOTIVOS)
