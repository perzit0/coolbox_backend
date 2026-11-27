"""Pedidos de la tienda online para el personal de la sede (recojo en tienda).

GET  /api/pedidos-online?estado=&sede=&q=&desde=&hasta=
GET  /api/pedidos-online/<id>
POST /api/pedidos-online/<id>/confirmar-pago   (PagoEfectivo pagado)
POST /api/pedidos-online/<id>/listo            (preparado para recojo)
POST /api/pedidos-online/<id>/entregar         {nro_documento}  (se valida el documento del comprador)
POST /api/pedidos-online/<id>/cancelar         {motivo}         (el stock vuelve al inventario)
POST /api/pedidos-online/<id>/comprobante/<tipo>            (reintenta boleta o nota de crédito)
GET  /api/pedidos-online/<id>/comprobante/<tipo>/<formato>  (PDF o XML guardado localmente)
POST /api/pedidos-online/<id>/devoluciones                  (devolución después de la entrega)
POST /api/pedidos-online/devoluciones/<id>/comprobante      (reintenta su nota de crédito)
GET  /api/pedidos-online/devoluciones/<id>/comprobante/<formato>

Comprobantes: la boleta se emite cuando el pedido queda pagado. Si un pedido pagado se cancela
(no se entregó), se emite una nota de crédito por anulación de la operación.

Devoluciones: un pedido ya entregado se puede devolver (todo o parte) dentro de
DEVOLUCION_DIAS_MAX días desde la entrega, con el PIN de un supervisor. El registro y el
reingreso al stock los hace el procedimiento almacenado sp_pedido_devolver; el dinero se
extorna al mismo medio de pago y se emite la nota de crédito que modifica la boleta.
"""

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..services.seguridad import patron_like
from ..extensions import db
from ..models import Pedido, PedidoComprobante, PedidoDevolucion, Sede, ahora
from ..services import procedimientos as sp
from ..services import sunat as sunat_svc
from ..services.autorizacion import consumir_token
from ..services.inventario import mover_stock
from .auth import _usuario_actual, require_permiso
from .ventas import rango_fechas

bp = Blueprint("pedidos_online", __name__)
PERMISO = "pedidos.gestionar"


def _get(pid):
    p = db.session.get(Pedido, pid)
    if not p:
        raise ApiError("pedido_no_encontrado", "El pedido no existe.", 404)
    return p


def _vencer_cips():
    """Cancela los pedidos PagoEfectivo cuyo CIP venció sin pago y devuelve el stock."""
    vencidos = Pedido.query.filter(Pedido.estado == "pendiente_pago", Pedido.cip_expira < ahora()).all()
    for p in vencidos:
        _devolver_stock(p, None, "CIP de PagoEfectivo vencido sin pago")
    if vencidos:
        db.session.commit()


def _devolver_stock(pedido, usuario, motivo):
    for d in pedido.detalles:
        mover_stock(d.producto, d.cantidad, "cancel_web", usuario, f"Pedido {pedido.codigo} cancelado: {motivo}")
    pedido.estado = "cancelado"
    pedido.motivo_cancelacion = motivo[:240]


@bp.get("/pedidos-online")
@jwt_required()
def listar():
    require_permiso(PERMISO)
    _vencer_cips()
    q = Pedido.query
    if request.args.get("estado"):
        q = q.filter(Pedido.estado == request.args["estado"])
    if request.args.get("sede", type=int):
        q = q.filter(Pedido.sede_id == request.args.get("sede", type=int))
    texto = (request.args.get("q") or "").strip()
    if texto:
        like = patron_like(texto)
        q = q.filter(
            db.or_(
                Pedido.codigo.ilike(like),
                Pedido.nro_documento.ilike(like),
                Pedido.email.ilike(like),
                Pedido.nombres.ilike(like),
                Pedido.apellidos.ilike(like),
            )
        )
    inicio, fin = rango_fechas(request.args.get("desde"), request.args.get("hasta"))
    if inicio:
        q = q.filter(Pedido.fecha >= inicio)
    if fin:
        q = q.filter(Pedido.fecha < fin)
    pedidos = q.order_by(Pedido.id.desc()).limit(200).all()
    conteo = dict(db.session.query(Pedido.estado, db.func.count(Pedido.id)).group_by(Pedido.estado).all())
    return jsonify(
        pedidos=[p.to_dict() for p in pedidos],
        conteo=conteo,
        sedes=[{"id": s.id, "nombre": s.nombre} for s in Sede.query.order_by(Sede.nombre).all()],
    )


