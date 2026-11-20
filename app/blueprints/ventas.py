"""Registro de ventas en tienda.

El código de venta se genera de forma correlativa (V-000001) al confirmarla.
Toda venta se registra en la caja abierta del cajero que la cobra.
Al registrar la venta se descuenta stock; al anularla se devuelve stock.
Ambos movimientos quedan en el kardex.

Medios de pago (como en una caja real de Lima):
    efectivo  -> monto recibido y vuelto
    tarjeta   -> POS (OpenPay, Niubiz, Izipay...), débito/crédito, N° de operación del voucher
    qr        -> QR que muestra el POS (Yape, Plin u otra billetera), N° de operación
    yape/plin -> pago directo al número de la tienda, N° de operación de la app
Una venta puede pagarse con varios medios (pago mixto); la suma debe ser igual al total.
"""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..services import promociones as promo_svc
from ..extensions import db
import re
from pathlib import Path

from ..models import Producto, Usuario, Venta, VentaDetalle, VentaPago, ahora
from ..services.autorizacion import consumir_token
from ..services.caja import exigir_caja_abierta
from ..services.inventario import mover_stock
from ..services import sunat as sunat_svc
from .auth import _usuario_actual, require_permiso

bp = Blueprint("ventas", __name__)

# Perú no tiene horario de verano: UTC-5 todo el año.
ZONA_LIMA = timezone(timedelta(hours=-5))
CENTIMO = Decimal("0.01")


