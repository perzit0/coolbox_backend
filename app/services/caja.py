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


def resumen_caja(caja):
    """Totales del turno: ventas por método de pago y efectivo esperado en caja.

    Efectivo esperado = fondo inicial + ventas completadas en efectivo.
    Las ventas anuladas no suman (su dinero se devolvió al cliente).
    """
    por_metodo = {}
    completadas = anuladas = 0
    total = Decimal("0")
    total_anulado = Decimal("0")
    for v in caja.ventas:
        monto = Decimal(str(v.total))
        if v.estado == "anulada":
            anuladas += 1
            total_anulado += monto
            continue
        completadas += 1
        total += monto
        fila = por_metodo.setdefault(v.metodo_pago, {"metodo": v.metodo_pago, "ventas": 0, "total": Decimal("0")})
        fila["ventas"] += 1
        fila["total"] += monto

    efectivo_ventas = por_metodo.get("efectivo", {}).get("total", Decimal("0"))
    esperado = (Decimal(str(caja.monto_apertura)) + efectivo_ventas).quantize(CENTIMO)
    return {
        "ventas_completadas": completadas,
        "ventas_anuladas": anuladas,
        "total_vendido": float(total.quantize(CENTIMO)),
        "total_anulado": float(total_anulado.quantize(CENTIMO)),
        "efectivo_ventas": float(efectivo_ventas.quantize(CENTIMO)),
        "efectivo_esperado": float(esperado),
        "por_metodo": [
            {"metodo": f["metodo"], "ventas": f["ventas"], "total": float(f["total"].quantize(CENTIMO))}
            for f in sorted(por_metodo.values(), key=lambda f: f["total"], reverse=True)
        ],
    }


def caja_con_resumen(caja, include_ventas=False):
    data = caja.to_dict()
    data["resumen"] = resumen_caja(caja)
    data["conteo"] = json.loads(caja.conteo_json) if caja.conteo_json else None
    if include_ventas:
        data["ventas"] = [v.to_dict(include_detalles=False) for v in caja.ventas]
    return data
