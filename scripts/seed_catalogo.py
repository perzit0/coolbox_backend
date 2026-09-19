"""Seed idempotente que carga catálogo maestro + demo:

- Permisos y roles (catálogo maestro). Retira roles obsoletos (Cajero).
- Usuarios demo: administrador, vendedor y supervisor de ventas.
- 6 categorías y 20 productos reales de la tienda, algunos con foto.

Se invoca automáticamente al iniciar la app (ver app/__init__.py) para que
Render + Supabase queden listos sin comandos manuales adicionales.
"""
from datetime import date
from urllib.parse import quote

from app.extensions import db
from app.models import Categoria, Permiso, Producto, Rol, Usuario, Venta
from app.services.email_generator import build_email
from app.services.roles import PERMISOS, ROLES, ROLES_OBSOLETOS


# --- Catálogo de productos reales de Coolbox (referencia: tienda física) ---
CATEGORIAS = [
    ("Laptops", "Computadoras portátiles para uso personal y profesional"),
    ("Celulares", "Smartphones y teléfonos móviles"),
    ("Audio", "Audífonos, parlantes y equipos de sonido"),
    ("Televisores", "Smart TVs y televisores LED"),
    ("Accesorios", "Accesorios de cómputo y telefonía"),
    ("Gaming", "Consolas, videojuegos y periféricos gamer"),
]

# 20 productos con datos verosímiles del mercado peruano (precios en soles)
PRODUCTOS = [
    ("LAP001", "Laptop HP 15-fc0009la Ryzen 5 7520U 8GB 512GB SSD 15.6\"", "HP", "Laptops", 2199.00, 12),
    ("LAP002", "Laptop Lenovo IdeaPad 1 Ryzen 3 7320U 8GB 256GB SSD 14\"", "Lenovo", "Laptops", 1499.00, 15),
    ("LAP003", "Laptop ASUS Vivobook 15 Intel Core i5-1235U 16GB 512GB SSD", "ASUS", "Laptops", 2699.00, 8),
    ("LAP004", "MacBook Air M2 13\" 8GB 256GB Midnight", "Apple", "Laptops", 4899.00, 4),

    ("CEL001", "Samsung Galaxy A15 128GB 6GB RAM Dual SIM", "Samsung", "Celulares", 699.00, 25),
    ("CEL002", "Xiaomi Redmi Note 13 256GB 8GB RAM", "Xiaomi", "Celulares", 899.00, 20),
    ("CEL003", "iPhone 15 128GB Azul", "Apple", "Celulares", 3999.00, 6),
    ("CEL004", "Motorola Moto G54 5G 256GB 8GB RAM", "Motorola", "Celulares", 999.00, 18),
    ("CEL005", "Huawei Nova 12i 128GB 8GB RAM", "Huawei", "Celulares", 1099.00, 10),

    ("AUD001", "Audífonos Sony WH-CH520 Bluetooth Inalámbricos", "Sony", "Audio", 249.00, 30),
    ("AUD002", "Apple AirPods Pro 2da Gen con Estuche USB-C", "Apple", "Audio", 999.00, 12),
    ("AUD003", "Parlante JBL Flip 6 Bluetooth Portátil", "JBL", "Audio", 549.00, 22),
    ("AUD004", "Audífonos Redmi Buds 4 Active TWS", "Xiaomi", "Audio", 129.00, 40),

    ("TV001", "Smart TV Samsung 55\" 4K UHD Crystal AU7000", "Samsung", "Televisores", 1699.00, 9),
    ("TV002", "Smart TV LG 43\" 4K UHD ThinQ AI UR7300", "LG", "Televisores", 1299.00, 11),
    ("TV003", "Smart TV TCL 65\" 4K QLED Google TV C645", "TCL", "Televisores", 2299.00, 5),

    ("ACC001", "Mouse Logitech M170 Inalámbrico", "Logitech", "Accesorios", 49.00, 60),
    ("ACC002", "Teclado Mecánico Redragon Kumara K552 Rojo", "Redragon", "Accesorios", 189.00, 25),

    ("GAM001", "Consola PlayStation 5 Disc Edition 825GB + DualSense", "Sony", "Gaming", 2799.00, 7),
    ("GAM002", "Control Xbox Series X Inalámbrico Carbon Black", "Microsoft", "Gaming", 299.00, 20),
]


# Fotos de libre uso (Wikimedia Commons) para los modelos que tienen imagen
# publicada. El resto muestra una ilustración de su categoría hasta que el
# administrador suba la foto desde "Editar producto".
IMAGENES_COMMONS = {
    "LAP004": "M2_Macbook_Air_Midnight_model_-_1.jpg",
    "CEL003": "Apple_iPhone_15.jpg",
    "CEL004": "Moto_G54_5G.jpg",
    "AUD002": "AirPods_Pro_(2nd_generation).jpg",
    "GAM001": "PlayStation_5_and_DualSense.jpg",
    "GAM002": "Xbox_Series_Controller_Carbon_Black.jpg",
}

# Nombres anteriores del seed: si el producto conserva el nombre antiguo (no
# lo editó un administrador) se actualiza al nuevo para que coincida con la foto.
NOMBRES_ANTERIORES = {
    "LAP004": 'MacBook Air M2 13" 8GB 256GB Space Gray',
    "GAM001": "Consola PlayStation 5 Slim 1TB Disc Edition",
}


def _url_commons(archivo):
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(archivo)}?width=480"


