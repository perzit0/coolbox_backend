"""Registro de ventas en tienda.

El código de venta se genera de forma correlativa (V-000001) al confirmarla.
Al registrar la venta se descuenta stock; al anularla se devuelve stock.
"""
from decimal import Decimal
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy import func

from ..errors import ApiError
from ..extensions import db
from ..models import Producto, Venta, VentaDetalle
from .auth import require_permiso, _usuario_actual

bp = Blueprint("ventas", __name__)


def _siguiente_codigo():
    ultimo = db.session.query(func.max(Venta.id)).scalar() or 0
    return f"V-{ultimo + 1:06d}"


@bp.get("/ventas")
@jwt_required()
def listar_ventas():
    require_permiso("ventas.ver")
    ventas = Venta.query.order_by(Venta.fecha.desc()).limit(200).all()
    return jsonify(ventas=[v.to_dict(include_detalles=False) for v in ventas])


@bp.get("/ventas/<int:venta_id>")
@jwt_required()
def obtener_venta(venta_id):
    require_permiso("ventas.ver")
    venta = Venta.query.get_or_404(venta_id)
    return jsonify(venta=venta.to_dict())


@bp.post("/ventas")
@jwt_required()
def crear_venta():
    rol = require_permiso("ventas.crear")
    usuario = _usuario_actual()
    data = request.get_json(silent=True) or {}

    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise ApiError("datos_invalidos", "Debe agregar al menos un producto a la venta.", 400)

    metodo_pago = (data.get("metodo_pago") or "efectivo").strip().lower()
    if metodo_pago not in ("efectivo", "tarjeta", "yape", "plin", "transferencia"):
        raise ApiError("metodo_invalido", "Método de pago no soportado.", 400)

    cliente_nombre = (data.get("cliente_nombre") or "").strip() or None
    cliente_documento = (data.get("cliente_documento") or "").strip() or None

    igv_rate = Decimal(str(current_app.config.get("IGV_RATE", 0.18)))

    subtotal = Decimal("0")
    detalles_por_crear = []

    # Bloqueamos los productos afectados para evitar sobreventa concurrente
    ids = [int(it.get("producto_id")) for it in items if it.get("producto_id")]
    if not ids:
        raise ApiError("datos_invalidos", "Los ítems deben incluir producto_id.", 400)

    productos = Producto.query.filter(Producto.id.in_(ids)).with_for_update().all()
    por_id = {p.id: p for p in productos}

    for it in items:
        pid = int(it.get("producto_id"))
        cantidad = int(it.get("cantidad") or 0)
        if cantidad <= 0:
            raise ApiError("cantidad_invalida", "La cantidad de cada ítem debe ser mayor a cero.", 400)
        producto = por_id.get(pid)
        if not producto:
            raise ApiError("producto_no_encontrado", f"Producto {pid} no existe.", 404)
        if not producto.activo:
            raise ApiError("producto_inactivo", f"El producto {producto.codigo} no está disponible.", 400)
        if producto.stock < cantidad:
            raise ApiError(
                "stock_insuficiente",
                f"Stock insuficiente para {producto.nombre}. Disponible: {producto.stock}.",
                409,
            )
        precio_unit = Decimal(str(producto.precio))
        sub = (precio_unit * cantidad).quantize(Decimal("0.01"))
        subtotal += sub
        detalles_por_crear.append((producto, cantidad, precio_unit, sub))

    # IGV incluido en Perú: subtotal ya viene con IGV, así que lo separamos.
    # base = total / (1 + igv)
    total = subtotal.quantize(Decimal("0.01"))
    base = (total / (Decimal("1") + igv_rate)).quantize(Decimal("0.01"))
    igv = (total - base).quantize(Decimal("0.01"))

    venta = Venta(
        codigo="temporal",
        usuario_id=usuario.id,
        rol_id=rol.id,
        cliente_nombre=cliente_nombre,
        cliente_documento=cliente_documento,
        metodo_pago=metodo_pago,
        subtotal=base,
        igv=igv,
        total=total,
        estado="completada",
    )
    db.session.add(venta)
    db.session.flush()
    venta.codigo = f"V-{venta.id:06d}"

    for producto, cantidad, precio_unit, sub in detalles_por_crear:
        db.session.add(VentaDetalle(
            venta_id=venta.id,
            producto_id=producto.id,
            cantidad=cantidad,
            precio_unitario=precio_unit,
            subtotal=sub,
        ))
        producto.stock = producto.stock - cantidad

    db.session.commit()
    return jsonify(venta=venta.to_dict()), 201


@bp.post("/ventas/<int:venta_id>/anular")
@jwt_required()
def anular_venta(venta_id):
    require_permiso("ventas.anular")
    venta = Venta.query.get_or_404(venta_id)
    if venta.estado == "anulada":
        raise ApiError("venta_anulada", "La venta ya se encuentra anulada.", 409)

    for detalle in venta.detalles:
        if detalle.producto:
            detalle.producto.stock = detalle.producto.stock + detalle.cantidad
    venta.estado = "anulada"
    db.session.commit()
    return jsonify(venta=venta.to_dict())


@bp.get("/ventas/reporte/resumen")
@jwt_required()
def reporte_resumen():
    require_permiso("reportes.ver")
    total_completadas = db.session.query(func.coalesce(func.sum(Venta.total), 0)).filter(Venta.estado == "completada").scalar()
    cantidad = db.session.query(func.count(Venta.id)).filter(Venta.estado == "completada").scalar()
    anuladas = db.session.query(func.count(Venta.id)).filter(Venta.estado == "anulada").scalar()
    return jsonify(
        total_facturado=float(total_completadas or 0),
        ventas_completadas=int(cantidad or 0),
        ventas_anuladas=int(anuladas or 0),
    )
