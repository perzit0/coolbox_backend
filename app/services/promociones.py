"""Motor de promociones (tienda online y caja).

Tipos de promoción que el administrador puede elegir (como en los carteles de
supermercado, donde una marca auspicia varios de sus productos por un tiempo):

    PORCENTAJE        -X % en cada unidad                       valor = %
    MONTO             S/ X menos en cada unidad                 valor = S/
    PRECIO_ESPECIAL   precio fijo de oferta por producto        precio por producto (o valor)
    NXM               lleva N y paga M (2x1, 3x2, 3x1, 4x3...)  lleva = N, paga = M
    SEGUNDA_UNIDAD    la 2.ª unidad con X % de descuento        valor = %
    VOLUMEN           desde N unidades, -X % en todas           lleva = N, valor = %
    CUPON             código para la tienda online (-X % del total, con compra mínima)

Una promoción se aplica a productos elegidos, a una marca, a una familia o a
todo el catálogo; por canal (todos, solo online o solo en tienda); y solo
entre su fecha de inicio y su fecha de fin. Si a un producto le aplican varias
promociones a la vez, se usa la que más ahorra al cliente (no se acumulan).
"""

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..errors import ApiError

CENT = Decimal("0.01")

TIPOS = {
    "PORCENTAJE": {
        "nombre": "Descuento porcentual",
        "descripcion": "Un porcentaje de descuento en cada unidad (por ejemplo -20 %).",
    },
    "MONTO": {
        "nombre": "Descuento en soles",
        "descripcion": "Un monto fijo menos en cada unidad (por ejemplo S/ 50 menos).",
    },
    "PRECIO_ESPECIAL": {
        "nombre": "Precio especial",
        "descripcion": "Un precio de oferta fijo para cada producto elegido.",
    },
    "NXM": {
        "nombre": "Lleva N y paga M",
        "descripcion": "Promociones 2x1, 3x2, 3x1 o 4x3: el cliente lleva más unidades y paga menos.",
    },
    "SEGUNDA_UNIDAD": {
        "nombre": "Segunda unidad con descuento",
        "descripcion": "La segunda unidad sale con un porcentaje de descuento (por ejemplo al 50 %).",
    },
    "VOLUMEN": {
        "nombre": "Descuento por cantidad",
        "descripcion": "Desde cierta cantidad de unidades, todas tienen un porcentaje de descuento.",
    },
    "CUPON": {
        "nombre": "Cupón de descuento",
        "descripcion": "Un código que el cliente ingresa en el carrito online para un % de descuento sobre su compra.",
    },
}

# Atajos que se muestran como botones al crear la promoción
PRESETS = [
    {"id": "2x1", "nombre": "2x1", "tipo": "NXM", "lleva": 2, "paga": 1, "descripcion": "Lleva 2 y paga 1."},
    {"id": "3x2", "nombre": "3x2", "tipo": "NXM", "lleva": 3, "paga": 2, "descripcion": "Lleva 3 y paga 2."},
    {"id": "3x1", "nombre": "3x1", "tipo": "NXM", "lleva": 3, "paga": 1, "descripcion": "Lleva 3 y paga 1."},
    {"id": "4x3", "nombre": "4x3", "tipo": "NXM", "lleva": 4, "paga": 3, "descripcion": "Lleva 4 y paga 3."},
    {
        "id": "porcentaje",
        "nombre": "Descuento %",
        "tipo": "PORCENTAJE",
        "descripcion": TIPOS["PORCENTAJE"]["descripcion"],
    },
    {"id": "monto", "nombre": "Descuento en S/", "tipo": "MONTO", "descripcion": TIPOS["MONTO"]["descripcion"]},
    {
        "id": "precio",
        "nombre": "Precio especial",
        "tipo": "PRECIO_ESPECIAL",
        "descripcion": TIPOS["PRECIO_ESPECIAL"]["descripcion"],
    },
    {
        "id": "segunda",
        "nombre": "2.ª unidad al X %",
        "tipo": "SEGUNDA_UNIDAD",
        "descripcion": TIPOS["SEGUNDA_UNIDAD"]["descripcion"],
    },
    {"id": "volumen", "nombre": "Desde N unidades", "tipo": "VOLUMEN", "descripcion": TIPOS["VOLUMEN"]["descripcion"]},
    {"id": "cupon", "nombre": "Cupón online", "tipo": "CUPON", "descripcion": TIPOS["CUPON"]["descripcion"]},
]

ALCANCES = ("productos", "marca", "familia", "todo")
CANALES = ("todos", "online", "tienda")


def _d(v):
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


def _num(v):
    return f"{v:g}" if isinstance(v, float) else (f"{float(v):g}" if v is not None else "")


