"""Administración de promociones (solo Administrador).

GET  /api/promociones/tipos          -> tipos, botones de atajo (2x1, 3x1...), marcas y familias
GET  /api/promociones?estado=        -> lista (vigente | programada | vencida | desactivada)
GET  /api/promociones/<id>
POST /api/promociones                -> crear
PUT  /api/promociones/<id>           -> editar
POST /api/promociones/<id>/activar | /desactivar
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Familia, Marca, Producto, Promocion, PromocionProducto
from ..services import promociones as svc
from .auth import _usuario_actual, require_permiso

bp = Blueprint("promociones", __name__)
PERMISO = "promociones.gestionar"


def _get(pid):
    p = db.session.get(Promocion, pid)
    if not p:
        raise ApiError("promocion_no_encontrada", "La promoción no existe.", 404)
    return p


@bp.get("/promociones/tipos")
@jwt_required()
def tipos():
    require_permiso(PERMISO)
    return jsonify(
        tipos=[{"codigo": k, **v} for k, v in svc.TIPOS.items()],
        presets=svc.PRESETS,
        canales=[
            {"codigo": "todos", "nombre": "Tienda online y cajas"},
            {"codigo": "online", "nombre": "Solo tienda online"},
            {"codigo": "tienda", "nombre": "Solo cajas de tienda"},
        ],
        marcas=[{"id": m.id, "nombre": m.nombre} for m in Marca.query.order_by(Marca.nombre).all()],
        familias=[
            {"id": f.id, "codigo": f.codigo, "nombre": f.nombre} for f in Familia.query.order_by(Familia.codigo).all()
        ],
    )


@bp.get("/promociones")
@jwt_required()
def listar():
    require_permiso(PERMISO)
    estado = request.args.get("estado")
    promos = Promocion.query.order_by(Promocion.fecha_fin.desc()).all()
    data = [p.to_dict() for p in promos]
    if estado:
        data = [d for d in data if d["estado"] == estado]
    orden = {"vigente": 0, "programada": 1, "desactivada": 2, "vencida": 3}
    data.sort(key=lambda d: (orden.get(d["estado"], 9), d["fecha_fin"]))
    resumen = {k: sum(1 for d in (p.to_dict() for p in promos) if d["estado"] == k) for k in orden}
    return jsonify(promociones=data, resumen=resumen)


@bp.get("/promociones/<int:pid>")
@jwt_required()
def obtener(pid):
    require_permiso(PERMISO)
    return jsonify(promocion=_get(pid).to_dict(con_productos=True))


def _guardar(promo, nueva):
    data = request.get_json(silent=True) or {}
    productos = svc.aplicar_datos(promo, data, nueva)
    if nueva:
        promo.creado_por_id = _usuario_actual().id
        db.session.add(promo)
    db.session.flush()
    PromocionProducto.query.filter_by(promocion_id=promo.id).delete()
    for pid, esp in productos:
        db.session.add(PromocionProducto(promocion_id=promo.id, producto_id=pid, precio_especial=esp))
    db.session.commit()
    db.session.refresh(promo)
    return promo


@bp.post("/promociones")
@jwt_required()
def crear():
    require_permiso(PERMISO)
    promo = _guardar(Promocion(), True)
    return jsonify(promocion=promo.to_dict(con_productos=True), mensaje="Promoción creada."), 201


@bp.put("/promociones/<int:pid>")
@jwt_required()
def editar(pid):
    require_permiso(PERMISO)
    promo = _guardar(_get(pid), False)
    return jsonify(promocion=promo.to_dict(con_productos=True), mensaje="Promoción actualizada.")


@bp.post("/promociones/<int:pid>/<accion>")
@jwt_required()
def cambiar_estado(pid, accion):
    require_permiso(PERMISO)
    if accion not in ("activar", "desactivar"):
        raise ApiError("accion_invalida", "Acción no válida.", 400)
    promo = _get(pid)
    promo.activa = accion == "activar"
    db.session.commit()
    return jsonify(
        promocion=promo.to_dict(), mensaje="Promoción activada." if promo.activa else "Promoción desactivada."
    )


@bp.get("/promociones/productos")
@jwt_required()
def buscar_productos():
    """Buscador de productos para armar la promoción."""
    require_permiso(PERMISO)
    q = (request.args.get("q") or "").strip()
    consulta = Producto.query.filter(Producto.activo.is_(True))
    if q:
        like = f"%{q}%"
        consulta = consulta.filter(
            db.or_(Producto.nombre.ilike(like), Producto.sku.ilike(like), Producto.codigo.ilike(like))
        )
    if request.args.get("marca", type=int):
        consulta = consulta.filter(Producto.marca_id == request.args.get("marca", type=int))
    if request.args.get("familia", type=int):
        consulta = consulta.filter(Producto.categoria_id == request.args.get("familia", type=int))
    return jsonify(
        productos=[
            {
                "id": p.id,
                "sku": p.sku,
                "nombre": p.nombre,
                "precio": float(p.precio),
                "marca": p.marca.nombre if p.marca else None,
                "stock": p.stock,
                "imagen_url": p.imagen_url,
            }
            for p in consulta.order_by(Producto.nombre).limit(40).all()
        ]
    )
