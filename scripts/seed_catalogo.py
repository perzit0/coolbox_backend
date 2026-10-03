"""Seed idempotente que carga catálogo maestro + demo:

- Permisos y roles (catálogo maestro). Retira roles obsoletos (Cajero).
- Familias, subfamilias y marcas (estructura del SKU de 8 dígitos).
- 37 productos reales de coolbox.pe (nombre, referencia, precio e imagen
  oficial publicados en la tienda en línea de Coolbox, octubre 2026).
- Migra los 20 productos de la versión anterior (LAP001, CEL001...) a su
  equivalente real, conservando su stock y su historial de ventas.
- Asigna SKU a cualquier producto que aún no lo tenga.
- Usuarios demo: administrador, vendedor/almacenero y supervisor de ventas.

Se invoca automáticamente al iniciar la app (ver app/__init__.py) para que
Render + Supabase queden listos sin comandos manuales adicionales.
"""
from datetime import date

from app.extensions import db
from app.models import Familia, Marca, MovimientoStock, Permiso, Producto, Rol, Subfamilia, Usuario, Venta
from app.services.catalogo import FAMILIAS, FAMILIAS_ANTERIORES, MARCAS, siguiente_sku
from app.services.email_generator import build_email
from app.services.roles import PERMISOS, ROLES, ROLES_OBSOLETOS

IMG = "https://coolboxpe.vteximg.com.br/arquivos/ids/"

