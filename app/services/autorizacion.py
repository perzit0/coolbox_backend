"""Autorización en caja con el PIN de un supervisor.

Escenario real (como en las cajas de supermercado): el cajero está atendiendo
y necesita hacer algo restringido (anular un ítem, aplicar un descuento mayor,
anular la venta, retirar efectivo...). En lugar de cerrar su sesión, llama al
supervisor, quien escribe su PIN en la misma pantalla.

    1. El cajero pide la autorización: POST /api/autorizaciones
       {accion, supervisor_id, pin, motivo, comentario, venta_id?, monto?, detalle?}
    2. Si el PIN es correcto se devuelve un token de UN SOLO USO, válido
       PIN_TOKEN_SEGUNDOS y solo para esa acción (y esa venta, si aplica).
    3. El cajero ejecuta la acción enviando `autorizacion_token`; el backend
       consume el token (consumir_token) y registra quién autorizó.

Acciones "inmediatas" (ANULAR_ITEM, VACIAR_CARRITO, REIMPRESION) no cambian
nada en el backend: quedan registradas en la bitácora en el mismo momento.
Todo intento (aprobado o con PIN errado) queda en la tabla `autorizacion`.
"""

import hashlib
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from flask import current_app

from ..errors import ApiError
from ..extensions import db
from ..models import Autorizacion, Usuario, ahora

ACCIONES = {
    "ANULAR_ITEM": "Retirar un producto del carrito",
    "VACIAR_CARRITO": "Cancelar la venta en curso",
    "ANULAR_VENTA": "Anular una venta ya cobrada",
    "RETIRO_EFECTIVO": "Retiro parcial de efectivo",
    "CIERRE_DIFERENCIA": "Cerrar caja con diferencia",
    "REIMPRESION": "Reimprimir un comprobante",
    "DEVOLUCION": "Devolución de productos con nota de crédito",
}
ACCIONES_INMEDIATAS = {"ANULAR_ITEM", "VACIAR_CARRITO", "REIMPRESION"}

MOTIVOS = {
    "error_digitacion": "Error de digitación",
    "cliente_desiste": "El cliente desiste",
    "producto_danado": "Producto dañado o con falla",
    "precio_incorrecto": "Precio incorrecto en góndola",
    "pago_rechazado": "Pago rechazado o incompleto",
    "promocion": "Promoción o compensación al cliente",
    "exceso_efectivo": "Exceso de efectivo en gaveta",
    "error_cobro": "Error en el cobro o en el vuelto",
    "solicitud_cliente": "Solicitud del cliente",
    "otro": "Otro",
}

# PIN débiles que no se aceptan
PIN_LARGO = 6
_PIN_DEBILES = {"123456", "654321", "012345", "543210", "123123", "121212", "112233", "456789", "987654", "123321"}


def _cfg(clave, defecto):
    return current_app.config.get(clave, defecto)


def token_segundos():
    return _cfg("PIN_TOKEN_SEGUNDOS", 90)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(dt):
    from datetime import timezone

    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def validar_pin_nuevo(pin, usuario):
    pin = (pin or "").strip()
    if not re.fullmatch(r"\d{6}", pin):
        raise ApiError("pin_invalido", "El PIN debe tener exactamente 6 dígitos.", 400)
    if len(set(pin)) == 1 or pin in _PIN_DEBILES or pin in "0123456789" or pin in "9876543210":
        raise ApiError("pin_debil", "El PIN es muy fácil de adivinar. Evita secuencias y dígitos repetidos.", 400)
    if usuario.dni and pin in usuario.dni:
        raise ApiError("pin_debil", "El PIN no puede ser parte de tu DNI.", 400)
    return pin


def _monto(valor):
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ApiError("monto_invalido", "El monto de la autorización no es válido.", 400)


