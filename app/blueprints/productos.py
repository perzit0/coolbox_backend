"""Catálogo: familias, subfamilias, marcas, productos (con SKU) y kardex."""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Familia, Marca, MovimientoStock, Producto, Subfamilia
from ..services.catalogo import siguiente_sku
from ..services.inventario import mover_stock
from .auth import _usuario_actual, require_permiso

bp = Blueprint("productos", __name__)

# Imagen subida en base64: se limita para no inflar la BD (el frontend la reduce a ~600px).
MAX_IMAGEN_CHARS = 1_500_000


def _validar_imagen(valor):
    """Acepta una URL http(s) o una data URL de imagen. Vacío = sin imagen."""
    valor = (valor or "").strip()
    if not valor:
        return None
    if valor.startswith(("http://", "https://")):
        if len(valor) > 2000:
            raise ApiError("imagen_invalida", "La URL de la imagen es demasiado larga.", 400)
        return valor
    if valor.startswith("data:image/") and ";base64," in valor:
        if len(valor) > MAX_IMAGEN_CHARS:
            raise ApiError("imagen_muy_grande", "La imagen es demasiado pesada (máximo aprox. 1 MB).", 400)
        return valor
    raise ApiError("imagen_invalida", "La imagen debe ser una URL http(s) o un archivo de imagen.", 400)


def _numero(data, campo, tipo, minimo=0, etiqueta=None):
    try:
        valor = tipo(data[campo])
    except (TypeError, ValueError, KeyError):
        raise ApiError("datos_invalidos", f"El campo '{etiqueta or campo}' debe ser numérico.", 400)
    if valor < minimo:
        raise ApiError("valor_invalido", f"El campo '{etiqueta or campo}' no puede ser menor que {minimo}.", 400)
    return valor


def _subfamilia_y_marca(subfamilia_id, marca_id):
    subfamilia = db.session.get(Subfamilia, int(subfamilia_id)) if subfamilia_id else None
    marca = db.session.get(Marca, int(marca_id)) if marca_id else None
    if not subfamilia:
        raise ApiError("subfamilia_invalida", "Seleccione una subfamilia válida.", 400)
    if not marca:
        raise ApiError("marca_invalida", "Seleccione una marca válida.", 400)
    return subfamilia, marca


def _calcular_sku(subfamilia, marca):
    familia = subfamilia.familia
    prefijo = f"{familia.codigo}{subfamilia.codigo}{marca.codigo}"
    existentes = [s for (s,) in db.session.query(Producto.sku).filter(Producto.sku.like(f"{prefijo}%")).all()]
    try:
        return siguiente_sku(familia, subfamilia, marca, existentes)
    except ValueError as exc:
        raise ApiError("sku_agotado", str(exc), 409)


# ---------------------------------------------------------------- catálogo base

@bp.get("/familias")
@jwt_required()
def listar_familias():
    familias = Familia.query.order_by(Familia.codigo.asc(), Familia.nombre.asc()).all()
    return jsonify(familias=[f.to_dict(include_subfamilias=True) for f in familias])


@bp.get("/categorias")
@jwt_required()
def listar_categorias():
    """Compatibilidad: las categorías ahora son las familias del SKU."""
    familias = Familia.query.order_by(Familia.codigo.asc(), Familia.nombre.asc()).all()
    return jsonify(categorias=[f.to_dict() for f in familias])


@bp.get("/marcas")
@jwt_required()
def listar_marcas():
    marcas = Marca.query.order_by(Marca.nombre.asc()).all()
    return jsonify(marcas=[m.to_dict() for m in marcas])


@bp.post("/marcas")
@jwt_required()
def crear_marca():
    """Registra una marca nueva con el siguiente código libre (01-99)."""
    require_permiso("productos.crear")
    nombre = ((request.get_json(silent=True) or {}).get("nombre") or "").strip()
    if not nombre:
        raise ApiError("datos_invalidos", "Ingrese el nombre de la marca.", 400)
    if Marca.query.filter(db.func.lower(Marca.nombre) == nombre.lower()).first():
        raise ApiError("marca_duplicada", f"La marca {nombre} ya existe.", 409)
    usados = {int(c) for (c,) in db.session.query(Marca.codigo).all() if c and c.isdigit()}
    libre = next((n for n in range(1, 100) if n not in usados), None)
    if libre is None:
        raise ApiError("marcas_agotadas", "Se alcanzó el máximo de 99 marcas.", 409)
    marca = Marca(codigo=f"{libre:02d}", nombre=nombre)
    db.session.add(marca)
    db.session.commit()
    return jsonify(marca=marca.to_dict()), 201


