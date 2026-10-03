"""Operaciones de stock con registro automático en el kardex."""
from ..errors import ApiError
from ..extensions import db
from ..models import MovimientoStock


def mover_stock(producto, cantidad, tipo, usuario=None, motivo=None, venta=None):
    """Aplica `cantidad` (positiva o negativa) al stock y registra el movimiento.

    No hace commit: quien llama decide cuándo confirmar la transacción.
    """
    if tipo not in MovimientoStock.TIPOS:
        raise ValueError(f"Tipo de movimiento desconocido: {tipo}")
    anterior = producto.stock or 0
    nuevo = anterior + int(cantidad)
    if nuevo < 0:
        raise ApiError(
            "stock_insuficiente",
            f"Stock insuficiente para {producto.nombre}. Disponible: {anterior}.",
            409,
        )
    producto.stock = nuevo
    movimiento = MovimientoStock(
        producto=producto,
        tipo=tipo,
        cantidad=int(cantidad),
        stock_anterior=anterior,
        stock_nuevo=nuevo,
        motivo=(motivo or "").strip()[:240] or None,
        usuario=usuario,
        venta=venta,
    )
    db.session.add(movimiento)
    return movimiento
