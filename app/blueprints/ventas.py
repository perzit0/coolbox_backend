"""Registro de ventas en tienda.

El código de venta se genera de forma correlativa (V-000001) al confirmarla.
Al registrar la venta se descuenta stock; al anularla se devuelve stock.
Ambos movimientos quedan en el kardex.
"""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Producto, Usuario, Venta, VentaDetalle, ahora
from ..services.inventario import mover_stock
from .auth import _usuario_actual, require_permiso

bp = Blueprint("ventas", __name__)

# Perú no tiene horario de verano: UTC-5 todo el año.
ZONA_LIMA = timezone(timedelta(hours=-5))
CENTIMO = Decimal("0.01")


def rango_fechas(desde_txt, hasta_txt):
    """Convierte fechas YYYY-MM-DD (hora de Lima) a un rango UTC [inicio, fin)."""
    def parse(txt, etiqueta):
        try:
            return datetime.strptime(txt, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            raise ApiError("fecha_invalida", f"La fecha '{etiqueta}' debe tener formato AAAA-MM-DD.", 400)

    inicio = fin = None
    if desde_txt:
        inicio = datetime.combine(parse(desde_txt, "desde"), time.min, ZONA_LIMA).astimezone(timezone.utc)
    if hasta_txt:
        fin = (datetime.combine(parse(hasta_txt, "hasta"), time.min, ZONA_LIMA) + timedelta(days=1)).astimezone(timezone.utc)
    if inicio and fin and inicio >= fin:
        raise ApiError("rango_invalido", "La fecha 'desde' debe ser anterior o igual a 'hasta'.", 400)
    return inicio, fin


@bp.get("/ventas")
@jwt_required()
def listar_ventas():
    """Historial con filtros y paginación.

    Parámetros: desde, hasta (AAAA-MM-DD, hora de Lima), estado, metodo_pago,
    usuario_id, q (código de venta o cliente), page, per_page.
    """
    require_permiso("ventas.ver")
    inicio, fin = rango_fechas(request.args.get("desde"), request.args.get("hasta"))
    query = Venta.query
    if inicio:
        query = query.filter(Venta.fecha >= inicio)
    if fin:
        query = query.filter(Venta.fecha < fin)
    estado = request.args.get("estado")
    if estado in ("completada", "anulada"):
        query = query.filter(Venta.estado == estado)
    metodo = request.args.get("metodo_pago")
    if metodo in Venta.METODOS_PAGO:
        query = query.filter(Venta.metodo_pago == metodo)
    usuario_id = request.args.get("usuario_id", type=int)
    if usuario_id:
        query = query.filter(Venta.usuario_id == usuario_id)
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Venta.codigo.ilike(like), Venta.cliente_nombre.ilike(like)))

    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 20, type=int), 5), 100)
    total = query.count()
    ventas = (query.order_by(Venta.fecha.desc(), Venta.id.desc())
              .offset((page - 1) * per_page).limit(per_page).all())
    return jsonify(
        ventas=[v.to_dict(include_detalles=False) for v in ventas],
        page=page, per_page=per_page, total=total,
        pages=max(1, -(-total // per_page)),
    )


@bp.get("/ventas/vendedores")
@jwt_required()
def vendedores():
    """Usuarios que registraron ventas (para el filtro del historial)."""
    require_permiso("ventas.ver")
    ids = [i for (i,) in db.session.query(Venta.usuario_id).distinct().all()]
    usuarios = Usuario.query.filter(Usuario.id.in_(ids)).order_by(Usuario.nombres).all() if ids else []
    return jsonify(vendedores=[{"id": u.id, "nombre": u.nombre_completo} for u in usuarios])


@bp.get("/ventas/<int:venta_id>")
@jwt_required()
def obtener_venta(venta_id):
    require_permiso("ventas.ver")
    venta = db.get_or_404(Venta, venta_id)
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
    if metodo_pago not in Venta.METODOS_PAGO:
        raise ApiError("metodo_invalido", "Método de pago no soportado.", 400)

    # El prototipo no solicita DNI/RUC (no hay validación contra RENIEC/SUNAT).
    cliente_nombre = (data.get("cliente_nombre") or "").strip()[:180] or None
    igv_rate = Decimal(str(current_app.config.get("IGV_RATE", 0.18)))

    # Agrupar ítems repetidos del mismo producto
    cantidades = {}
    for it in items:
        try:
            pid = int(it.get("producto_id"))
            cantidad = int(it.get("cantidad") or 0)
        except (TypeError, ValueError):
            raise ApiError("datos_invalidos", "Cada ítem debe incluir producto_id y cantidad numéricos.", 400)
        if cantidad <= 0:
            raise ApiError("cantidad_invalida", "La cantidad de cada ítem debe ser mayor a cero.", 400)
        cantidades[pid] = cantidades.get(pid, 0) + cantidad

    # Bloqueamos los productos afectados para evitar sobreventa concurrente
    productos = Producto.query.filter(Producto.id.in_(cantidades.keys())).with_for_update().all()
    por_id = {p.id: p for p in productos}

    total = Decimal("0")
    lineas = []
    for pid, cantidad in cantidades.items():
        producto = por_id.get(pid)
        if not producto:
            raise ApiError("producto_no_encontrado", f"El producto {pid} no existe.", 404)
        if not producto.activo:
            raise ApiError("producto_inactivo", f"El producto {producto.sku or producto.codigo} no está disponible.", 400)
        if producto.stock < cantidad:
            raise ApiError("stock_insuficiente",
                           f"Stock insuficiente para {producto.nombre}. Disponible: {producto.stock}.", 409)
        precio_unit = Decimal(str(producto.precio))
        sub = (precio_unit * cantidad).quantize(CENTIMO)
        total += sub
        lineas.append((producto, cantidad, precio_unit, sub))

    # Precios con IGV incluido (Perú): se separa la base imponible.
    total = total.quantize(CENTIMO)
    base = (total / (Decimal("1") + igv_rate)).quantize(CENTIMO)
    igv = (total - base).quantize(CENTIMO)

    monto_recibido = vuelto = None
    if metodo_pago == "efectivo" and data.get("monto_recibido") not in (None, ""):
        try:
            monto_recibido = Decimal(str(data["monto_recibido"])).quantize(CENTIMO)
        except (InvalidOperation, ValueError):
            raise ApiError("monto_invalido", "El monto recibido debe ser numérico.", 400)
        if monto_recibido < total:
            raise ApiError("monto_insuficiente", f"El monto recibido no cubre el total (S/ {total}).", 400)
        vuelto = (monto_recibido - total).quantize(CENTIMO)

    venta = Venta(
        codigo="temporal", usuario_id=usuario.id, rol_id=rol.id, cliente_nombre=cliente_nombre,
        metodo_pago=metodo_pago, subtotal=base, igv=igv, total=total,
        monto_recibido=monto_recibido, vuelto=vuelto, estado="completada",
    )
    db.session.add(venta)
    db.session.flush()
    venta.codigo = f"V-{venta.id:06d}"

    for producto, cantidad, precio_unit, sub in lineas:
        db.session.add(VentaDetalle(venta_id=venta.id, producto_id=producto.id, cantidad=cantidad,
                                    precio_unitario=precio_unit, subtotal=sub))
        mover_stock(producto, -cantidad, "venta", usuario, f"Venta {venta.codigo}", venta)

    db.session.commit()
    return jsonify(venta=venta.to_dict()), 201


@bp.post("/ventas/<int:venta_id>/anular")
@jwt_required()
def anular_venta(venta_id):
    require_permiso("ventas.anular")
    venta = db.get_or_404(Venta, venta_id)
    if venta.estado == "anulada":
        raise ApiError("venta_anulada", "La venta ya se encuentra anulada.", 409)
    motivo = ((request.get_json(silent=True) or {}).get("motivo") or "").strip()
    if len(motivo) < 10:
        raise ApiError("motivo_requerido", "Indique el motivo de la anulación (mínimo 10 caracteres).", 400)

    usuario = _usuario_actual()
    for detalle in venta.detalles:
        if detalle.producto:
            mover_stock(detalle.producto, detalle.cantidad, "anulacion", usuario,
                        f"Anulación de {venta.codigo}", venta)
    venta.estado = "anulada"
    venta.anulada_por_id = usuario.id
    venta.fecha_anulacion = ahora()
    venta.motivo_anulacion = motivo[:240]
    db.session.commit()
    return jsonify(venta=venta.to_dict())


@bp.get("/ventas/reporte/resumen")
@jwt_required()
def reporte_resumen_compat():
    """Compatibilidad con versiones anteriores: delega en /reportes/resumen."""
    from .reportes import resumen
    return resumen()
