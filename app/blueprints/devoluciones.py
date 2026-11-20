"""Devoluciones de productos después de la compra, con nota de crédito electrónica.

Escenario real: el cliente vuelve con su boleta y devuelve uno o varios productos.
  - Se atiende en caja (el dinero sale de la caja abierta del cajero) y siempre con
    el PIN de un supervisor.
  - Solo ventas con boleta o factura aceptada por la SUNAT: la nota de crédito la modifica.
  - Plazo: DEVOLUCION_DIAS_MAX días desde la compra (política de la tienda, configurable).
  - Se puede devolver todo o parte, en una o varias devoluciones, sin superar lo vendido.
  - El importe es proporcional a lo cobrado: incluye promociones y el descuento global.
  - Reembolso por un medio que se usó en la venta (efectivo de la gaveta o extorno en el
    POS / app con su N° de operación), sin superar lo pagado por ese medio.
  - Cada producto vuelve al stock, o no (si llega con falla va a servicio técnico).
  - Nota de crédito: motivo 06 (devolución total) si devuelve toda la venta de una vez;
    07 (devolución por ítem) en los demás casos.

La anulación (POST /ventas/<id>/anular) sigue siendo para el mismo turno, con la caja abierta.
"""

import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Devolucion, DevolucionDetalle, Venta, ahora
from ..services import sunat as sunat_svc
from ..services.autorizacion import consumir_token
from ..services.caja import exigir_caja_abierta, resumen_caja
from ..services.tiempo import fecha_local
from ..services.inventario import mover_stock
from .auth import _usuario_actual, require_permiso

bp = Blueprint("devoluciones", __name__)

CENTIMO = Decimal("0.01")
MOTIVO_TOTAL, MOTIVO_ITEM = "06", "07"


def _cent(valor):
    return Decimal(valor).quantize(CENTIMO, rounding=ROUND_HALF_UP)


def _validar_venta(venta):
    if not venta:
        raise ApiError("venta_no_encontrada", "La venta no existe.", 404)
    if venta.estado != "completada":
        raise ApiError("venta_anulada", "La venta está anulada; no hay nada que devolver.", 409)
    if venta.estado_sunat != "aceptado" or not venta.comprobante_serie:
        raise ApiError(
            "comprobante_requerido",
            "La venta no tiene boleta o factura aceptada por SUNAT. Emita el comprobante primero; "
            "si es del turno actual, puede anularla.",
            409,
        )
    dias = current_app.config.get("DEVOLUCION_DIAS_MAX", 7)
    if fecha_local(ahora()) - fecha_local(venta.fecha) > timedelta(days=dias):
        raise ApiError("plazo_vencido", f"El plazo de devolución es de {dias} días desde la compra.", 409)


def _leer_lineas(venta, items):
    """[(venta_detalle, cantidad, reingresa_stock)] validadas contra lo vendido y lo ya devuelto."""
    if not isinstance(items, list) or not items:
        raise ApiError("datos_invalidos", "Seleccione al menos un producto a devolver.", 400)
    por_id = {d.id: d for d in venta.detalles}
    lineas, vistos = [], set()
    for it in items:
        try:
            det_id, cantidad = int(it.get("venta_detalle_id")), int(it.get("cantidad") or 0)
        except (TypeError, ValueError, AttributeError):
            raise ApiError("datos_invalidos", "Cada producto debe indicar venta_detalle_id y cantidad.", 400)
        if cantidad <= 0:
            continue
        det = por_id.get(det_id)
        if not det or det_id in vistos:
            raise ApiError("datos_invalidos", "Un producto no pertenece a la venta o está repetido.", 400)
        disponible = det.cantidad - det.cantidad_devuelta
        if cantidad > disponible:
            nombre = det.producto.nombre if det.producto else "el producto"
            raise ApiError("cantidad_invalida", f"De {nombre} solo quedan {disponible} unidad(es) por devolver.", 409)
        vistos.add(det_id)
        lineas.append((det, cantidad, bool(it.get("reingresa_stock", True))))
    if not lineas:
        raise ApiError("datos_invalidos", "Indique la cantidad a devolver.", 400)
    return lineas


def _montos(venta, lineas):
    """Importe de cada línea, proporcional a lo cobrado. Si con esta devolución se completa
    toda la venta, la última línea absorbe los céntimos para que el total sea exacto."""
    factor = sunat_svc.factor_descuento(venta)
    montos = [_cent(Decimal(str(det.subtotal)) / det.cantidad * cantidad * factor) for det, cantidad, _ in lineas]
    devueltas = {det.id: cantidad for det, cantidad, _ in lineas}
    completa = all(d.cantidad_devuelta + devueltas.get(d.id, 0) >= d.cantidad for d in venta.detalles)
    if completa:
        restante = Decimal(str(venta.total)) - venta.monto_devuelto
        montos[-1] += restante - sum(montos)
    return montos, completa