# (familia, subfamilia, marca, codigo_referencia, nombre, precio, stock, imagen)
# Nombres, referencias, precios e imágenes tomados de coolbox.pe.
PRODUCTOS = [
    # ---------------- 10 Laptops
    ("10", "01", "HP", "HP15FC0025-T",
     'Laptop HP 15-fc0025wm 15.6", AMD Ryzen 5, 1TB SSD, 8GB RAM, Windows 11 Home, plateado',
     2279.00, 12, "565133/HP15FC0025-T-5.jpg?v=639226779244500000"),
    ("10", "01", "Lenovo", "82YU00X5LMPY",
     'Laptop Lenovo V15 G4 AMN 15.6", AMD Athlon 7120U, 256GB SSD, 8GB RAM, FreeDos - sin Sistema Operativo, gris',
     1499.00, 15, "561574/82YU00X5LMPY-5.jpg?v=639214836126030000"),
    ("10", "01", "Lenovo", "RSC-LEN342LAP",
     'Laptop Lenovo IdeaPad Slim 3 15IRH10 15.3" CI5 13420H, 512GB SSD, 8GB RAM, FreeDos - sin sistema operativo, gris',
     2139.00, 9, "532257/Laptopsdasdas.jpg?v=639081666498330000"),
    ("10", "01", "Asus", "PS29857020",
     'Laptop Asus PM1503CDA-S70514 15.6", AMD Ryzen 5-150, 512GB SSD, 16GB RAM, Windows 11 Home, gris',
     2789.00, 8, "573823/PS29857020-5.jpg?v=639247434636170000"),
    ("10", "02", "HP", "NBHPBT4F6LA",
     'Laptop gamer HP Victus 15-fb3019la, 15.6", Ryzen 7 7445HS, SSD 512GB, 8GB RAM, RTX 3050, FreeDos - sin Sistema Operativo, plata',
     2799.00, 6, "571023/NBHPBT4F6LA-f2.jpg?v=639240533098470000"),
    ("10", "03", "Apple", "PS41703571",
     'MacBook Neo 13" Chip A18 Pro, 256GB SSD, 8GB RAM, macOS, teclado inglés, índigo',
     3309.00, 4, "553897/MacBook-Neo-13-A18-Pro-256GB-azul-medianoche-frontal.jpg?v=639186208298830000"),

    # ---------------- 20 Celulares
    ("20", "01", "Samsung", "SM-A075MZKALTP",
     'Celular Samsung Galaxy A07 64GB, 4GB RAM, cámara trasera 50MP y frontal 8MP, 6.7", negro',
     599.00, 30, "507474/celular-samsung-galaxy-a07-64gb-4gb-ram-50mp-negro.jpg?v=639123120423730000"),
    ("20", "01", "Motorola", "PB970058PE",
     'Celular Motorola G06 4G 256GB, 4GB RAM, cámara trasera 50MP y frontal 8MP, 6.8", azul',
     529.00, 22, "525561/celular-motorola-g06-4g-256gb-4gb-azul-pb970058pe_2.jpg?v=639080801549830000"),
    ("20", "02", "Samsung", "SM-A175FZKKLTP",
     "Celular Samsung Galaxy A17 4G, 256GB, 8GB RAM, cámara trasera 50MP y frontal 13MP, 6.7'', negro",
     1049.00, 25, "508674/samsung-galaxy-a17-4g-256gb-8gb-ram-67-negro.jpg?v=639123128688870000"),
    ("20", "02", "Xiaomi", "72149",
     'Celular Xiaomi Redmi Note 15 4G 256GB, 8GB RAM, cámara principal 108MP + 2MP, frontal 20MP, 6.7", negro',
     899.00, 20, "518578/Celular-xiaomi-redmi-note-15-4g-256gb-8gb-ram_1.jpg?v=639041792613230000"),
    ("20", "03", "Apple", "MTP03BE-A",
     'iPhone 15 5G 128GB, 6GB RAM, cámara trasera 48MP y frontal 12MP, 6.1", negro',
     2599.00, 8, "498753/iPhone-15-negro_1.jpg?v=639123015052770000"),
    ("20", "03", "Apple", "MG6M4BE-A",
     'iPhone 17 5G 256GB, 8GB RAM, cámara trasera 48MP, frontal 18MP, 6.3", lavanda',
     3799.00, 5, "513767/iPhone-17-256GB-8GB-lavanda_1.jpg?v=639122943517170000"),

    # ---------------- 30 Tablets
    ("30", "01", "Apple", "MD3Y4CLA",
     'iPad 11.ª generación 11" A16 128GB, cámara 12MP, silver',
     1799.00, 7, "507881/MD3Y4CLA_1.jpg.jpg?v=639180826417930000"),
    ("30", "01", "Lenovo", "ZAFM0734PE",
     "Tablet Lenovo Idea Tab 5G 11, 128GB, 8GB RAM, cámara 8MP y 5MP, batería 7040 mAh, Dimensity 6300, luna grey",
     1049.00, 10, "545996/ZAFM0734PE-f3.jpg?v=639159298662500000"),
    ("30", "01", "Lenovo", "ZAG70837PE",
     'Tablet Lenovo Idea Tab Plus 12.1" 256GB, 8GB RAM, cámara principal 13MP y frontal 8MP, 10200 mAh, MediaTek Dimensity 6400, grey',
     1399.00, 6, "533181/tablet-lenovo-idea-tab-plus-11-256gb-8gb-ram-frontal-oblicuo-pencil.jpg?v=639114638955900000"),

    # ---------------- 40 Televisores
    ("40", "01", "Samsung", "UN32H5000FGXPE",
     'Smart TV Samsung 32", HD H5000F, LED, sistema Tizen integrado, UN32H5000FGXPE, 2025',
     559.00, 14, "550095/UN32H5000FGXPE.jpg?v=639173944327770000"),
    ("40", "02", "LG", "50UA7300PSBAWFQ",
     'Smart TV LG 50" 4K Ultra HD, LED, sistema webOS 25 integrado, 50UA7300PSB (2025)',
     1199.00, 11, "540952/50UA7300PSBAWFQ-1.jpg?v=639124714059130000"),
    ("40", "03", "TCL", "RRJ501",
     'Smart TV TCL 43" Full HD QLED, Google TV, 43S5K 2026',
     679.00, 12, "536972/image-82678db15e40444ea2db4865d0e0c995.jpg?v=639106700433800000"),
    ("40", "03", "LG", "RRJ535",
     'Smart TV LG 50" 4K Ultra HD, NanoCell, sistema webOS integrado, 50NU800BPSC, 2026',
     1119.00, 9, "578817/RRJ535-2.jpg?v=639257967823400000"),
    ("40", "04", "Samsung", "UN55M70HAGXPE",
     'Smart TV Samsung 55", 4K, Mini LED M70H, sistema Tizen integrado, UN55M70HAGXPE, 2026',
     1499.00, 7, "550102/UN55M70HAGXPE.jpg?v=639173945870130000"),

    # ---------------- 50 Audio
    ("50", "01", "JBL", "JBLT530BTBLKAM",
     "Audífonos Bluetooth on ear JBL Tune 530BT, almohadillas acolchadas, máx. 40 horas, control de música y llamadas, negro",
     159.00, 35, "544150/JBLT530BTBLKAM_8.jpg?v=639191211953070000"),
    ("50", "01", "JBL", "JBLLIVE780NCBLK",
     "Audífonos bluetooth on ear JBL Live 780NC, almohadillas acolchadas, hasta 80 horas, control de música y llamadas, negro",
     499.00, 10, "542326/JBLLIVE780NCBLK-LIVE-4.jpg?v=639190610596770000"),
    ("50", "02", "Apple", "MFHP4AM-A",
     "AirPods Pro 3ra Gen, cancelación de ruido, resistente al agua IP57, duración máx. 30 horas con estuche de carga, blanco",
     999.00, 12, "487727/audifonos-bluetooth-apple-airpods-pro-3ra-gen-usbc-blanco-1.jpg?v=639192323007200000"),
    ("50", "02", "Apple", "MXP63AM-A",
     "AirPods 4ta Gen, resistente al agua IP54, duración máx. 30 horas, con estuche de carga, blanco",
     549.00, 15, "398598/MXP63AM-A_1.jpg?v=639192074560370000"),
    ("50", "02", "Samsung", "SM-R410NZKALTA",
     "Audífonos bluetooth True Wireless Samsung Buds Core resistente al agua IP54, duración máx. 35 horas con estuche de carga, cancelación de ruido, negro",
     189.00, 25, "487638/audifonos-samsung-buds-core-tws-anc-35h-negro-1.jpg?v=639193195958830000"),
    ("50", "03", "JBL", "JBLGO5BLKAM",
     "Parlante Bluetooth JBL Go 5, 4.8W, resistente al agua IP68, hasta 10 horas de reproducción, negro",
     169.00, 30, "552183/Negro-1.jpg?v=639180843475900000"),
    ("50", "03", "JBL", "JBLCHARGE6BLKAM",
     "Parlante JBL Charge 6 Bluetooth, 45W RMS, IP68, negro",
     599.00, 14, "552135/JBLCHARGE6BLKAM_2.jpg.jpg?v=639195714165200000"),

    # ---------------- 60 Smartwatch y Wearables
    ("60", "01", "Xiaomi", "57760",
     'Smartwatch Xiaomi Redmi Watch 5 Active 2" LCD, llamadas Bluetooth, más de 140 modos deportivos, 5ATM, batería hasta 18 días, correa M, negro',
     139.00, 20, "558620/240.png?v=639199002457470000"),
    ("60", "01", "Roadtrip", "S6",
     'Smartwatch Roadtrip Watch 4 GPS, pantalla 1.46", resistente al agua IP67, modos de deporte, batería hasta 5 días, negro + correa gris',
     149.00, 18, "529611/S6_1.jpg?v=639072017840100000"),
    ("60", "01", "Roadtrip", "S6-MINI",
     'Smartwatch Roadtrip Watch 4 GPS, pantalla 1.27", resistente al agua IP67, modos de deporte, batería hasta 5 días, rosado + correa negra',
     149.00, 12, "399208/S6-MINI_1.jpg?v=638664213418800000"),

    # ---------------- 70 Gamer
    ("70", "01", "Sony", "DIGITALBNDL2115",
     "Consola PlayStation 5 Slim Edición Digital Bundle Gran Turismo 7 + Astro Bot, SSD 825GB, gráficos 4K, control DualSense",
     2999.00, 6, "535065/consola-ps5-digital-bundle-gran-turismo-7-astro-bot-frontal-empaque.jpg?v=639096221869030000"),
    ("70", "01", "Sony", "PS5STANDBNDL2",
     "Consola PlayStation 5 Slim Edición Standard Bundle Gran Turismo 7 + Astro Bot, SSD 1TB, lector de discos, gráficos 4K, audio 3D, control DualSense",
     3699.00, 5, "534699/PS5STANDBNDL2-3.jpg?v=639094489014800000"),
    ("70", "02", "Asus", "90NV00H2-M00490",
     'Consola portátil Asus ROG Xbox Ally X 2025 7" AMD Ryzen AI Z2 Extreme, 1TB SSD, 24GB RAM, 120Hz, Win11, negro + 3 meses Xbox Game Pass',
     3699.00, 4, "550294/Asus-ROG-Xbox-Ally-X-2025-1tb-24gb_1.jpg?v=639180824423300000"),
    ("70", "04", "Coolbox Teraware", "TERASILLA23-BK",
     "Silla gamer Coolbox T4G carga máxima 100kg, con reposapiés y almohadillas, estructura de aleación de aluminio, reclinable, negra",
     299.00, 8, "341138/TERASILLA23-BK_1.jpg?v=638424813462130000"),

    # ---------------- 80 Accesorios de Cómputo
    ("80", "01", "Logitech", "910-006862",
     "Mouse inalámbrico Logitech M170 receptor USB, 1000 dpi, 3 botones, rosado",
     39.90, 60, "403622/910-006862_1.jpg?v=639198361116170000"),
    ("80", "01", "Logitech", "910-005790",
     "Mouse gamer alámbrico Logitech G203 Lightsync, conexión USB, 8000 dpi, 6 botones, luces RGB, negro",
     99.90, 25, "192029/910-005790_1.jpg?v=637690413056470000"),
    ("80", "02", "Logitech", "920-004422",
     "Teclado alámbrico Logitech K120 USB, membrana, español, negro",
     59.90, 40, "207802/920-004422_1.jpg?v=639178274770770000"),
    ("80", "02", "Logitech", "920-013446",
     "Teclado inalámbrico Logitech K250 Bluetooth, membrana, español, blanco",
     89.90, 20, "506870/teclado-inalambrico-logitech-k250-bluetooth-espanol-blanco_3.jpg?v=639126654926470000"),
    ("80", "03", "Teros", "MON238CTE2403S",
     'Monitor curvo Teros TE-2403S 23.8" Panel VA, FHD, 144Hz, 1ms, entradas HDMI/DP',
     369.00, 10, "541736/Monitor-curvo-Teros-23.8-FHD-144Hz-1ms-VA-frontal.jpg?v=639135057002100000"),
]

