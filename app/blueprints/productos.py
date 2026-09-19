from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Categoria, Producto
from .auth import require_permiso

bp = Blueprint("productos", __name__)


@bp.get("/categorias")
@jwt_required()
def listar_categorias():
    categorias = Categoria.query.order_by(Categoria.nombre.asc()).all()
    return jsonify(categorias=[c.to_dict() for c in categorias])


@bp.get("/productos")
@jwt_required()
def listar_productos():
    require_permiso("productos.ver")
    q = (request.args.get("q") or "").strip().lower()
    categoria_id = request.args.get("categoria_id", type=int)
    solo_activos = request.args.get("solo_activos", "1") == "1"

    query = Producto.query
    if solo_activos:
        query = query.filter(Producto.activo.is_(True))
    if categoria_id:
        query = query.filter(Producto.categoria_id == categoria_id)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Producto.nombre.ilike(like),
                Producto.codigo.ilike(like),
                Producto.marca.ilike(like),
            )
        )
    productos = query.order_by(Producto.nombre.asc()).all()
    return jsonify(productos=[p.to_dict() for p in productos])


@bp.get("/productos/<int:producto_id>")
@jwt_required()
def obtener_producto(producto_id):
    require_permiso("productos.ver")
    producto = Producto.query.get_or_404(producto_id)
    return jsonify(producto=producto.to_dict())


@bp.post("/productos")
@jwt_required()
def crear_producto():
    require_permiso("productos.crear")
    data = request.get_json(silent=True) or {}
    codigo = (data.get("codigo") or "").strip().upper()
    nombre = (data.get("nombre") or "").strip()
    categoria_id = data.get("categoria_id")
    precio = data.get("precio")

    if not codigo or not nombre or not categoria_id or precio is None:
        raise ApiError("datos_invalidos", "Código, nombre, categoría y precio son obligatorios.", 400)
    if Producto.query.filter_by(codigo=codigo).first():
        raise ApiError("codigo_duplicado", f"Ya existe un producto con el código {codigo}.", 409)
    if not Categoria.query.get(int(categoria_id)):
        raise ApiError("categoria_invalida", "La categoría indicada no existe.", 400)

    producto = Producto(
        codigo=codigo,
        nombre=nombre,
        descripcion=(data.get("descripcion") or "").strip() or None,
        marca=(data.get("marca") or "").strip() or None,
        categoria_id=int(categoria_id),
        precio=round(float(precio), 2),
        stock=int(data.get("stock") or 0),
        stock_minimo=int(data.get("stock_minimo") or 5),
        activo=bool(data.get("activo", True)),
    )
    db.session.add(producto)
    db.session.commit()
    return jsonify(producto=producto.to_dict()), 201


@bp.patch("/productos/<int:producto_id>")
@jwt_required()
def editar_producto(producto_id):
    require_permiso("productos.editar")
    producto = Producto.query.get_or_404(producto_id)
    data = request.get_json(silent=True) or {}

    for campo in ("nombre", "descripcion", "marca"):
        if campo in data:
            valor = (data[campo] or "").strip()
            setattr(producto, campo, valor or None if campo != "nombre" else valor)
    if "precio" in data:
        producto.precio = round(float(data["precio"]), 2)
    if "categoria_id" in data:
        if not Categoria.query.get(int(data["categoria_id"])):
            raise ApiError("categoria_invalida", "La categoría indicada no existe.", 400)
        producto.categoria_id = int(data["categoria_id"])
    if "stock" in data:
        producto.stock = int(data["stock"])
    if "stock_minimo" in data:
        producto.stock_minimo = int(data["stock_minimo"])
    if "activo" in data:
        producto.activo = bool(data["activo"])

    db.session.commit()
    return jsonify(producto=producto.to_dict())


@bp.post("/productos/<int:producto_id>/stock")
@jwt_required()
def actualizar_stock(producto_id):
    require_permiso("stock.actualizar")
    producto = Producto.query.get_or_404(producto_id)
    data = request.get_json(silent=True) or {}
    delta = data.get("delta")
    absoluto = data.get("stock")
    if delta is None and absoluto is None:
        raise ApiError("datos_invalidos", "Envíe 'delta' (suma/resta) o 'stock' (valor absoluto).", 400)
    if absoluto is not None:
        producto.stock = max(0, int(absoluto))
    else:
        nuevo = producto.stock + int(delta)
        if nuevo < 0:
            raise ApiError("stock_insuficiente", "El stock no puede quedar por debajo de cero.", 400)
        producto.stock = nuevo
    db.session.commit()
    return jsonify(producto=producto.to_dict())
