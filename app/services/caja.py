"""Cálculos del turno de caja (resumen, efectivo esperado y arqueo)."""
from datetime import timedelta, timezone
from decimal import Decimal
import json

from ..errors import ApiError
from ..models import Caja

CENTIMO = Decimal("0.01")
ZONA_LIMA = timezone(timedelta(hours=-5))

# Denominaciones del sol peruano usadas en el arqueo (billetes y monedas)
DENOMINACIONES = ["200", "100", "50", "20", "10", "5", "2", "1", "0.50", "0.20", "0.10"]


def fecha_lima(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZONA_LIMA).date()


def calcular_conteo(conteo):
    """Valida {denominacion: cantidad} y devuelve (conteo_limpio, total)."""
    if not isinstance(conteo, dict):
        raise ApiError("conteo_invalido", "El conteo de efectivo no tiene un formato válido.", 400)
    limpio, total = {}, Decimal("0")
    for den in DENOMINACIONES:
        valor = conteo.get(den) or 0
        try:
            cantidad = int(valor)
        except (TypeError, ValueError):
            raise ApiError("conteo_invalido", f"La cantidad para S/ {den} debe ser un número entero.", 400)
        if cantidad < 0 or cantidad > 10000:
            raise ApiError("conteo_invalido", f"La cantidad para S/ {den} no es válida.", 400)
        if cantidad:
            limpio[den] = cantidad
            total += Decimal(den) * cantidad
    return limpio, total.quantize(CENTIMO)


def caja_abierta_de(usuario):
    """Caja abierta del usuario, o None."""
    return Caja.query.filter_by(usuario_id=usuario.id, estado="abierta").first()


def exigir_caja_abierta(usuario):
    from ..models import ahora
    caja = caja_abierta_de(usuario)
    if not caja:
        raise ApiError("caja_cerrada", "Debe abrir su caja antes de registrar ventas.", 409)
    # La caja se cierra el mismo día: una caja olvidada abierta de ayer no recibe ventas nuevas.
    if fecha_lima(caja.fecha_apertura) < fecha_lima(ahora()):
        raise ApiError("caja_vencida",
                       f"Su caja {caja.codigo} es de un día anterior. Ciérrela con su arqueo antes de vender.", 409)
    return caja


METODOS_ELECTRONICOS = ("tarjeta", "qr", "yape", "plin")


def resumen_caja(caja):
    """Totales del turno: cobros por método de pago, retiros y efectivo esperado.

    Efectivo esperado = fondo inicial + cobros en efectivo (netos de vuelto)
                        - devoluciones pagadas en efectivo - retiros.
    Las ventas anuladas no suman (su dinero se devolvió al cliente).
    Con pago mixto, cada parte suma en su propio método.
    Las devoluciones restan en el método con el que se reembolsó (en la caja donde se
    atendió, que puede ser otra que la de la venta): así el total de tarjeta, QR, Yape o
    Plin cuadra con el cierre del POS o de la app, que ya muestra el extorno.
    """
    from flask import current_app
    por_metodo = {}
    completadas = anuladas = 0
    total = Decimal("0")
    total_anulado = Decimal("0")
    total_descuentos = Decimal("0")
    for v in caja.ventas:
        monto_venta = Decimal(str(v.total))
        if v.estado == "anulada":
            anuladas += 1
            total_anulado += monto_venta
            continue
        completadas += 1
        total += monto_venta
        total_descuentos += Decimal(str(v.descuento or 0))
        for metodo, monto in v.pagos_efectivos():
            fila = por_metodo.setdefault(metodo, {"metodo": metodo, "ventas": 0, "total": Decimal("0")})
            fila["ventas"] += 1
            fila["total"] += monto

    efectivo_ventas = por_metodo.get("efectivo", {}).get("total", Decimal("0"))
    total_devuelto = Decimal("0")
    devuelto_efectivo = Decimal("0")
    for d in caja.devoluciones:
        monto = Decimal(str(d.total))
        total_devuelto += monto
        if d.metodo_reembolso == "efectivo":
            devuelto_efectivo += monto
        fila = por_metodo.setdefault(d.metodo_reembolso, {"metodo": d.metodo_reembolso, "ventas": 0,
                                                          "total": Decimal("0")})
        fila["total"] -= monto
        fila["devuelto"] = fila.get("devuelto", Decimal("0")) + monto

    retiros = sum((Decimal(str(m.monto)) for m in caja.movimientos if m.tipo == "retiro"), Decimal("0"))
    esperado = (Decimal(str(caja.monto_apertura)) + efectivo_ventas - devuelto_efectivo - retiros).quantize(CENTIMO)
    limite = Decimal(str(current_app.config.get("LIMITE_EFECTIVO_CAJA", 1500)))
    return {
        "ventas_completadas": completadas,
        "ventas_anuladas": anuladas,
        "total_vendido": float(total.quantize(CENTIMO)),
        "total_anulado": float(total_anulado.quantize(CENTIMO)),
        "total_descuentos": float(total_descuentos.quantize(CENTIMO)),
        "efectivo_ventas": float(efectivo_ventas.quantize(CENTIMO)),
        "devoluciones": len(caja.devoluciones),
        "total_devuelto": float(total_devuelto.quantize(CENTIMO)),
        "devuelto_efectivo": float(devuelto_efectivo.quantize(CENTIMO)),
        "retiros": float(retiros.quantize(CENTIMO)),
        "efectivo_esperado": float(esperado),
        "limite_efectivo": float(limite),
        "requiere_retiro": esperado > limite,
        "por_metodo": [
            {"metodo": f["metodo"], "ventas": f["ventas"], "total": float(f["total"].quantize(CENTIMO)),
             "devuelto": float(f.get("devuelto", Decimal("0")).quantize(CENTIMO))}
            for f in sorted(por_metodo.values(), key=lambda f: f["total"], reverse=True)
        ],
    }


def ocultar_para_cuadre_ciego(data):
    """Cuadre ciego: mientras la caja está abierta, el cajero no ve cuánto efectivo
    debería tener. Así cuenta lo que realmente hay y no "ajusta" el conteo."""
    r = data["resumen"]
    r["cuadre_ciego"] = True
    for clave in ("efectivo_esperado", "efectivo_ventas"):
        r[clave] = None
    r["por_metodo"] = [m for m in r["por_metodo"] if m["metodo"] != "efectivo"] + (
        [{"metodo": "efectivo", "ventas": next((m["ventas"] for m in r["por_metodo"] if m["metodo"] == "efectivo"), 0),
          "total": None}] if any(m["metodo"] == "efectivo" for m in r["por_metodo"]) else [])
    return data


def caja_con_resumen(caja, include_ventas=False, ciego=False):
    data = caja.to_dict()
    data["resumen"] = resumen_caja(caja)
    data["conteo"] = json.loads(caja.conteo_json) if caja.conteo_json else None
    data["conciliacion"] = json.loads(caja.conciliacion_json) if caja.conciliacion_json else None
    data["retiros"] = [m.to_dict() for m in caja.movimientos]
    data["devoluciones"] = [{k: v for k, v in d.to_dict().items() if k != "detalles"} for d in caja.devoluciones]
    if include_ventas:
        data["ventas"] = [v.to_dict(include_detalles=False) for v in caja.ventas]
        from ..models import Autorizacion
        data["autorizaciones"] = [a.to_dict() for a in
                                  Autorizacion.query.filter_by(caja_id=caja.id).order_by(Autorizacion.fecha).all()]
    if ciego and caja.estado == "abierta":
        ocultar_para_cuadre_ciego(data)
    return data