@bp.get("/productos/sku-preview")
@jwt_required()
def sku_preview():
    """SKU que se asignaría con la subfamilia y marca elegidas (antes de guardar)."""
    require_permiso("productos.crear")
    subfamilia, marca = _subfamilia_y_marca(request.args.get("subfamilia_id"), request.args.get("marca_id"))
    return jsonify(sku=_calcular_sku(subfamilia, marca))


# ---------------------------------------------------------------- productos

@bp.get("/productos")
@jwt_required()
def listar_productos():
    require_permiso("productos.ver")
    q = (request.args.get("q") or "").strip()
    familia_id = request.args.get("familia_id", type=int) or request.args.get("categoria_id", type=int)
    subfamilia_id = request.args.get("subfamilia_id", type=int)
    marca_id = request.args.get("marca_id", type=int)
    estado_stock = request.args.get("estado_stock")  # bajo | agotado
    solo_activos = request.args.get("solo_activos", "1") == "1"

    query = Producto.query.outerjoin(Marca)
    if solo_activos:
        query = query.filter(Producto.activo.is_(True))
    if familia_id:
        query = query.filter(Producto.categoria_id == familia_id)
    if subfamilia_id:
        query = query.filter(Producto.subfamilia_id == subfamilia_id)
    if marca_id:
        query = query.filter(Producto.marca_id == marca_id)
    if estado_stock == "agotado":
        query = query.filter(Producto.stock <= 0)
    elif estado_stock == "bajo":
        query = query.filter(Producto.stock <= Producto.stock_minimo)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Producto.nombre.ilike(like),
            Producto.codigo.ilike(like),
            Producto.sku.ilike(like),
            Marca.nombre.ilike(like),
        ))
    productos = query.order_by(Producto.sku.asc(), Producto.nombre.asc()).all()
    return jsonify(productos=[p.to_dict() for p in productos])


@bp.get("/productos/<int:producto_id>")
@jwt_required()
def obtener_producto(producto_id):
    require_permiso("productos.ver")
    producto = db.get_or_404(Producto, producto_id)
    return jsonify(producto=producto.to_dict())


@bp.post("/productos")
@jwt_required()
def crear_producto():
    require_permiso("productos.crear")
    data = request.get_json(silent=True) or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        raise ApiError("datos_invalidos", "El nombre del producto es obligatorio.", 400)
    if len(nombre) > 180:
        raise ApiError("datos_invalidos", "El nombre no puede superar 180 caracteres.", 400)
    if data.get("precio") in (None, ""):
        raise ApiError("datos_invalidos", "El precio es obligatorio.", 400)

    subfamilia, marca = _subfamilia_y_marca(data.get("subfamilia_id"), data.get("marca_id"))
    sku = _calcular_sku(subfamilia, marca)

    # Código del fabricante: opcional; si no se indica se usa el SKU.
    codigo = (data.get("codigo") or "").strip().upper()[:30] or sku
    if Producto.query.filter_by(codigo=codigo).first():
        raise ApiError("codigo_duplicado", f"Ya existe un producto con el código de referencia {codigo}.", 409)

    data.setdefault("stock", 0)
    data.setdefault("stock_minimo", 5)
    producto = Producto(
        sku=sku,
        codigo=codigo,
        nombre=nombre,
        descripcion=(data.get("descripcion") or "").strip()[:500] or None,
        categoria_id=subfamilia.familia_id,
        subfamilia_id=subfamilia.id,
        marca_id=marca.id,
        precio=round(_numero(data, "precio", float, 0.01, "precio"), 2),
        stock=0,
        stock_minimo=_numero(data, "stock_minimo", int, 0, "stock mínimo"),
        activo=bool(data.get("activo", True)),
        imagen_url=_validar_imagen(data.get("imagen_url")),
    )
    db.session.add(producto)
    db.session.flush()
    stock_inicial = _numero(data, "stock", int, 0, "stock inicial")
    if stock_inicial:
        mover_stock(producto, stock_inicial, "inicial", _usuario_actual(), "Stock inicial al registrar el producto")
    db.session.commit()
    return jsonify(producto=producto.to_dict()), 201


