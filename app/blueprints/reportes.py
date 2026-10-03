"""Reportes de operación para Supervisor de Ventas y Administrador.

GET /api/reportes/resumen?desde=AAAA-MM-DD&hasta=AAAA-MM-DD
    Indicadores, ventas por día, por método de pago, por vendedor, por
    familia y top de productos. Sin fechas: últimos 30 días.
GET /api/reportes/inventario
    Valorización del inventario y productos con stock bajo o agotado.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy.orm import joinedload

from ..models import Producto, Venta, VentaDetalle
from .auth import require_permiso
from .ventas import ZONA_LIMA, rango_fechas

bp = Blueprint("reportes", __name__)


def _a_lima(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZONA_LIMA)


@bp.get("/reportes/resumen")
@jwt_required()
def resumen():
    require_permiso("reportes.ver")
    hoy = datetime.now(ZONA_LIMA).date()
    desde_txt = request.args.get("desde") or (hoy - timedelta(days=29)).isoformat()
    hasta_txt = request.args.get("hasta") or hoy.isoformat()
    inicio, fin = rango_fechas(desde_txt, hasta_txt)

    ventas = (Venta.query
              .options(joinedload(Venta.detalles).joinedload(VentaDetalle.producto).joinedload(Producto.familia),
                       joinedload(Venta.usuario))
              .filter(Venta.fecha >= inicio, Venta.fecha < fin)
              .all())

    completadas = [v for v in ventas if v.estado == "completada"]
    anuladas = [v for v in ventas if v.estado == "anulada"]
    total = sum((Decimal(v.total) for v in completadas), Decimal("0"))
    igv = sum((Decimal(v.igv) for v in completadas), Decimal("0"))
    unidades = sum(d.cantidad for v in completadas for d in v.detalles)

    # Ventas por día (todos los días del rango, aunque no haya ventas)
    por_dia = defaultdict(lambda: {"total": Decimal("0"), "ventas": 0})
    for v in completadas:
        dia = _a_lima(v.fecha).date().isoformat()
        por_dia[dia]["total"] += Decimal(v.total)
        por_dia[dia]["ventas"] += 1
    serie_dias = []
    d = date.fromisoformat(desde_txt)
    ultimo = date.fromisoformat(hasta_txt)
    while d <= ultimo and len(serie_dias) < 366:
        k = d.isoformat()
        serie_dias.append({"fecha": k, "total": float(por_dia[k]["total"]), "ventas": por_dia[k]["ventas"]})
        d += timedelta(days=1)

    por_metodo = defaultdict(lambda: {"total": Decimal("0"), "ventas": 0})
    por_vendedor = defaultdict(lambda: {"total": Decimal("0"), "ventas": 0, "nombre": ""})
    por_familia = defaultdict(lambda: {"total": Decimal("0"), "unidades": 0})
    por_producto = defaultdict(lambda: {"total": Decimal("0"), "unidades": 0, "nombre": "", "sku": None, "imagen_url": None})
    for v in completadas:
        por_metodo[v.metodo_pago]["total"] += Decimal(v.total)
        por_metodo[v.metodo_pago]["ventas"] += 1
        pv = por_vendedor[v.usuario_id]
        pv["total"] += Decimal(v.total)
        pv["ventas"] += 1
        pv["nombre"] = v.usuario.nombre_completo if v.usuario else "—"
        for det in v.detalles:
            fam = det.producto.familia.nombre if det.producto and det.producto.familia else "Sin familia"
            por_familia[fam]["total"] += Decimal(det.subtotal)
            por_familia[fam]["unidades"] += det.cantidad
            pp = por_producto[det.producto_id]
            pp["total"] += Decimal(det.subtotal)
            pp["unidades"] += det.cantidad
            if det.producto:
                pp.update(nombre=det.producto.nombre, sku=det.producto.sku, imagen_url=det.producto.imagen_url)

    def ordenar(dic, clave="total"):
        return sorted(dic.items(), key=lambda kv: kv[1][clave], reverse=True)

    return jsonify(
        desde=desde_txt,
        hasta=hasta_txt,
        # Claves históricas (las usa el panel anterior)
        total_facturado=float(total),
        ventas_completadas=len(completadas),
        ventas_anuladas=len(anuladas),
        indicadores={
            "total_vendido": float(total),
            "igv": float(igv),
            "base_imponible": float(total - igv),
            "ventas_completadas": len(completadas),
            "ventas_anuladas": len(anuladas),
            "monto_anulado": float(sum((Decimal(v.total) for v in anuladas), Decimal("0"))),
            "ticket_promedio": float((total / len(completadas)).quantize(Decimal("0.01"))) if completadas else 0.0,
            "unidades_vendidas": unidades,
        },
        por_dia=serie_dias,
        por_metodo=[{"metodo": k, "total": float(x["total"]), "ventas": x["ventas"]} for k, x in ordenar(por_metodo)],
        por_vendedor=[{"usuario_id": k, "nombre": x["nombre"], "total": float(x["total"]), "ventas": x["ventas"]}
                      for k, x in ordenar(por_vendedor)],
        por_familia=[{"familia": k, "total": float(x["total"]), "unidades": x["unidades"]} for k, x in ordenar(por_familia)],
        top_productos=[{"producto_id": k, "nombre": x["nombre"], "sku": x["sku"], "imagen_url": x["imagen_url"],
                        "unidades": x["unidades"], "total": float(x["total"])}
                       for k, x in ordenar(por_producto, "unidades")[:10]],
    )


@bp.get("/reportes/inventario")
@jwt_required()
def inventario():
    require_permiso("reportes.ver")
    productos = Producto.query.filter(Producto.activo.is_(True)).all()
    valor = sum((Decimal(p.precio) * p.stock for p in productos), Decimal("0"))
    criticos = sorted((p for p in productos if p.stock <= p.stock_minimo), key=lambda p: (p.stock, p.nombre))
    return jsonify(
        productos_activos=len(productos),
        unidades_en_stock=sum(p.stock for p in productos),
        valor_inventario=float(valor),
        agotados=sum(1 for p in productos if p.stock <= 0),
        stock_bajo=sum(1 for p in productos if 0 < p.stock <= p.stock_minimo),
        criticos=[p.to_dict() for p in criticos],
    )