def rango_fechas(desde_txt, hasta_txt):
    """Convierte fechas YYYY-MM-DD (hora de Lima) a un rango UTC [inicio, fin)."""
    def parse(txt, etiqueta):
        try:
            return datetime.strptime(txt, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            raise ApiError("fecha_invalida", f"La fecha '{etiqueta}' debe tener formato AAAA-MM-DD.", 400)

    inicio = fin = None
    if desde_txt:
        inicio = datetime.combine(parse(desde_txt, "desde"), time.min, ZONA_LIMA).astimezone(timezone.utc)
    if hasta_txt:
        fin = (datetime.combine(parse(hasta_txt, "hasta"), time.min, ZONA_LIMA) + timedelta(days=1)).astimezone(timezone.utc)
    if inicio and fin and inicio >= fin:
        raise ApiError("rango_invalido", "La fecha 'desde' debe ser anterior o igual a 'hasta'.", 400)
    return inicio, fin


@bp.get("/ventas")
@jwt_required()
def listar_ventas():
    """Historial con filtros y paginación.

    Parámetros: desde, hasta (AAAA-MM-DD, hora de Lima), estado, metodo_pago,
    usuario_id, q (código de venta o cliente), page, per_page.
    """
    require_permiso("ventas.ver")
    inicio, fin = rango_fechas(request.args.get("desde"), request.args.get("hasta"))
    query = Venta.query
    if inicio:
        query = query.filter(Venta.fecha >= inicio)
    if fin:
        query = query.filter(Venta.fecha < fin)
    estado = request.args.get("estado")
    if estado in ("completada", "anulada"):
        query = query.filter(Venta.estado == estado)
    metodo = request.args.get("metodo_pago")
    if metodo in Venta.METODOS_PAGO:
        query = query.filter(db.or_(Venta.metodo_pago == metodo, Venta.pagos.any(VentaPago.metodo == metodo)))
    elif metodo == "mixto":
        query = query.filter(Venta.metodo_pago == "mixto")
    usuario_id = request.args.get("usuario_id", type=int)
    if usuario_id:
        query = query.filter(Venta.usuario_id == usuario_id)
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Venta.codigo.ilike(like), Venta.cliente_nombre.ilike(like),
                                    Venta.cliente_documento.ilike(like),
                                    Venta.pagos.any(VentaPago.nro_operacion.ilike(like))))

    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 20, type=int), 5), 100)
    total = query.count()
    ventas = (query.order_by(Venta.fecha.desc(), Venta.id.desc())
              .offset((page - 1) * per_page).limit(per_page).all())
    return jsonify(
        ventas=[v.to_dict(include_detalles=False) for v in ventas],
        page=page, per_page=per_page, total=total,
        pages=max(1, -(-total // per_page)),
    )


@bp.get("/ventas/vendedores")
@jwt_required()
def vendedores():
    """Usuarios que registraron ventas (cajeros, para el filtro del historial)."""
    require_permiso("ventas.ver")
    ids = [i for (i,) in db.session.query(Venta.usuario_id).distinct().all()]
    usuarios = Usuario.query.filter(Usuario.id.in_(ids)).order_by(Usuario.nombres).all() if ids else []
    return jsonify(vendedores=[{"id": u.id, "nombre": u.nombre_completo} for u in usuarios])


@bp.get("/ventas/<int:venta_id>")
@jwt_required()
def obtener_venta(venta_id):
    require_permiso("ventas.ver")
    venta = db.get_or_404(Venta, venta_id)
    return jsonify(venta=venta.to_dict())


PROVEEDORES_POS = ("OpenPay", "Niubiz", "Izipay", "Culqi", "Otro")
MARCAS_TARJETA = ("Visa", "Mastercard", "American Express", "Diners Club")


@bp.post("/ventas/cotizar")
@jwt_required()
def cotizar_venta():
    """Precios del carrito de caja con las promociones vigentes en tienda (sin registrar nada)."""
    require_permiso("ventas.crear")
    items = (request.get_json(silent=True) or {}).get("items") or []
    cantidades = {}
    for it in items[:60]:
        try:
            pid, q = int(it.get("producto_id")), int(it.get("cantidad") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if q > 0:
            cantidades[pid] = cantidades.get(pid, 0) + q
    productos = {p.id: p for p in Producto.query.filter(Producto.id.in_(cantidades.keys() or [0])).all()}
    cot = promo_svc.cotizar([(productos[pid], q) for pid, q in cantidades.items() if pid in productos], "tienda")
    return jsonify(
        items=[{"producto_id": it["producto"].id, "cantidad": it["cantidad"], "bruto": float(it["bruto"]),
                "descuento": float(it["descuento"]), "neto": float(it["neto"]),
                "promocion": {"id": it["promocion"].id, "nombre": it["promocion"].nombre,
                              "etiqueta": promo_svc.etiqueta(it["promocion"])} if it["promocion"] else None}
               for it in cot["items"]],
        subtotal=float(cot["subtotal"]), descuento_promociones=float(cot["descuento"]), total=float(cot["total"]))


def _decimal(valor, mensaje):
    try:
        return Decimal(str(valor)).quantize(CENTIMO)
    except (InvalidOperation, ValueError, TypeError):
        raise ApiError("monto_invalido", mensaje, 400)


def _leer_pagos(data, total):
    """Normaliza y valida los pagos. Devuelve [dict] listos para VentaPago.

    Compatibilidad: si no llega `pagos`, se arma uno solo con `metodo_pago`
    (y `monto_recibido` para efectivo), como en la versión anterior.
    """
    pagos = data.get("pagos")
    if not pagos:
        pagos = [{"metodo": data.get("metodo_pago") or "efectivo", "monto": str(total),
                  "monto_recibido": data.get("monto_recibido"), "nro_operacion": data.get("nro_operacion"),
                  "proveedor": data.get("proveedor")}]
    if not isinstance(pagos, list) or len(pagos) > 4:
        raise ApiError("pagos_invalidos", "Indique entre 1 y 4 medios de pago.", 400)

    limpios, suma, hay_efectivo = [], Decimal("0"), False
    for p in pagos:
        metodo = (p.get("metodo") or "").strip().lower()
        if metodo not in Venta.METODOS_PAGO:
            raise ApiError("metodo_invalido", "Método de pago no soportado.", 400)
        monto = _decimal(p.get("monto"), "Cada pago debe tener un monto numérico.")
        if monto <= 0:
            raise ApiError("monto_invalido", "Cada pago debe tener un monto mayor a cero.", 400)
        fila = {"metodo": metodo, "monto": monto, "monto_recibido": None, "vuelto": None,
                "proveedor": None, "tipo_tarjeta": None, "nro_operacion": None, "marca_tarjeta": None}
        if metodo == "efectivo":
            if hay_efectivo:
                raise ApiError("pagos_invalidos", "Registre el efectivo en un solo pago.", 400)
            hay_efectivo = True
            if p.get("monto_recibido") not in (None, ""):
                recibido = _decimal(p["monto_recibido"], "El monto recibido debe ser numérico.")
                if recibido < monto:
                    raise ApiError("monto_insuficiente", f"El efectivo recibido no cubre S/ {monto}.", 400)
                fila["monto_recibido"], fila["vuelto"] = recibido, (recibido - monto).quantize(CENTIMO)
        else:
            nro = re.sub(r"\s+", "", str(p.get("nro_operacion") or "")).upper()
            if not re.fullmatch(r"[A-Z0-9-]{4,30}", nro):
                raise ApiError("operacion_requerida",
                               f"Ingrese el N° de operación del pago con {metodo} (4 a 30 caracteres, letras o números).", 400)
            fila["nro_operacion"] = nro
            if metodo in ("tarjeta", "qr"):
                proveedor = (p.get("proveedor") or "").strip()
                if proveedor not in PROVEEDORES_POS:
                    raise ApiError("proveedor_requerido", "Seleccione el POS con el que se cobró (OpenPay, Niubiz, Izipay...).", 400)
                fila["proveedor"] = proveedor
            if metodo == "tarjeta":
                marca = (p.get("marca_tarjeta") or "").strip()
                if marca and marca not in MARCAS_TARJETA:
                    raise ApiError("marca_tarjeta_invalida", "Marca de tarjeta no válida.", 400)
                fila["marca_tarjeta"] = marca or None
                tipo = (p.get("tipo_tarjeta") or "").strip().lower()
                if tipo not in ("debito", "credito"):
                    raise ApiError("tipo_tarjeta_requerido", "Indique si la tarjeta es de débito o crédito.", 400)
                fila["tipo_tarjeta"] = tipo
            # Un mismo N° de operación no puede cobrarse dos veces (p. ej. una captura de Yape reutilizada)
            repetido = (VentaPago.query.join(Venta)
                        .filter(VentaPago.metodo == metodo, VentaPago.nro_operacion == nro, Venta.estado == "completada")
                        .first())
            if repetido or any(x["nro_operacion"] == nro and x["metodo"] == metodo for x in limpios):
                raise ApiError("operacion_repetida",
                               f"El N° de operación {nro} ya fue usado en la venta "
                               f"{repetido.venta.codigo if repetido else 'actual'}.", 409)
        suma += monto
        limpios.append(fila)

    if suma != total:
        raise ApiError("pagos_no_cuadran",
                       f"La suma de los pagos (S/ {suma}) debe ser igual al total de la venta (S/ {total}).", 400)
    return limpios


@bp.post("/ventas")
@jwt_required()
def crear_venta():
    rol = require_permiso("ventas.crear")
    usuario = _usuario_actual()
    # Toda venta se cobra en la caja abierta del cajero (RN: sin caja abierta no se vende).
    caja = exigir_caja_abierta(usuario)
    data = request.get_json(silent=True) or {}
    cfg = current_app.config

    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise ApiError("datos_invalidos", "Debe agregar al menos un producto a la venta.", 400)

    cliente_nombre = (data.get("cliente_nombre") or "").strip()[:180] or None
    cliente_documento = re.sub(r"\D", "", str(data.get("cliente_documento") or "")) or None
    # Toda boleta o factura identifica al cliente: documento y nombre siempre son obligatorios.
    if not cliente_documento:
        raise ApiError("documento_requerido", "Ingrese el DNI, CE o RUC del cliente para emitir el comprobante.", 400)
    if not cliente_nombre:
        raise ApiError("cliente_requerido", "Ingrese el nombre o razón social del cliente.", 400)
    if len(cliente_documento) not in (8, 9, 11):
        raise ApiError("documento_invalido", "El documento debe ser DNI (8 dígitos), CE (9) o RUC (11).", 400)
    igv_rate = Decimal(str(cfg.get("IGV_RATE", 0.18)))

    # Agrupar ítems repetidos del mismo producto
    cantidades = {}
    for it in items:
        try:
            pid = int(it.get("producto_id"))
            cantidad = int(it.get("cantidad") or 0)
        except (TypeError, ValueError):
            raise ApiError("datos_invalidos", "Cada ítem debe incluir producto_id y cantidad numéricos.", 400)
        if cantidad <= 0:
            raise ApiError("cantidad_invalida", "La cantidad de cada ítem debe ser mayor a cero.", 400)
        cantidades[pid] = cantidades.get(pid, 0) + cantidad

    # Bloqueamos los productos afectados para evitar sobreventa concurrente
    productos = Producto.query.filter(Producto.id.in_(cantidades.keys())).with_for_update().all()
    por_id = {p.id: p for p in productos}

    for pid, cantidad in cantidades.items():
        producto = por_id.get(pid)
        if not producto:
            raise ApiError("producto_no_encontrado", f"El producto {pid} no existe.", 404)
        if not producto.activo:
            raise ApiError("producto_inactivo", f"El producto {producto.sku or producto.codigo} no está disponible.", 400)
        if producto.stock < cantidad:
            raise ApiError("stock_insuficiente",
                           f"Stock insuficiente para {producto.nombre}. Disponible: {producto.stock}.", 409)
    # Promociones vigentes en tienda (2x1, % de descuento, precio especial...): la mejor por línea
    cot = promo_svc.cotizar([(por_id[pid], q) for pid, q in cantidades.items()], "tienda")
    lineas = [(it["producto"], it["cantidad"], Decimal(str(it["producto"].precio)), it["neto"], it["descuento"],
               it["promocion"]) for it in cot["items"]]
    bruto = cot["total"].quantize(CENTIMO)

    # Descuento global: hasta DESCUENTO_MAX_CAJERO % lo aplica el cajero; más, con PIN de supervisor.
    descuento = Decimal("0")
    autorizacion = None
    if data.get("descuento") not in (None, "", 0, "0"):
        descuento = _decimal(data["descuento"], "El descuento debe ser numérico.")
        if descuento < 0:
            raise ApiError("descuento_invalido", "El descuento no puede ser negativo.", 400)
        porcentaje = (descuento / bruto * 100) if bruto else Decimal("0")
        if porcentaje > Decimal(str(cfg.get("DESCUENTO_MAX_TOTAL", 30))):
            raise ApiError("descuento_invalido",
                           f"El descuento no puede superar el {cfg.get('DESCUENTO_MAX_TOTAL', 30):g}% de la venta.", 400)
        if porcentaje > Decimal(str(cfg.get("DESCUENTO_MAX_CAJERO", 5))):
            autorizacion = consumir_token(data.get("autorizacion_token"), "DESCUENTO", usuario, monto=descuento)

    # Precios con IGV incluido (Perú): se separa la base imponible.
    total = (bruto - descuento).quantize(CENTIMO)
    base = (total / (Decimal("1") + igv_rate)).quantize(CENTIMO)
    igv = (total - base).quantize(CENTIMO)

    pagos = _leer_pagos(data, total)
    efectivo = next((p for p in pagos if p["metodo"] == "efectivo"), None)

    venta = Venta(
        # Código provisional único: dos cajas pueden registrar ventas a la vez
        # sin chocar con la restricción UNIQUE antes de asignar el correlativo.
        codigo=f"TMP-{uuid4().hex[:16]}", usuario_id=usuario.id, rol_id=rol.id,
        cliente_nombre=cliente_nombre, cliente_documento=cliente_documento,
        metodo_pago=pagos[0]["metodo"] if len(pagos) == 1 else "mixto",
        descuento=descuento, subtotal=base, igv=igv, total=total,
        monto_recibido=efectivo["monto_recibido"] if efectivo else None,
        vuelto=efectivo["vuelto"] if efectivo else None,
        estado="completada", caja_id=caja.id,
        autorizada_por_id=autorizacion.supervisor_id if autorizacion else None,
    )
    db.session.add(venta)
    db.session.flush()
    venta.codigo = f"V-{venta.id:06d}"
    if autorizacion:
        autorizacion.venta_id = venta.id

    for p in pagos:
        db.session.add(VentaPago(venta_id=venta.id, **p))
    for producto, cantidad, precio_unit, sub, desc_linea, promo in lineas:
        db.session.add(VentaDetalle(venta_id=venta.id, producto_id=producto.id, cantidad=cantidad,
                                    precio_unitario=precio_unit, subtotal=sub, descuento=desc_linea,
                                    promocion_id=promo.id if promo else None,
                                    promocion_nombre=promo.nombre if promo else None))
        mover_stock(producto, -cantidad, "venta", usuario, f"Venta {venta.codigo}", venta)

    db.session.commit()

    # Comprobante electrónico: si la API falla, la venta ya está registrada y queda pendiente de reintento.
    try:
        sunat_svc.emitir(venta)
        db.session.commit()
    except Exception as exc:  # no bloquear la venta por un fallo de facturación
        db.session.rollback()
        current_app.logger.error("Comprobante de %s no emitido: %s", venta.codigo, exc)
    return jsonify(venta=venta.to_dict()), 201


@bp.post("/ventas/<int:venta_id>/comprobante")
@jwt_required()
def reintentar_comprobante(venta_id):
    """Reintenta la emisión del comprobante de una venta pendiente o con error."""
    require_permiso("caja.operar")
    venta = db.get_or_404(Venta, venta_id)
    if venta.estado_sunat == "aceptado":
        return jsonify(venta=venta.to_dict())
    sunat_svc.emitir(venta)
    db.session.commit()
    return jsonify(venta=venta.to_dict())


@bp.get("/ventas/<int:venta_id>/comprobante/<formato>")
@jwt_required()
def descargar_comprobante(venta_id, formato):
    """Entrega el PDF o XML guardado localmente (no vuelve a consultar la API)."""
    require_permiso("ventas.ver")
    venta = db.get_or_404(Venta, venta_id)
    ruta = venta.comprobante_pdf if formato == "pdf" else venta.comprobante_xml if formato == "xml" else None
    if not ruta or not Path(ruta).is_file():
        raise ApiError("comprobante_no_disponible", "El comprobante aún no está disponible.", 404)
    return send_file(Path(ruta).resolve(), as_attachment=False)


@bp.post("/ventas/<int:venta_id>/anular")
@jwt_required()
def anular_venta(venta_id):
    """Dos caminos:
    - Supervisor de Caja / Administrador (permiso ventas.anular): anula desde su sesión.
    - Cajero: anula una venta de SU caja abierta con el PIN de un supervisor
      (`autorizacion_token`), sin salir de su sesión y sin hacer esperar a la cola.
    """
    from .auth import _rol_activo
    rol = _rol_activo()
    permisos = {p.codigo for p in rol.permisos} if rol else set()
    if "ventas.anular" in permisos:
        require_permiso("ventas.anular")
        via_cajero = False
    else:
        require_permiso("caja.operar")
        via_cajero = True

    # Se bloquea la fila: si dos supervisores anulan a la vez, el segundo espera
    # y encuentra la venta ya anulada (el stock no se devuelve dos veces).
    venta = Venta.query.filter_by(id=venta_id).with_for_update().first()
    if not venta:
        raise ApiError("venta_no_encontrada", "La venta no existe.", 404)
    if venta.estado == "anulada":
        raise ApiError("venta_anulada", "La venta ya se encuentra anulada.", 409)
    if venta.devoluciones:
        raise ApiError("venta_con_devoluciones",
                       "La venta tiene devoluciones con nota de crédito; registre el resto como otra devolución.", 409)
    # Solo se anula mientras la caja del turno siga abierta: después del arqueo
    # el dinero ya se entregó y el caso se trata como devolución.
    if venta.caja and venta.caja.estado == "cerrada":
        raise ApiError("caja_cerrada",
                       f"La caja {venta.caja.codigo} ya fue cerrada; la venta no puede anularse.", 409)
    data = request.get_json(silent=True) or {}
    motivo = (data.get("motivo") or "").strip()
    if len(motivo) < 10:
        raise ApiError("motivo_requerido", "Indique el motivo de la anulación (mínimo 10 caracteres).", 400)

    usuario = _usuario_actual()
    autorizacion = None
    if via_cajero:
        if not venta.caja or venta.caja.usuario_id != usuario.id:
            raise ApiError("venta_ajena", "Solo puede anular ventas cobradas en su propia caja.", 403)
        autorizacion = consumir_token(data.get("autorizacion_token"), "ANULAR_VENTA", usuario, venta_id=venta.id)

    for detalle in venta.detalles:
        if detalle.producto:
            mover_stock(detalle.producto, detalle.cantidad, "anulacion", usuario,
                        f"Anulación de {venta.codigo}", venta)
    venta.estado = "anulada"
    venta.anulada_por_id = usuario.id
    if autorizacion:
        venta.autorizada_por_id = autorizacion.supervisor_id
    venta.fecha_anulacion = ahora()
    venta.motivo_anulacion = motivo[:240]
    db.session.commit()
    return jsonify(venta=venta.to_dict())


@bp.get("/ventas/reporte/resumen")
@jwt_required()
def reporte_resumen_compat():
    """Compatibilidad con versiones anteriores: delega en /reportes/resumen."""
    from .reportes import resumen
    return resumen()