@bp.patch("/productos/<int:producto_id>")
@jwt_required()
def editar_producto(producto_id):
    """Edita datos comerciales. El SKU, la subfamilia y la marca no cambian
    (el SKU es inmutable) y el stock solo se modifica desde el kardex."""
    require_permiso("productos.editar")
    producto = db.get_or_404(Producto, producto_id)
    data = request.get_json(silent=True) or {}

    if "nombre" in data:
        nombre = (data["nombre"] or "").strip()
        if not nombre:
            raise ApiError("datos_invalidos", "El nombre del producto es obligatorio.", 400)
        producto.nombre = nombre[:180]
    if "descripcion" in data:
        producto.descripcion = (data["descripcion"] or "").strip()[:500] or None
    if "codigo" in data:
        codigo = (data["codigo"] or "").strip().upper()[:30]
        if codigo and codigo != producto.codigo:
            if Producto.query.filter(Producto.codigo == codigo, Producto.id != producto.id).first():
                raise ApiError("codigo_duplicado", f"Ya existe un producto con el código {codigo}.", 409)
            producto.codigo = codigo
    if "precio" in data:
        producto.precio = round(_numero(data, "precio", float, 0.01, "precio"), 2)
    if "stock_minimo" in data:
        producto.stock_minimo = _numero(data, "stock_minimo", int, 0, "stock mínimo")
    if "activo" in data:
        producto.activo = bool(data["activo"])
    if "imagen_url" in data:
        producto.imagen_url = _validar_imagen(data["imagen_url"])

    db.session.commit()
    return jsonify(producto=producto.to_dict())


# ---------------------------------------------------------------- stock / kardex

@bp.post("/productos/<int:producto_id>/stock")
@jwt_required()
def actualizar_stock(producto_id):
    """Registra un movimiento de almacén.

    Body: {"tipo": "entrada"|"salida"|"ajuste", "cantidad": int, "motivo": str}
      - entrada: suma la cantidad (recepción de mercadería)
      - salida:  resta la cantidad (merma, traslado, garantía...)
      - ajuste:  fija el stock al valor de `cantidad` (conteo físico)
    Compatibilidad: también acepta {"delta": n} o {"stock": n}.
    """
    require_permiso("stock.actualizar")
    producto = db.get_or_404(Producto, producto_id)
    data = request.get_json(silent=True) or {}
    usuario = _usuario_actual()

    tipo = data.get("tipo")
    if not tipo:  # formato anterior
        if data.get("stock") is not None:
            tipo, data["cantidad"] = "ajuste", data["stock"]
        elif data.get("delta") is not None:
            delta = int(data["delta"])
            tipo, data["cantidad"] = ("entrada", delta) if delta >= 0 else ("salida", -delta)
    if tipo not in ("entrada", "salida", "ajuste"):
        raise ApiError("tipo_invalido", "El tipo debe ser entrada, salida o ajuste.", 400)

    motivo = (data.get("motivo") or "").strip()
    if tipo in ("salida", "ajuste") and len(motivo) < 5:
        raise ApiError("motivo_requerido", "Indique el motivo del movimiento (mínimo 5 caracteres).", 400)

    if tipo == "ajuste":
        nuevo = _numero(data, "cantidad", int, 0, "stock contado")
        diferencia = nuevo - producto.stock
        if diferencia == 0:
            raise ApiError("sin_cambios", "El stock contado es igual al stock del sistema.", 400)
        mover_stock(producto, diferencia, "ajuste", usuario, motivo)
    else:
        cantidad = _numero(data, "cantidad", int, 1, "cantidad")
        mover_stock(producto, cantidad if tipo == "entrada" else -cantidad, tipo, usuario,
                    motivo or ("Recepción de mercadería" if tipo == "entrada" else None))
    db.session.commit()
    return jsonify(producto=producto.to_dict())


@bp.get("/productos/<int:producto_id>/movimientos")
@jwt_required()
def movimientos_producto(producto_id):
    require_permiso("kardex.ver")
    producto = db.get_or_404(Producto, producto_id)
    movimientos = (MovimientoStock.query.filter_by(producto_id=producto.id)
                   .order_by(MovimientoStock.fecha.desc(), MovimientoStock.id.desc()).limit(300).all())
    return jsonify(producto=producto.to_dict(), movimientos=[m.to_dict() for m in movimientos])


@bp.get("/movimientos")
@jwt_required()
def movimientos_recientes():
    require_permiso("kardex.ver")
    tipo = request.args.get("tipo")
    limite = min(request.args.get("limit", 100, type=int), 500)
    query = MovimientoStock.query
    if tipo in MovimientoStock.TIPOS:
        query = query.filter(MovimientoStock.tipo == tipo)
    movimientos = query.order_by(MovimientoStock.fecha.desc(), MovimientoStock.id.desc()).limit(limite).all()
    return jsonify(movimientos=[m.to_dict() for m in movimientos])