@bp.get("/pedidos-online/<int:pid>")
@jwt_required()
def obtener(pid):
    require_permiso(PERMISO)
    return jsonify(pedido=_get(pid).to_dict())


@bp.post("/pedidos-online/<int:pid>/confirmar-pago")
@jwt_required()
def confirmar_pago(pid):
    require_permiso(PERMISO)
    p = _get(pid)
    if p.estado != "pendiente_pago":
        raise ApiError("estado_invalido", "Solo se confirma el pago de pedidos pendientes de pago.", 409)
    p.estado, p.fecha_pago = "pagado", ahora()
    db.session.commit()
    from .tienda import emitir_boleta_pedido

    emitir_boleta_pedido(p)
    return jsonify(pedido=p.to_dict(), mensaje=f"Pago del pedido {p.codigo} confirmado.")


@bp.post("/pedidos-online/<int:pid>/listo")
@jwt_required()
def listo(pid):
    require_permiso(PERMISO)
    p = _get(pid)
    if p.estado != "pagado":
        raise ApiError("estado_invalido", "Solo se preparan pedidos pagados.", 409)
    p.estado, p.fecha_listo = "listo", ahora()
    db.session.commit()
    return jsonify(pedido=p.to_dict(), mensaje=f"Pedido {p.codigo} listo para recojo.")


@bp.post("/pedidos-online/<int:pid>/entregar")
@jwt_required()
def entregar(pid):
    require_permiso(PERMISO)
    p = _get(pid)
    if p.estado not in ("pagado", "listo"):
        raise ApiError("estado_invalido", "Solo se entregan pedidos pagados.", 409)
    doc = ((request.get_json(silent=True) or {}).get("nro_documento") or "").strip().upper()
    if doc != p.nro_documento:
        raise ApiError(
            "documento_no_coincide",
            f"El documento no coincide con el del comprador ({p.tipo_documento}). Solo el titular recoge el pedido.",
            400,
        )
    p.estado, p.fecha_entrega = "entregado", ahora()
    p.entregado_por_id = _usuario_actual().id
    db.session.commit()
    return jsonify(pedido=p.to_dict(), mensaje=f"Pedido {p.codigo} entregado a {p.nombres} {p.apellidos}.")


@bp.post("/pedidos-online/<int:pid>/cancelar")
@jwt_required()
def cancelar(pid):
    require_permiso(PERMISO)
    p = _get(pid)
    if p.estado in ("entregado", "cancelado"):
        raise ApiError("estado_invalido", "Este pedido ya no se puede cancelar.", 409)
    motivo = ((request.get_json(silent=True) or {}).get("motivo") or "").strip()
    if len(motivo) < 5:
        raise ApiError("motivo_requerido", "Indique el motivo de la cancelación.", 400)
    _devolver_stock(p, _usuario_actual(), motivo)
    db.session.commit()
    mensaje = f"Pedido {p.codigo} cancelado. El stock volvió al inventario."
    # Con boleta emitida, la anulación ante SUNAT se hace con nota de crédito.
    boleta = p.comprobante("boleta")
    if boleta and not boleta.comprobante_api_id:
        # La boleta nunca llegó a crearse en la API: ya no se emitirá.
        boleta.estado_sunat, boleta.sunat_mensaje = "anulado", "Pedido cancelado antes de emitir la boleta."
        db.session.commit()
    elif boleta:
        nota = PedidoComprobante(pedido_id=p.id, tipo="nota_credito", fecha=ahora(), motivo=motivo[:240])
        db.session.add(nota)
        db.session.commit()
        db.session.refresh(p)
        _emitir(nota)
        mensaje += (
            " Se emitió la nota de crédito."
            if nota.estado_sunat == "aceptado"
            else (" La nota de crédito quedó pendiente: reintente desde el pedido.")
        )
    return jsonify(pedido=p.to_dict(), mensaje=mensaje)