def _seed_permisos():
    for codigo, descripcion in PERMISOS:
        existente = Permiso.query.filter_by(codigo=codigo).first()
        if not existente:
            db.session.add(Permiso(codigo=codigo, descripcion=descripcion))
        else:
            existente.descripcion = descripcion
    db.session.commit()


def _seed_roles():
    permisos_por_codigo = {p.codigo: p for p in Permiso.query.all()}
    for datos in ROLES:
        rol = Rol.query.filter_by(codigo=datos["codigo"]).first()
        if not rol:
            rol = Rol(codigo=datos["codigo"])
            db.session.add(rol)
        rol.nombre = datos["nombre"]
        rol.descripcion = datos["descripcion"]
        rol.es_admin = datos["es_admin"]
        rol.permisos = [permisos_por_codigo[c] for c in datos["permisos"] if c in permisos_por_codigo]
    db.session.commit()


def _retirar_roles_obsoletos():
    """Elimina roles retirados (p. ej. Cajero) sin perder usuarios ni ventas."""
    for codigo_obsoleto, codigo_reemplazo in ROLES_OBSOLETOS.items():
        viejo = Rol.query.filter_by(codigo=codigo_obsoleto).first()
        if not viejo:
            continue
        nuevo = Rol.query.filter_by(codigo=codigo_reemplazo).first()
        for usuario in list(viejo.usuarios):
            if nuevo and nuevo not in usuario.roles:
                usuario.roles.append(nuevo)
            usuario.roles.remove(viejo)
        if nuevo:
            Venta.query.filter_by(rol_id=viejo.id).update({"rol_id": nuevo.id})
        viejo.permisos = []
        db.session.flush()
        db.session.delete(viejo)
    db.session.commit()


def _seed_categorias_productos():
    cat_por_nombre = {}
    for nombre, descripcion in CATEGORIAS:
        categoria = Categoria.query.filter_by(nombre=nombre).first()
        if not categoria:
            categoria = Categoria(nombre=nombre, descripcion=descripcion)
            db.session.add(categoria)
        else:
            categoria.descripcion = descripcion
        cat_por_nombre[nombre] = categoria
    db.session.commit()

    for codigo, nombre, marca, categoria_nombre, precio, stock in PRODUCTOS:
        producto = Producto.query.filter_by(codigo=codigo).first()
        categoria = cat_por_nombre.get(categoria_nombre)
        if not producto:
            # Solo se inicializan los datos al crearlo: las ediciones del
            # administrador (precio, stock, nombre...) no se sobrescriben.
            producto = Producto(
                codigo=codigo,
                nombre=nombre,
                marca=marca,
                categoria_id=categoria.id,
                precio=precio,
                stock=stock,
                stock_minimo=max(3, stock // 4),
                activo=True,
                descripcion=f"{marca} · {categoria_nombre} · Disponible en tienda Coolbox.",
            )
            db.session.add(producto)
        elif producto.nombre == NOMBRES_ANTERIORES.get(codigo):
            producto.nombre = nombre
        if not producto.imagen_url and codigo in IMAGENES_COMMONS:
            producto.imagen_url = _url_commons(IMAGENES_COMMONS[codigo])
    db.session.commit()


def _crear_usuario_demo(dni, nombres, ap_paterno, ap_materno, password, roles, email=None):
    """Crea el usuario solo si no existe (no toca usuarios ya registrados)."""
    if Usuario.query.filter_by(dni=dni).first():
        return
    email = email or build_email(
        nombres, ap_paterno, ap_materno, "coolbox.com.pe",
        existe_email=lambda e: Usuario.query.filter_by(email=e).first() is not None,
    )
    usuario = Usuario(
        dni=dni,
        nombres=nombres,
        apellido_paterno=ap_paterno,
        apellido_materno=ap_materno,
        email=email,
        fecha_ingreso=date.today(),
    )
    usuario.set_password(password)
    usuario.roles = [r for r in roles if r]
    db.session.add(usuario)
    db.session.flush()


def _seed_usuarios_demo():
    rol = {r.codigo: r for r in Rol.query.all()}

    # Administrador demo (admin@coolbox.com.pe / Admin123!)
    _crear_usuario_demo("00000001", "Administrador", "Sistema", "Coolbox", "Admin123!",
                        [rol.get("administrador")], email="admin@coolbox.com.pe")
    # Vendedor demo con dos roles: al ingresar debe elegir con cuál trabajar
    # (jperezl@coolbox.com.pe / Vendedor123!)
    _crear_usuario_demo("70123456", "Juan Daniel", "Perez", "Lozano", "Vendedor123!",
                        [rol.get("vendedor"), rol.get("almacenero")])
    # Supervisor de ventas demo: único rol de tienda que anula ventas
    # (mtorresr@coolbox.com.pe / Supervisor123!)
    _crear_usuario_demo("70987654", "Maria Elena", "Torres", "Rojas", "Supervisor123!",
                        [rol.get("supervisor")])
    db.session.commit()


def seed_catalogo_maestro():
    """Punto de entrada llamado al iniciar la app."""
    _seed_permisos()
    _seed_roles()
    _retirar_roles_obsoletos()
    _seed_categorias_productos()
    _seed_usuarios_demo()


if __name__ == "__main__":
    from app import create_app

    app = create_app()
    with app.app_context():
        db.create_all()
        seed_catalogo_maestro()
        print("Catálogo maestro y datos demo cargados correctamente.")