def solicitar(solicitante, caja, data):
    """Valida el PIN del supervisor y devuelve (autorizacion, token|None)."""
    accion = (data.get("accion") or "").strip().upper()
    if accion not in ACCIONES:
        raise ApiError("accion_invalida", "La acción a autorizar no es válida.", 400)
    motivo = (data.get("motivo") or "").strip()
    if motivo not in MOTIVOS:
        raise ApiError("motivo_requerido", "Seleccione el motivo de la autorización.", 400)
    comentario = (data.get("comentario") or "").strip()[:240] or None
    if motivo == "otro" and not comentario:
        raise ApiError("comentario_requerido", "Describa el motivo en el comentario.", 400)

    try:
        supervisor_id = int(data.get("supervisor_id"))
    except (TypeError, ValueError):
        raise ApiError("supervisor_requerido", "Seleccione al supervisor que autoriza.", 400)
    supervisor = Usuario.query.filter_by(id=supervisor_id).with_for_update().first()
    if not supervisor or supervisor.estado != "activo" or not supervisor.puede_autorizar:
        raise ApiError("supervisor_invalido", "El usuario seleccionado no puede autorizar.", 403)
    if supervisor.id == solicitante.id:
        raise ApiError("autoautorizacion", "No puede autorizar sus propias operaciones. Llame a otro supervisor.", 403)
    if not supervisor.pin_hash:
        raise ApiError(
            "pin_no_configurado",
            f"{supervisor.nombres} aún no configura su PIN (Mi cuenta > PIN de autorización).",
            409,
        )

    venta_id = data.get("venta_id")
    try:
        venta_id = int(venta_id) if venta_id not in (None, "") else None
    except (TypeError, ValueError):
        raise ApiError("venta_invalida", "La venta indicada no es válida.", 400)

    registro = Autorizacion(
        accion=accion,
        solicitante_id=solicitante.id,
        supervisor_id=supervisor.id,
        caja_id=caja.id if caja else None,
        venta_id=venta_id,
        monto=_monto(data.get("monto")),
        motivo=motivo,
        comentario=comentario,
        detalle=(data.get("detalle") or "").strip()[:240] or None,
    )

    bloqueado = _aware(supervisor.pin_bloqueado_hasta)
    if bloqueado and bloqueado > ahora():
        registro.resultado = "bloqueado"
        db.session.add(registro)
        db.session.commit()
        minutos = max(1, int((bloqueado - ahora()).total_seconds() // 60) + 1)
        raise ApiError(
            "pin_bloqueado",
            f"El PIN de {supervisor.nombres} está bloqueado por intentos fallidos. Intente en {minutos} min.",
            423,
        )

    pin_ingresado = str(data.get("pin") or "")
    if not re.fullmatch(r"\d{6}", pin_ingresado):
        raise ApiError("pin_invalido", "El PIN debe tener 6 dígitos.", 400)

    if not supervisor.check_pin(pin_ingresado):
        supervisor.pin_intentos = (supervisor.pin_intentos or 0) + 1
        registro.resultado = "pin_invalido"
        db.session.add(registro)
        restantes = _cfg("PIN_MAX_INTENTOS", 3) - supervisor.pin_intentos
        if restantes <= 0:
            supervisor.pin_intentos = 0
            supervisor.pin_bloqueado_hasta = ahora() + timedelta(minutes=_cfg("PIN_MINUTOS_BLOQUEO", 5))
            db.session.commit()
            raise ApiError(
                "pin_bloqueado",
                f"PIN incorrecto. El PIN de {supervisor.nombres} se bloqueó {_cfg('PIN_MINUTOS_BLOQUEO', 5)} minutos.",
                423,
            )
        db.session.commit()
        raise ApiError(
            "pin_incorrecto",
            f"PIN incorrecto. Queda{'n' if restantes > 1 else ''} {restantes} intento{'s' if restantes > 1 else ''}.",
            401,
        )

    supervisor.pin_intentos = 0
    supervisor.pin_bloqueado_hasta = None
    registro.resultado = "aprobada"
    token = None
    if accion in ACCIONES_INMEDIATAS:
        registro.usada = True
    else:
        token = secrets.token_urlsafe(24)
        registro.token_hash = _hash(token)
        registro.expira_en = ahora() + timedelta(seconds=token_segundos())
    db.session.add(registro)
    db.session.commit()
    return registro, token


def consumir_token(token, accion, solicitante, venta_id=None, monto=None):
    """Marca como usado un token vigente para esa acción. Devuelve la Autorizacion.

    Se llama dentro de la transacción de la acción: si la acción falla y se hace
    rollback, el token vuelve a quedar disponible hasta que expire.
    """
    if not token:
        raise ApiError(
            "autorizacion_requerida",
            f"Esta acción requiere la autorización de un supervisor ({ACCIONES.get(accion, accion)}).",
            403,
        )
    registro = (
        Autorizacion.query.filter_by(token_hash=_hash(str(token)), resultado="aprobada").with_for_update().first()
    )
    if not registro or registro.usada:
        raise ApiError(
            "autorizacion_invalida", "La autorización no es válida o ya fue usada. Solicítela de nuevo.", 403
        )
    if registro.accion != accion or registro.solicitante_id != solicitante.id:
        raise ApiError("autorizacion_invalida", "La autorización corresponde a otra operación.", 403)
    if venta_id is not None and registro.venta_id not in (None, venta_id):
        raise ApiError("autorizacion_invalida", "La autorización corresponde a otra venta.", 403)
    if _aware(registro.expira_en) < ahora():
        raise ApiError(
            "autorizacion_vencida", "La autorización venció. Pida al supervisor que ingrese su PIN otra vez.", 403
        )
    if monto is not None and registro.monto is not None and Decimal(str(monto)) > registro.monto:
        raise ApiError(
            "autorizacion_invalida",
            f"El supervisor autorizó hasta S/ {registro.monto}; el monto solicitado es mayor.",
            403,
        )
    registro.usada = True
    if venta_id is not None and registro.venta_id is None:
        registro.venta_id = venta_id
    return registro