def _validar_reembolso(venta, metodo, nro_operacion, total, caja):
    pagado = {}
    for m, monto in venta.pagos_efectivos():
        pagado[m] = pagado.get(m, Decimal("0")) + monto
    if metodo not in pagado:
        usados = ", ".join(sorted(pagado))
        raise ApiError("metodo_invalido", f"El reembolso debe hacerse por un medio usado en la compra ({usados}).", 400)
    ya = sum((Decimal(str(d.total)) for d in venta.devoluciones if d.metodo_reembolso == metodo), Decimal("0"))
    if total > pagado[metodo] - ya:
        raise ApiError(
            "monto_excede_medio",
            f"Por {metodo} solo quedan S/ {pagado[metodo] - ya} por devolver. Elija otro medio.",
            409,
        )
    if metodo == "efectivo":
        disponible = Decimal(str(resumen_caja(caja)["efectivo_esperado"]))
        if total > disponible:
            raise ApiError(
                "efectivo_insuficiente",
                f"La gaveta no tiene efectivo suficiente (S/ {disponible}). Use otro medio.",
                409,
            )
        return None
    nro = re.sub(r"\s+", "", str(nro_operacion or "")).upper()
    if not re.fullmatch(r"[A-Z0-9-]{4,30}", nro):
        raise ApiError("operacion_requerida", "Ingrese el N° de operación del extorno (4 a 30 caracteres).", 400)
    return nro


@bp.post("/ventas/<int:venta_id>/devoluciones")
@jwt_required()
def crear_devolucion(venta_id):
    require_permiso("caja.operar")
    usuario = _usuario_actual()
    caja = exigir_caja_abierta(usuario)
    data = request.get_json(silent=True) or {}

    # Se bloquea la venta: dos devoluciones simultáneas no pueden superar lo vendido.
    venta = Venta.query.filter_by(id=venta_id).with_for_update().first()
    _validar_venta(venta)
    motivo = (data.get("motivo") or "").strip()
    if len(motivo) < 10:
        raise ApiError("motivo_requerido", "Describa el motivo de la devolución (mínimo 10 caracteres).", 400)

    lineas = _leer_lineas(venta, data.get("items"))
    montos, completa = _montos(venta, lineas)
    total = sum(montos)
    if total <= 0:
        raise ApiError("monto_invalido", "El importe a devolver debe ser mayor a cero.", 400)
    metodo = (data.get("metodo_reembolso") or "").strip().lower()
    nro = _validar_reembolso(venta, metodo, data.get("nro_operacion"), total, caja)
    autorizacion = consumir_token(data.get("autorizacion_token"), "DEVOLUCION", usuario, venta_id=venta.id, monto=total)

    devolucion = Devolucion(
        codigo=f"TMP-{uuid4().hex[:16]}",
        venta_id=venta.id,
        caja_id=caja.id,
        usuario_id=usuario.id,
        autorizada_por_id=autorizacion.supervisor_id,
        motivo=motivo[:240],
        cod_motivo=MOTIVO_TOTAL if completa and not venta.devoluciones else MOTIVO_ITEM,
        total=total,
        metodo_reembolso=metodo,
        nro_operacion=nro,
    )
    db.session.add(devolucion)
    db.session.flush()
    devolucion.codigo = f"D-{devolucion.id:06d}"
    for (det, cantidad, reingresa), monto in zip(lineas, montos):
        db.session.add(
            DevolucionDetalle(
                devolucion_id=devolucion.id,
                venta_detalle_id=det.id,
                cantidad=cantidad,
                monto=monto,
                reingresa_stock=reingresa,
            )
        )
        if reingresa and det.producto:
            mover_stock(
                det.producto,
                cantidad,
                "devolucion",
                usuario,
                f"Devolución {devolucion.codigo} de {venta.codigo}",
                venta,
            )
    db.session.commit()

    # Nota de crédito: si la API falla, la devolución ya está registrada y queda pendiente de reintento.
    try:
        sunat_svc.emitir_nota_credito(devolucion)
        db.session.commit()
    except Exception as exc:  # no revertir la devolución por un fallo de facturación
        db.session.rollback()
        current_app.logger.error("Nota de crédito de %s no emitida: %s", devolucion.codigo, exc)
    db.session.refresh(venta)
    return jsonify(devolucion=devolucion.to_dict(), venta=venta.to_dict()), 201


@bp.post("/devoluciones/<int:devolucion_id>/comprobante")
@jwt_required()
def reintentar_nota(devolucion_id):
    """Reintenta la emisión de la nota de crédito pendiente o con error."""
    require_permiso("caja.operar")
    devolucion = db.get_or_404(Devolucion, devolucion_id)
    if devolucion.estado_sunat != "aceptado":
        sunat_svc.emitir_nota_credito(devolucion)
        db.session.commit()
    return jsonify(devolucion=devolucion.to_dict())


@bp.get("/devoluciones/<int:devolucion_id>/comprobante/<formato>")
@jwt_required()
def descargar_nota(devolucion_id, formato):
    """PDF o XML de la nota de crédito guardado localmente (no consulta la API)."""
    require_permiso("ventas.ver")
    devolucion = db.get_or_404(Devolucion, devolucion_id)
    ruta = devolucion.comprobante_pdf if formato == "pdf" else devolucion.comprobante_xml if formato == "xml" else None
    archivo = sunat_svc.archivo_local(ruta)
    if not archivo:
        raise ApiError("comprobante_no_disponible", "La nota de crédito aún no está disponible.", 404)
    return send_file(archivo, as_attachment=False)
