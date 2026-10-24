"""Turnos de caja del vendedor.

    GET  /api/cajas/mi-caja    -> caja abierta del usuario (o null) con su resumen
    POST /api/cajas/abrir      -> abre la caja con un fondo inicial en efectivo
    POST /api/cajas/cerrar     -> cierra la caja con el arqueo (conteo de efectivo)
    GET  /api/cajas/mis-cajas  -> últimos turnos del usuario
    GET  /api/cajas/<id>       -> detalle de un turno (reporte de cierre)
"""
import json
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Caja, Usuario, ahora
from ..services.caja import DENOMINACIONES, caja_abierta_de, caja_con_resumen, calcular_conteo, resumen_caja
from .auth import _usuario_actual, require_permiso

bp = Blueprint("cajas", __name__)

FONDO_MAXIMO = Decimal("5000.00")


def _monto(valor, campo):
    try:
        monto = Decimal(str(valor)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ApiError("monto_invalido", f"El {campo} debe ser un número.", 400)
    if monto < 0:
        raise ApiError("monto_invalido", f"El {campo} no puede ser negativo.", 400)
    return monto


@bp.get("/cajas/mi-caja")
@jwt_required()
def mi_caja():
    require_permiso("caja.operar")
    caja = caja_abierta_de(_usuario_actual())
    return jsonify(caja=caja_con_resumen(caja) if caja else None)


@bp.post("/cajas/abrir")
@jwt_required()
def abrir_caja():
    require_permiso("caja.operar")
    # Se bloquea la fila del usuario: un doble clic no abre dos cajas.
    usuario = Usuario.query.filter_by(id=_usuario_actual().id).with_for_update().first()
    if caja_abierta_de(usuario):
        raise ApiError("caja_ya_abierta", "Ya tiene una caja abierta. Ciérrela antes de abrir otra.", 409)

    data = request.get_json(silent=True) or {}
    if data.get("monto_apertura") in (None, ""):
        raise ApiError("monto_requerido", "Indique el fondo inicial de la caja.", 400)
    monto = _monto(data["monto_apertura"], "fondo inicial")
    if monto > FONDO_MAXIMO:
        raise ApiError("monto_invalido", f"El fondo inicial no puede superar S/ {FONDO_MAXIMO}.", 400)

    caja = Caja(codigo=f"TMP-{uuid4().hex[:16]}", usuario_id=usuario.id, monto_apertura=monto, estado="abierta")
    db.session.add(caja)
    db.session.flush()
    caja.codigo = f"C-{caja.id:06d}"
    db.session.commit()
    return jsonify(caja=caja_con_resumen(caja)), 201


@bp.get("/cajas/denominaciones")
@jwt_required()
def denominaciones():
    return jsonify(denominaciones=DENOMINACIONES)


@bp.post("/cajas/cerrar")
@jwt_required()
def cerrar_caja():
    """Arqueo: el vendedor cuenta el efectivo por denominación.

    diferencia = contado - esperado  (positiva = sobrante, negativa = faltante).
    Si hay diferencia, la observación es obligatoria.
    """
    require_permiso("caja.operar")
    usuario = _usuario_actual()
    caja = (Caja.query.filter_by(usuario_id=usuario.id, estado="abierta")
            .with_for_update().first())
    if not caja:
        raise ApiError("caja_cerrada", "No tiene una caja abierta para cerrar.", 409)

    data = request.get_json(silent=True) or {}
    conteo, contado = calcular_conteo(data.get("conteo") or {})
    resumen = resumen_caja(caja)
    esperado = Decimal(str(resumen["efectivo_esperado"]))
    diferencia = (contado - esperado).quantize(Decimal("0.01"))
    observacion = (data.get("observacion") or "").strip()
    if diferencia != 0 and len(observacion) < 10:
        tipo = "sobrante" if diferencia > 0 else "faltante"
        raise ApiError("observacion_requerida",
                       f"Hay un {tipo} de S/ {abs(diferencia)}. Explique la diferencia (mínimo 10 caracteres).", 400)

    caja.efectivo_esperado = esperado
    caja.efectivo_contado = contado
    caja.diferencia = diferencia
    caja.conteo_json = json.dumps(conteo)
    caja.observacion_cierre = observacion[:240] or None
    caja.fecha_cierre = ahora()
    caja.estado = "cerrada"
    db.session.commit()
    return jsonify(caja=caja_con_resumen(caja, include_ventas=True))


@bp.get("/cajas/mis-cajas")
@jwt_required()
def mis_cajas():
    require_permiso("caja.operar")
    cajas = (Caja.query.filter_by(usuario_id=_usuario_actual().id)
             .order_by(Caja.fecha_apertura.desc()).limit(15).all())
    return jsonify(cajas=[caja_con_resumen(c) for c in cajas])


def _puede_ver_caja(caja):
    """El dueño ve su caja; quien tenga caja.supervisar ve todas."""
    from .auth import _rol_activo
    rol = _rol_activo()
    permisos = {p.codigo for p in rol.permisos} if rol else set()
    if "caja.supervisar" in permisos:
        return True
    return "caja.operar" in permisos and caja.usuario_id == _usuario_actual().id


@bp.get("/cajas/<int:caja_id>")
@jwt_required()
def detalle_caja(caja_id):
    require_permiso("ventas.ver")
    caja = db.session.get(Caja, caja_id)
    if not caja or not _puede_ver_caja(caja):
        raise ApiError("caja_no_encontrada", "La caja no existe o no tiene acceso a ella.", 404)
    return jsonify(caja=caja_con_resumen(caja, include_ventas=True))