def etiqueta(p):
    """Texto corto para el cartel: '2x1', '-20%', 'S/ 50 menos'..."""
    if p.tipo == "PORCENTAJE":
        return f"-{_num(p.valor)}%"
    if p.tipo == "MONTO":
        return f"S/ {_num(p.valor)} menos"
    if p.tipo == "PRECIO_ESPECIAL":
        return "Precio especial"
    if p.tipo == "NXM":
        return f"{p.lleva}x{p.paga}"
    if p.tipo == "SEGUNDA_UNIDAD":
        return f"2.ª unid. -{_num(p.valor)}%"
    if p.tipo == "VOLUMEN":
        return f"Desde {p.lleva} unid. -{_num(p.valor)}%"
    if p.tipo == "CUPON":
        return f"Cupón {p.codigo_cupon}"
    return p.tipo


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def vigentes(canal, momento=None, incluir_cupones=False):
    """Promociones activas y dentro de su vigencia para el canal ('online' o 'tienda')."""
    from ..models import Promocion, ahora
    from sqlalchemy.orm import selectinload

    momento = momento or ahora()
    q = Promocion.query.options(selectinload(Promocion.productos)).filter(
        Promocion.activa.is_(True),
        Promocion.fecha_inicio <= momento,
        Promocion.fecha_fin > momento,
        Promocion.canal.in_(("todos", canal)),
    )
    if not incluir_cupones:
        q = q.filter(Promocion.tipo != "CUPON")
    return q.all()


def aplica_a(promo, producto):
    if promo.tipo == "CUPON":
        return False
    if promo.alcance == "todo":
        return True
    if promo.alcance == "marca":
        return promo.marca_id is not None and producto.marca_id == promo.marca_id
    if promo.alcance == "familia":
        return promo.familia_id is not None and producto.categoria_id == promo.familia_id
    return any(pp.producto_id == producto.id for pp in promo.productos)


def _precio_especial(promo, producto):
    for pp in promo.productos:
        if pp.producto_id == producto.id and pp.precio_especial is not None:
            return Decimal(str(pp.precio_especial))
    return Decimal(str(promo.valor)) if promo.valor is not None else None


