"""Seed idempotente que carga catálogo maestro + demo:

- Permisos y roles (catálogo maestro).
- Un usuario administrador demo (admin@coolbox.com.pe / Admin123!).
- Un vendedor demo (correo generado automáticamente).
- 6 categorías y 20 productos reales de la tienda.

Se invoca automáticamente al iniciar la app (ver app/__init__.py) para que
Render + Supabase queden listos sin comandos manuales adicionales.
"""
from datetime import date

from app.extensions import db
from app.models import Categoria, Permiso, Producto, Rol, Usuario
from app.services.email_generator import build_email
from app.services.roles import PERMISOS, ROLES


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
    ("LAP004", "MacBook Air M2 13\" 8GB 256GB Space Gray", "Apple", "Laptops", 4899.00, 4),

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

    ("GAM001", "Consola PlayStation 5 Slim 1TB Disc Edition", "Sony", "Gaming", 2799.00, 7),
    ("GAM002", "Control Xbox Series X Inalámbrico Carbon Black", "Microsoft", "Gaming", 299.00, 20),
]


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
            producto = Producto(codigo=codigo, categoria_id=categoria.id)
            db.session.add(producto)
        producto.nombre = nombre
        producto.marca = marca
        producto.categoria_id = categoria.id
        producto.precio = precio
        # Solo inicializamos stock si el producto es nuevo, para no borrar movimientos
        if producto.id is None or producto.stock == 0:
            producto.stock = stock
        producto.stock_minimo = max(3, stock // 4)
        producto.activo = True
        producto.descripcion = producto.descripcion or f"{marca} · {categoria_nombre} · Disponible en tienda Coolbox."
    db.session.commit()


def _seed_usuarios_demo():
    dominio = "coolbox.com.pe"

    rol_admin = Rol.query.filter_by(codigo="administrador").first()
    rol_vendedor = Rol.query.filter_by(codigo="vendedor").first()
    rol_cajero = Rol.query.filter_by(codigo="cajero").first()

    # Admin demo
    admin = Usuario.query.filter_by(dni="00000001").first()
    if not admin:
        admin = Usuario(
            dni="00000001",
            nombres="Administrador",
            apellido_paterno="Sistema",
            apellido_materno="Coolbox",
            email=f"admin@{dominio}",
            fecha_ingreso=date.today(),
        )
        admin.set_password("Admin123!")
        admin.roles = [rol_admin] if rol_admin else []
        db.session.add(admin)

    # Vendedor demo (correo generado automáticamente)
    vendedor = Usuario.query.filter_by(dni="70123456").first()
    if not vendedor:
        email_vendedor = build_email(
            "Juan Daniel", "Perez", "Lozano", dominio,
            existe_email=lambda e: Usuario.query.filter_by(email=e).first() is not None,
        )
        vendedor = Usuario(
            dni="70123456",
            nombres="Juan Daniel",
            apellido_paterno="Perez",
            apellido_materno="Lozano",
            email=email_vendedor,
            telefono="987654321",
            fecha_ingreso=date.today(),
        )
        vendedor.set_password("Vendedor123!")
        vendedor.roles = [r for r in (rol_vendedor, rol_cajero) if r]
        db.session.add(vendedor)

    db.session.commit()


def seed_catalogo_maestro():
    """Punto de entrada llamado al iniciar la app."""
    _seed_permisos()
    _seed_roles()
    _seed_categorias_productos()
    _seed_usuarios_demo()


if __name__ == "__main__":
    from app import create_app

    app = create_app()
    with app.app_context():
        db.create_all()
        seed_catalogo_maestro()
        print("Catálogo maestro y datos demo cargados correctamente.")