# Productos de la versión anterior -> referencia Coolbox real que los reemplaza.
# Se conserva la fila (y por tanto sus ventas y stock); se actualizan nombre,
# precio, imagen, familia, marca y se le asigna su SKU.
EQUIVALENCIAS_ANTERIORES = {
    "LAP001": "HP15FC0025-T",
    "LAP002": "82YU00X5LMPY",
    "LAP003": "PS29857020",
    "LAP004": "PS41703571",
    "CEL001": "SM-A175FZKKLTP",
    "CEL002": "72149",
    "CEL003": "MTP03BE-A",
    "CEL004": "PB970058PE",
    "CEL005": "SM-A075MZKALTP",
    "AUD001": "JBLT530BTBLKAM",
    "AUD002": "MFHP4AM-A",
    "AUD003": "JBLCHARGE6BLKAM",
    "AUD004": "SM-R410NZKALTA",
    "TV001": "UN55M70HAGXPE",
    "TV002": "50UA7300PSBAWFQ",
    "TV003": "RRJ501",
    "ACC001": "910-006862",
    "ACC002": "920-004422",
    "GAM001": "PS5STANDBNDL2",
    "GAM002": "90NV00H2-M00490",
}


# ---------------------------------------------------------------- permisos/roles

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


# ---------------------------------------------------------------- catálogo

