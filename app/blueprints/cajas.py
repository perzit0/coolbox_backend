"""Turnos de caja del cajero.

GET  /api/cajas/mi-caja    -> caja abierta del usuario (o null) con su resumen (cuadre ciego)
POST /api/cajas/abrir      -> abre la caja con un fondo inicial en efectivo
POST /api/cajas/retiro     -> retiro parcial de efectivo (sangría) con PIN de supervisor
POST /api/cajas/cerrar     -> cierra la caja con el arqueo (conteo de efectivo + conciliación)
GET  /api/cajas/mis-cajas  -> últimos turnos del usuario
GET  /api/cajas/<id>       -> detalle de un turno (reporte de cierre)
GET  /api/cajas            -> control de cajas para el supervisor (filtros por fecha/estado/cajero)
"""

import json
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Caja, MovimientoCaja, Usuario, ahora
from ..services.autorizacion import consumir_token
from ..services.caja import (
    DENOMINACIONES,
    METODOS_ELECTRONICOS,
    caja_abierta_de,
    caja_con_resumen,
    calcular_conteo,
    exigir_caja_abierta,
    resumen_caja,
)
from .auth import _usuario_actual, _usuario_actual_o_404, require_permiso

bp = Blueprint("cajas", __name__)


def _monto(valor, campo):
    try:
        monto = Decimal(str(valor)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ApiError("monto_invalido", f"El {campo} debe ser un número.", 400)
    if monto < 0:
        raise ApiError("monto_invalido", f"El {campo} no puede ser negativo.", 400)
    return monto


def _ciego():
    """El cuadre ciego aplica a quien opera la caja, no a quien la supervisa."""
    from .auth import _rol_activo

    rol = _rol_activo()
    permisos = {p.codigo for p in rol.permisos} if rol else set()
    return bool(current_app.config.get("CUADRE_CIEGO", True)) and "caja.supervisar" not in permisos


@bp.get("/cajas/mi-caja")
@jwt_required()
def mi_caja():
    require_permiso("caja.operar")
    caja = caja_abierta_de(_usuario_actual())
    return jsonify(caja=caja_con_resumen(caja, ciego=_ciego()) if caja else None)


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
    fondo_maximo = Decimal(str(current_app.config.get("CAJA_FONDO_MAXIMO", 5000)))
    if monto > fondo_maximo:
        raise ApiError("monto_invalido", f"El fondo inicial no puede superar S/ {fondo_maximo}.", 400)

    caja = Caja(codigo=f"TMP-{uuid4().hex[:16]}", usuario_id=usuario.id, monto_apertura=monto, estado="abierta")
    db.session.add(caja)
    db.session.flush()
    caja.codigo = f"C-{caja.id:06d}"
    db.session.commit()
    return jsonify(caja=caja_con_resumen(caja, ciego=_ciego())), 201


@bp.post("/cajas/retiro")
@jwt_required()
def retiro_efectivo():
    """Retiro parcial (sangría): el supervisor se lleva efectivo de la gaveta.

    No se puede retirar más de lo que debería haber en caja (fondo + cobros en efectivo - retiros).
    """
    require_permiso("caja.operar")
    usuario = _usuario_actual()
    caja = exigir_caja_abierta(usuario)
    data = request.get_json(silent=True) or {}
    monto = _monto(data.get("monto"), "monto del retiro")
    if monto <= 0:
        raise ApiError("monto_invalido", "El monto del retiro debe ser mayor a cero.", 400)
    disponible = Decimal(str(resumen_caja(caja)["efectivo_esperado"]))
    if monto > disponible:
        raise ApiError("monto_invalido", "El retiro supera el efectivo registrado en la caja.", 400)
    autorizacion = consumir_token(data.get("autorizacion_token"), "RETIRO_EFECTIVO", usuario, monto=monto)
    mov = MovimientoCaja(
        caja_id=caja.id,
        tipo="retiro",
        monto=monto,
        motivo=(data.get("motivo") or autorizacion.comentario or "Retiro parcial de efectivo")[:240],
        usuario_id=usuario.id,
        supervisor_id=autorizacion.supervisor_id,
        autorizacion_id=autorizacion.id,
    )
    db.session.add(mov)
    db.session.commit()
    return jsonify(movimiento=mov.to_dict(), caja=caja_con_resumen(caja, ciego=_ciego())), 201


@bp.get("/cajas/denominaciones")
@jwt_required()
def denominaciones():
    _usuario_actual_o_404()  # solo personal de la tienda (no sesiones de clientes)
    return jsonify(denominaciones=DENOMINACIONES)


@bp.post("/cajas/cerrar")
@jwt_required()
def cerrar_caja():
    """Arqueo: el cajero cuenta el efectivo por denominación y declara lo que
    marcan el cierre del POS y los reportes de Yape/Plin.

    diferencia = contado - esperado  (positiva = sobrante, negativa = faltante).
    Si hay diferencia (en efectivo o en algún medio electrónico) se exige una
    observación y el PIN de un supervisor. La primera vez que se detecta, la API
    responde 409 `diferencia_detectada` con el detalle, para que el cajero pueda
    volver a contar o llamar al supervisor.
    """
    require_permiso("caja.operar")
    usuario = _usuario_actual()
    caja = Caja.query.filter_by(usuario_id=usuario.id, estado="abierta").with_for_update().first()
    if not caja:
        raise ApiError("caja_cerrada", "No tiene una caja abierta para cerrar.", 409)

    data = request.get_json(silent=True) or {}
    conteo, contado = calcular_conteo(data.get("conteo") or {})
    resumen = resumen_caja(caja)
    esperado = Decimal(str(resumen["efectivo_esperado"]))
    diferencia = (contado - esperado).quantize(Decimal("0.01"))

    # Conciliación de medios electrónicos (lo que dice el POS / la app vs. el sistema)
    declarado = data.get("declarado") or {}
    sistema = {
        m["metodo"]: Decimal(str(m["total"])) for m in resumen["por_metodo"] if m["metodo"] in METODOS_ELECTRONICOS
    }
    conciliacion = {}
    for metodo in METODOS_ELECTRONICOS:
        valor = declarado.get(metodo)
        if valor in (None, "") and metodo not in sistema:
            continue
        decl = _monto(valor if valor not in (None, "") else sistema.get(metodo, 0), f"monto declarado de {metodo}")
        sis = sistema.get(metodo, Decimal("0")).quantize(Decimal("0.01"))
        conciliacion[metodo] = {"sistema": float(sis), "declarado": float(decl), "diferencia": float(decl - sis)}
    dif_electronica = [m for m, c in conciliacion.items() if c["diferencia"] != 0]

    observacion = (data.get("observacion") or "").strip()
    autorizacion = None
    if diferencia != 0 or dif_electronica:
        detalle = {
            "efectivo_esperado": float(esperado),
            "efectivo_contado": float(contado),
            "diferencia": float(diferencia),
            "conciliacion": conciliacion,
        }
        if not data.get("autorizacion_token") or len(observacion) < 10:
            partes = []
            if diferencia != 0:
                partes.append(f"{'sobrante' if diferencia > 0 else 'faltante'} de S/ {abs(diferencia)} en efectivo")
            if dif_electronica:
                partes.append("diferencias en " + ", ".join(dif_electronica))
            raise ApiError(
                "diferencia_detectada",
                f"Hay {' y '.join(partes)}. Vuelva a contar o cierre con observación y autorización del supervisor.",
                409,
                extra={"arqueo": detalle},
            )
        autorizacion = consumir_token(data.get("autorizacion_token"), "CIERRE_DIFERENCIA", usuario)

    caja.efectivo_esperado = esperado
    caja.efectivo_contado = contado
    caja.diferencia = diferencia
    caja.conteo_json = json.dumps(conteo)
    caja.conciliacion_json = json.dumps(conciliacion) if conciliacion else None
    caja.observacion_cierre = observacion[:240] or None
    caja.autorizacion_cierre_id = autorizacion.id if autorizacion else None
    caja.fecha_cierre = ahora()
    caja.estado = "cerrada"
    db.session.commit()
    return jsonify(caja=caja_con_resumen(caja, include_ventas=True))


@bp.get("/cajas/mis-cajas")
@jwt_required()
def mis_cajas():
    require_permiso("caja.operar")
    cajas = Caja.query.filter_by(usuario_id=_usuario_actual().id).order_by(Caja.fecha_apertura.desc()).limit(15).all()
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
    return jsonify(caja=caja_con_resumen(caja, include_ventas=True, ciego=_ciego()))


@bp.get("/cajas")
@jwt_required()
def control_cajas():
    """Cajas de todos los cajeros. Parámetros: desde, hasta (AAAA-MM-DD), estado, usuario_id."""
    require_permiso("caja.supervisar")
    from .ventas import rango_fechas

    inicio, fin = rango_fechas(request.args.get("desde"), request.args.get("hasta"))
    query = Caja.query
    if inicio:
        query = query.filter(Caja.fecha_apertura >= inicio)
    if fin:
        query = query.filter(Caja.fecha_apertura < fin)
    estado = request.args.get("estado")
    if estado in ("abierta", "cerrada"):
        query = query.filter(Caja.estado == estado)
    usuario_id = request.args.get("usuario_id", type=int)
    if usuario_id:
        query = query.filter(Caja.usuario_id == usuario_id)
    cajas = query.order_by(Caja.fecha_apertura.desc()).limit(200).all()
    data = [caja_con_resumen(c) for c in cajas]

    cerradas = [c for c in data if c["estado"] == "cerrada"]
    indicadores = {
        "abiertas": sum(1 for c in data if c["estado"] == "abierta"),
        "cerradas": len(cerradas),
        "total_vendido": round(sum(c["resumen"]["total_vendido"] for c in data), 2),
        "sobrantes": round(sum(c["diferencia"] for c in cerradas if c["diferencia"] > 0), 2),
        "faltantes": round(sum(-c["diferencia"] for c in cerradas if c["diferencia"] < 0), 2),
        "con_diferencia": sum(1 for c in cerradas if c["diferencia"] != 0),
    }
    vendedores = sorted({(c["usuario_id"], c["usuario_nombre"]) for c in data}, key=lambda x: x[1] or "")
    return jsonify(cajas=data, indicadores=indicadores, vendedores=[{"id": i, "nombre": n} for i, n in vendedores])