def descuento(promo, producto, cantidad):
    """Ahorro total (en S/) que da la promoción a `cantidad` unidades del producto."""
    precio = Decimal(str(producto.precio))
    q = int(cantidad)
    bruto = precio * q
    v = Decimal(str(promo.valor)) if promo.valor is not None else Decimal("0")
    if promo.tipo == "PORCENTAJE":
        d = bruto * v / 100
    elif promo.tipo == "MONTO":
        d = min(v, precio) * q
    elif promo.tipo == "PRECIO_ESPECIAL":
        esp = _precio_especial(promo, producto)
        d = (precio - esp) * q if esp is not None and esp < precio else Decimal("0")
    elif promo.tipo == "NXM":
        n, m = int(promo.lleva or 0), int(promo.paga or 0)
        d = Decimal((q // n) * (n - m)) * precio if n > m >= 1 else Decimal("0")
    elif promo.tipo == "SEGUNDA_UNIDAD":
        d = Decimal(q // 2) * precio * v / 100
    elif promo.tipo == "VOLUMEN":
        d = bruto * v / 100 if q >= int(promo.lleva or 0) else Decimal("0")
    else:
        d = Decimal("0")
    return max(Decimal("0"), min(_d(d), _d(bruto)))


def mejor(producto, cantidad, promos):
    """(promoción, ahorro) más conveniente para el cliente, o (None, 0)."""
    elegida, ahorro = None, Decimal("0")
    for p in promos:
        if not aplica_a(p, producto):
            continue
        a = descuento(p, producto, cantidad)
        if a > ahorro:
            elegida, ahorro = p, a
    return elegida, ahorro


def promo_destacada(producto, promos):
    """La promoción que se muestra en la tarjeta del producto (aunque requiera varias unidades)."""
    candidatas = [p for p in promos if aplica_a(p, producto)]
    if not candidatas:
        return None

    # Primero la que rebaja desde 1 unidad; si no hay, la que más ahorra llevando las unidades que pide
    def ahorro(p):
        n = max(int(p.lleva or 1), 2 if p.tipo == "SEGUNDA_UNIDAD" else 1)
        return (descuento(p, producto, 1) > 0, descuento(p, producto, n) / n)

    return max(candidatas, key=ahorro)


def info_precio(producto, promos):
    """Datos de precio para la tienda: regular, oferta (por 1 unidad), % y la promoción."""
    regular = _d(producto.precio)
    p = promo_destacada(producto, promos)
    oferta = regular - descuento(p, producto, 1) if p else regular
    pct = (
        int(((regular - oferta) / regular * 100).to_integral_value(ROUND_HALF_UP))
        if regular and oferta < regular
        else 0
    )
    return {
        "precio_regular": float(regular),
        "precio": float(oferta),
        "descuento_pct": pct,
        "promocion": (
            {
                "id": p.id,
                "nombre": p.nombre,
                "tipo": p.tipo,
                "etiqueta": etiqueta(p),
                "patrocinador": p.patrocinador,
                "fecha_fin": _aware(p.fecha_fin).isoformat(),
                "lleva": p.lleva,
                "paga": p.paga,
            }
            if p
            else None
        ),
    }


def cotizar(lineas, canal, cupon=None, momento=None):
    """Precio de un carrito. `lineas` = [(producto, cantidad)]. Devuelve líneas y totales.

    El cupón (solo online) se aplica sobre el total ya rebajado por las promociones.
    """
    promos = vigentes(canal, momento)
    items, subtotal, ahorro_total = [], Decimal("0"), Decimal("0")
    for producto, cantidad in lineas:
        bruto = _d(Decimal(str(producto.precio)) * cantidad)
        promo, ahorro = mejor(producto, cantidad, promos)
        subtotal += bruto
        ahorro_total += ahorro
        items.append(
            {
                "producto": producto,
                "cantidad": cantidad,
                "bruto": bruto,
                "descuento": ahorro,
                "neto": bruto - ahorro,
                "promocion": promo,
            }
        )
    total = subtotal - ahorro_total
    descuento_cupon, cupon_info = Decimal("0"), None
    if cupon:
        cupon_promo = validar_cupon(cupon, total, canal, momento)
        descuento_cupon = _d(total * Decimal(str(cupon_promo.valor)) / 100)
        total -= descuento_cupon
        cupon_info = {
            "codigo": cupon_promo.codigo_cupon,
            "nombre": cupon_promo.nombre,
            "valor": float(cupon_promo.valor),
            "promocion_id": cupon_promo.id,
        }
    return {
        "items": items,
        "subtotal": subtotal,
        "descuento": ahorro_total,
        "descuento_cupon": descuento_cupon,
        "total": total,
        "cupon": cupon_info,
        "promos": promos,
    }


def validar_cupon(codigo, total, canal="online", momento=None):
    from ..models import Promocion, ahora

    momento = momento or ahora()
    codigo = (codigo or "").strip().upper()
    p = Promocion.query.filter_by(codigo_cupon=codigo, tipo="CUPON").first()
    if not p or p.estado(momento) != "vigente" or p.canal not in ("todos", canal):
        raise ApiError("cupon_invalido", "El cupón no existe o ya no está vigente.", 400)
    if p.monto_minimo is not None and total < Decimal(str(p.monto_minimo)):
        raise ApiError(
            "cupon_minimo", f"El cupón {codigo} aplica a compras desde S/ {Decimal(str(p.monto_minimo)):.2f}.", 400
        )
    return p


# ------------------------------------------------------------------ validación


def _dec(v, campo, minimo=None, maximo=None):
    try:
        d = Decimal(str(v)).quantize(CENT)
    except (InvalidOperation, TypeError, ValueError):
        raise ApiError("promocion_invalida", f"Indique {campo}.", 400)
    if minimo is not None and d < minimo or maximo is not None and d > maximo:
        raise ApiError(
            "promocion_invalida", f"{campo[0].upper() + campo[1:]} debe estar entre {minimo} y {maximo}.", 400
        )
    return d


def _fecha(v, campo):
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise ApiError("promocion_invalida", f"La fecha de {campo} no es válida.", 400)
    if dt.tzinfo is None:
        # Fecha y hora de Lima (UTC-5) escritas por el administrador
        from datetime import timedelta

        dt = dt.replace(tzinfo=timezone(timedelta(hours=-5)))
    return dt


def aplicar_datos(promo, data, nueva):
    """Valida `data` y la vuelca en `promo`. Devuelve la lista de (producto_id, precio_especial)."""
    from ..models import Familia, Marca, Producto, Promocion

    tipo = (data.get("tipo") or promo.tipo or "").upper()
    if tipo not in TIPOS:
        raise ApiError("promocion_invalida", "Elija un tipo de promoción válido.", 400)
    nombre = (data.get("nombre") or "").strip()
    if len(nombre) < 3:
        raise ApiError("promocion_invalida", "El nombre de la promoción debe tener al menos 3 caracteres.", 400)
    promo.tipo, promo.nombre = tipo, nombre[:120]
    promo.descripcion = (data.get("descripcion") or "").strip()[:300] or None
    promo.patrocinador = (data.get("patrocinador") or "").strip()[:80] or None
    promo.valor = promo.lleva = promo.paga = promo.monto_minimo = None
    promo.codigo_cupon = None

    if tipo in ("PORCENTAJE", "SEGUNDA_UNIDAD", "VOLUMEN", "CUPON"):
        promo.valor = _dec(data.get("valor"), "el porcentaje", Decimal("1"), Decimal("90"))
    elif tipo == "MONTO":
        promo.valor = _dec(data.get("valor"), "el monto de descuento", Decimal("1"), Decimal("10000"))
    if tipo == "NXM":
        try:
            n, m = int(data.get("lleva")), int(data.get("paga"))
        except (TypeError, ValueError):
            raise ApiError("promocion_invalida", "Indique cuántas unidades lleva y cuántas paga.", 400)
        if not (2 <= n <= 10 and 1 <= m < n):
            raise ApiError("promocion_invalida", "En 'lleva N y paga M', N va de 2 a 10 y M debe ser menor que N.", 400)
        promo.lleva, promo.paga = n, m
    if tipo == "VOLUMEN":
        try:
            n = int(data.get("lleva"))
        except (TypeError, ValueError):
            n = 0
        if not 2 <= n <= 50:
            raise ApiError("promocion_invalida", "Indique desde cuántas unidades aplica (2 a 50).", 400)
        promo.lleva = n
    if tipo == "CUPON":
        codigo = (data.get("codigo_cupon") or "").strip().upper()
        if not codigo or not codigo.replace("-", "").isalnum() or not 4 <= len(codigo) <= 30:
            raise ApiError("promocion_invalida", "El código del cupón debe tener de 4 a 30 letras o números.", 400)
        otro = Promocion.query.filter(Promocion.codigo_cupon == codigo, Promocion.id != (promo.id or 0)).first()
        if otro:
            raise ApiError("promocion_invalida", f"El código {codigo} ya lo usa la promoción '{otro.nombre}'.", 409)
        promo.codigo_cupon = codigo
        if data.get("monto_minimo") not in (None, ""):
            promo.monto_minimo = _dec(data.get("monto_minimo"), "la compra mínima", Decimal("0"), Decimal("100000"))

    canal = data.get("canal") or "todos"
    if canal not in CANALES:
        raise ApiError("promocion_invalida", "Canal no válido.", 400)
    promo.canal = "online" if tipo == "CUPON" else canal

    alcance = "todo" if tipo == "CUPON" else (data.get("alcance") or "productos")
    if alcance not in ALCANCES:
        raise ApiError("promocion_invalida", "Indique a qué productos aplica.", 400)
    if tipo == "PRECIO_ESPECIAL" and alcance != "productos":
        raise ApiError("promocion_invalida", "El precio especial se define producto por producto.", 400)
    promo.alcance = alcance
    promo.marca_id = promo.familia_id = None
    if alcance == "marca":
        marca = Marca.query.get(data.get("marca_id"))
        if not marca:
            raise ApiError("promocion_invalida", "Elija la marca.", 400)
        promo.marca_id = marca.id
    if alcance == "familia":
        familia = Familia.query.get(data.get("familia_id"))
        if not familia:
            raise ApiError("promocion_invalida", "Elija la familia.", 400)
        promo.familia_id = familia.id

    productos = []
    if alcance == "productos":
        for item in data.get("productos") or []:
            pid = item.get("producto_id") if isinstance(item, dict) else item
            prod = Producto.query.get(pid)
            if not prod:
                raise ApiError("promocion_invalida", f"El producto {pid} no existe.", 400)
            esp = None
            if tipo == "PRECIO_ESPECIAL":
                raw = item.get("precio_especial") if isinstance(item, dict) else None
                esp = _dec(raw, f"el precio especial de {prod.sku}", Decimal("0.10"), Decimal(str(prod.precio)) - CENT)
            productos.append((prod.id, esp))
        if not productos:
            raise ApiError("promocion_invalida", "Agregue al menos un producto a la promoción.", 400)

    ini, fin = _fecha(data.get("fecha_inicio"), "inicio"), _fecha(data.get("fecha_fin"), "fin")
    if fin <= ini:
        raise ApiError("promocion_invalida", "La fecha de fin debe ser posterior a la de inicio.", 400)
    if (fin - ini).days > 366:
        raise ApiError("promocion_invalida", "Una promoción puede durar como máximo un año.", 400)
    promo.fecha_inicio, promo.fecha_fin = ini, fin
    promo.destacada = bool(data.get("destacada"))
    if nueva:
        promo.activa = True
    return productos