def _emitir(comprobante):
    try:
        sunat_svc.emitir_pedido(comprobante)
        db.session.commit()
    except Exception:  # el pedido ya cambió de estado; el comprobante queda para reintentar
        db.session.rollback()


@bp.post("/pedidos-online/<int:pid>/comprobante/<tipo>")
@jwt_required()
def reintentar_comprobante(pid, tipo):
    require_permiso(PERMISO)
    p = _get(pid)
    if tipo == "boleta" and p.estado in ("pagado", "listo", "entregado"):
        from .tienda import emitir_boleta_pedido

        emitir_boleta_pedido(p)
    else:
        comprobante = p.comprobante(tipo)
        if not comprobante:
            raise ApiError("comprobante_no_encontrado", "El pedido no tiene ese comprobante.", 404)
        if comprobante.estado_sunat != "aceptado":
            _emitir(comprobante)
    db.session.refresh(p)
    return jsonify(pedido=p.to_dict())


@bp.get("/pedidos-online/<int:pid>/comprobante/<tipo>/<formato>")
@jwt_required()
def descargar_comprobante(pid, tipo, formato):
    require_permiso(PERMISO)
    comprobante = _get(pid).comprobante(tipo)
    return sunat_svc.entregar(comprobante, comprobante.tipo if comprobante else None, formato)


# ------------------------------------------------------------------ devoluciones después de la entrega


def _emitir_nota_devolucion(devolucion):
    try:
        sunat_svc.emitir_nota_devolucion_pedido(devolucion)
        db.session.commit()
    except Exception as exc:  # la devolución ya está registrada; la nota queda para reintentar
        db.session.rollback()
        current_app.logger.error("Nota de crédito de %s no emitida: %s", devolucion.codigo, exc)


@bp.post("/pedidos-online/<int:pid>/devoluciones")
@jwt_required()
def devolver(pid):
    require_permiso(PERMISO)
    sp.exigir()
    usuario = _usuario_actual()
    pedido = _get(pid)
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    autorizacion = consumir_token(data.get("autorizacion_token"), "DEVOLUCION", usuario)
    devolucion_id = sp.valor(
        "sp_pedido_devolver",
        pedido.id,
        usuario.id,
        autorizacion.supervisor_id,
        data.get("motivo"),
        (data.get("metodo_reembolso") or "").strip().lower(),
        data.get("nro_operacion"),
        sp.json_param(items if isinstance(items, list) else []),
        current_app.config.get("DEVOLUCION_DIAS_MAX", 7),
        current_app.config.get("ZONA_HORARIA_HORAS", -5),
    )
    db.session.commit()  # devolución, stock y uso del PIN en la misma transacción
    devolucion = db.session.get(PedidoDevolucion, devolucion_id)
    _emitir_nota_devolucion(devolucion)
    db.session.refresh(pedido)
    return (
        jsonify(
            pedido=pedido.to_dict(),
            devolucion=devolucion.to_dict(),
            mensaje=(
                f"Devolución {devolucion.codigo} registrada por S/ {devolucion.total}. "
                + (
                    "Se emitió la nota de crédito."
                    if devolucion.estado_sunat == "aceptado"
                    else "La nota de crédito quedó pendiente: reintente desde el pedido."
                )
            ),
        ),
        201,
    )


@bp.post("/pedidos-online/devoluciones/<int:did>/comprobante")
@jwt_required()
def reintentar_nota_devolucion(did):
    require_permiso(PERMISO)
    devolucion = db.get_or_404(PedidoDevolucion, did)
    if devolucion.estado_sunat != "aceptado":
        _emitir_nota_devolucion(devolucion)
    return jsonify(pedido=devolucion.pedido.to_dict(), devolucion=devolucion.to_dict())


@bp.get("/pedidos-online/devoluciones/<int:did>/comprobante/<formato>")
@jwt_required()
def descargar_nota_devolucion(did, formato):
    require_permiso(PERMISO)
    devolucion = db.get_or_404(PedidoDevolucion, did)
    return sunat_svc.entregar(devolucion, "nota_credito", formato, "La nota de crédito aún no está disponible.")
