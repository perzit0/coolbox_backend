"""Turnos de caja del vendedor.

    GET  /api/cajas/mi-caja  -> caja abierta del usuario (o null) con su resumen
    POST /api/cajas/abrir    -> abre la caja con un fondo inicial en efectivo
"""
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Caja, Usuario
from ..services.caja import caja_abierta_de, caja_con_resumen
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