def _seed_familias_marcas():
    """Crea/actualiza familias (reutilizando las categorías antiguas), subfamilias y marcas."""
    legado = {codigo: nombre for nombre, codigo in FAMILIAS_ANTERIORES.items()}
    for codigo, nombre, descripcion, subfamilias in FAMILIAS:
        familia = Familia.query.filter_by(codigo=codigo).first()
        if not familia and codigo in legado:
            familia = Familia.query.filter_by(nombre=legado[codigo], codigo=None).first()
        if not familia:
            familia = Familia.query.filter_by(nombre=nombre).first()
        if not familia:
            familia = Familia(nombre=nombre)
            db.session.add(familia)
        familia.codigo = codigo
        familia.nombre = nombre
        familia.descripcion = descripcion
        db.session.flush()
        for sub_codigo, sub_nombre in subfamilias:
            sub = Subfamilia.query.filter_by(familia_id=familia.id, codigo=sub_codigo).first()
            if not sub:
                db.session.add(Subfamilia(familia_id=familia.id, codigo=sub_codigo, nombre=sub_nombre))
            else:
                sub.nombre = sub_nombre

    for codigo, nombre in MARCAS:
        marca = Marca.query.filter_by(codigo=codigo).first() or Marca.query.filter_by(nombre=nombre).first()
        if not marca:
            db.session.add(Marca(codigo=codigo, nombre=nombre))
    db.session.commit()


def _marcas_texto_anteriores():
    """La versión anterior guardaba la marca como texto en producto.marca."""
    from sqlalchemy import inspect, text
    columnas = {c["name"] for c in inspect(db.engine).get_columns("producto")}
    if "marca" not in columnas:
        return {}
    filas = db.session.execute(text("SELECT id, marca FROM producto WHERE sku IS NULL")).all()
    return {pid: (marca or "").strip() for pid, marca in filas}


def _marca_por_nombre(nombre):
    nombre = (nombre or "").strip() or "Coolbox Teraware"
    marca = Marca.query.filter(db.func.lower(Marca.nombre) == nombre.lower()).first()
    if marca:
        return marca
    usados = {int(c) for (c,) in db.session.query(Marca.codigo).all() if c and c.isdigit()}
    libre = next(n for n in range(1, 100) if n not in usados)
    marca = Marca(codigo=f"{libre:02d}", nombre=nombre[:80])
    db.session.add(marca)
    db.session.flush()
    return marca


