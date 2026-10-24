"""Cálculos del turno de caja (resumen y efectivo esperado)."""
from decimal import Decimal

from ..errors import ApiError
from ..models import Caja

CENTIMO = Decimal("0.01")


def caja_abierta_de(usuario):
    """Caja abierta del usuario, o None."""
    return Caja.query.filter_by(usuario_id=usuario.id, estado="abierta").first()


def exigir_caja_abierta(usuario):
    caja = caja_abierta_de(usuario)
    if not caja:
        raise ApiError("caja_cerrada", "Debe abrir su caja antes de registrar ventas.", 409)
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


def caja_con_resumen(caja):
    data = caja.to_dict()
    data["resumen"] = resumen_caja(caja)
    return data