def _asignar_sku(producto):
    if producto.sku or not producto.subfamilia or not producto.marca:
        return
    familia = producto.subfamilia.familia
    prefijo = f"{familia.codigo}{producto.subfamilia.codigo}{producto.marca.codigo}"
    existentes = [s for (s,) in db.session.query(Producto.sku).filter(Producto.sku.like(f"{prefijo}%")).all()]
    producto.sku = siguiente_sku(familia, producto.subfamilia, producto.marca, existentes)
    db.session.flush()


def _seed_productos():
    familias = {f.codigo: f for f in Familia.query.all()}
    marcas = {m.nombre: m for m in Marca.query.all()}
    subfamilias = {(s.familia.codigo, s.codigo): s for s in Subfamilia.query.all()}

    # 1) Convertir productos antiguos a su equivalente real (si aún no existe)
    for viejo_codigo, nuevo_codigo in EQUIVALENCIAS_ANTERIORES.items():
        viejo = Producto.query.filter_by(codigo=viejo_codigo).first()
        if viejo and not Producto.query.filter_by(codigo=nuevo_codigo).first():
            viejo.codigo = nuevo_codigo
    db.session.flush()

    # 2) Alta / actualización de los productos reales
    for fam, sub, marca_nombre, codigo, nombre, precio, stock, imagen in PRODUCTOS:
        familia = familias[fam]
        subfamilia = subfamilias[(fam, sub)]
        marca = marcas[marca_nombre]
        producto = Producto.query.filter_by(codigo=codigo).first()
        nuevo = producto is None
        if nuevo:
            producto = Producto(codigo=codigo, stock=0, activo=True)
            db.session.add(producto)
        # Datos maestros: se alinean con coolbox.pe
        if nuevo or not producto.sku:
            producto.nombre = nombre
            producto.precio = precio
            producto.descripcion = f"{marca.nombre} · {familia.nombre} · {subfamilia.nombre}. Disponible en tiendas Coolbox."
            producto.stock_minimo = max(3, stock // 4)
            producto.imagen_url = IMG + imagen
        producto.categoria_id = familia.id
        producto.subfamilia_id = subfamilia.id
        producto.marca_id = marca.id
        # Producto sin foto (o con la foto de Wikimedia de la versión anterior): usar la oficial
        if not producto.imagen_url or "wikimedia.org" in producto.imagen_url:
            producto.imagen_url = IMG + imagen
        db.session.flush()
        _asignar_sku(producto)
        if nuevo and stock:
            producto.stock = stock
            db.session.add(MovimientoStock(producto=producto, tipo="inicial", cantidad=stock,
                                           stock_anterior=0, stock_nuevo=stock,
                                           motivo="Stock inicial (carga de catálogo)"))
    db.session.commit()

    # 3) Productos creados a mano que aún no tienen SKU: subfamilia 01 de su
    #    familia y la marca que tenían escrita (se crea si no existe).
    marcas_texto = _marcas_texto_anteriores()
    for producto in Producto.query.filter(Producto.sku.is_(None)).all():
        familia = producto.familia
        if not familia or not familia.subfamilias:
            continue
        if not producto.subfamilia_id:
            producto.subfamilia_id = familia.subfamilias[0].id
        if not producto.marca_id:
            producto.marca_id = _marca_por_nombre(marcas_texto.get(producto.id)).id
        db.session.flush()
        db.session.refresh(producto)
        _asignar_sku(producto)
    db.session.commit()


# ---------------------------------------------------------------- usuarios demo

def _crear_usuario_demo(dni, nombres, ap_paterno, ap_materno, password, roles, email=None):
    """Crea el usuario solo si no existe (no toca usuarios ya registrados)."""
    if Usuario.query.filter_by(dni=dni).first():
        return
    email = email or build_email(
        nombres, ap_paterno, ap_materno, "coolbox.com.pe",
        existe_email=lambda e: Usuario.query.filter_by(email=e).first() is not None,
    )
    usuario = Usuario(dni=dni, nombres=nombres, apellido_paterno=ap_paterno, apellido_materno=ap_materno,
                      email=email, fecha_ingreso=date.today())
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
    _seed_familias_marcas()
    _seed_productos()
    _seed_usuarios_demo()


if __name__ == "__main__":
    from app import create_app

    app = create_app()
    with app.app_context():
        db.create_all()
        seed_catalogo_maestro()
        print("Catálogo maestro y datos demo cargados correctamente.")
