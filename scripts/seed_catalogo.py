"""Seed idempotente que carga catálogo maestro + demo:

- Permisos y roles (catálogo maestro). Retira roles obsoletos (Cajero, Almacenero).
- Familias, subfamilias y marcas (estructura del SKU de 8 dígitos).
- 101 productos reales de coolbox.pe (nombre, referencia, precio e imagen
  oficial publicados en la tienda en línea de Coolbox, octubre 2026).
- Migra los 20 productos de la versión anterior (LAP001, CEL001...) a su
  equivalente real, conservando su stock y su historial de ventas.
- Asigna SKU a cualquier producto que aún no lo tenga.
- Usuarios demo: administrador, vendedor y supervisor de ventas (este último también vende).

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

# Ampliación del catálogo (octubre 2026): más productos reales de coolbox.pe
PRODUCTOS += [
    # ---------------- 10 Laptops
    ("10", "01", "Lenovo", "83K100NALMPY",
     'Laptop Lenovo IdeaPad Slim 3 15IRH10 15.3", Intel Core i7-13620H, 1TB SSD, 16GB RAM, FreeDos - sin Sistema Operativo, azul',
     3199.00, 7, "561811/83K100NALMPY-5.jpg?v=639215505086700000"),
    ("10", "01", "Lenovo", "RSC-LEN148LAP",
     'Laptop Lenovo IdeaPad Slim 3 14IRH10 14" WUXGA OLED, Intel Core i5-13420H, 16GB RAM, SSD 512GB, luna grey',
     2819.00, 8, "563774/image-1b458920428e485a8dc8c7a9dd22765f.jpg?v=639223151994370000"),
    ("10", "01", "Lenovo", "83GW005GLD32PY",
     'Laptop Lenovo V15 G5 IRL 15.6", Intel Core i7 13620H, 512GB SSD, 40GB RAM, FreeDos - sin Sistema Operativo, luna grey',
     3699.00, 5, "569467/83GW005GLD32PY-5.jpg?v=639237886825900000"),
    ("10", "01", "Asus", "PS41019179",
     'Laptop Asus Vivobook F1605VAWS74 16" Intel Core i7-1355U, 512GB SSD, 16GB RAM, Win11 Home, negro',
     3399.00, 6, "554183/Laptop-Asus-Vivobook-16-i7-512GB-16GB-Negro-frontal.jpg?v=639186131772730000"),
    ("10", "01", "Asus", "PS80748079",
     'Laptop Asus Vivobook 15 X1504VA-E84085 15.6", Intel Core 5 120U, 512GB SSD, 16GB RAM, Windows 11 Home, quiet blue',
     2999.00, 9, "587558/110.jpg?v=639282198249500000"),
    ("10", "01", "Asus", "PS24552277",
     'Laptop Asus Vivobook F1502VA-SB96 15.6" Intel Core i9-13900H, 1TB SSD, 16GB RAM, Win11 Home, azul',
     4859.00, 4, "554386/Laptop%20Asus%20Vivobook%2015.6%20i9%201TB%2016GB%20Azul%20frontal.jpg?v=639186965728130000"),
    ("10", "01", "Dell", "3XHY0PY",
     'Laptop Dell DC15250 15.6" FHD, Intel Core i7-1355U, 512GB SSD, 16GB RAM, FreeDOS - sin sistema operativo, teclado en español, silver',
     2899.00, 6, "550329/image-9015cc2241324904a82d3ba69bf4900f.jpg?v=639174233635670000"),
    ("10", "02", "HP", "NBHPC2DA0LA",
     'Laptop gamer HP OMEN 16-ap0007la, 16", Ryzen 9 8940HX, SSD 1TB, 24GB RAM, RTX 5070, Windows 11, negro',
     9399.00, 3, "571041/NBHPC2DA0LA-f2.jpg?v=639240541292330000"),
    ("10", "03", "Apple", "MDHE4E-A",
     'MacBook Air 13.6" Chip M5, 512GB SSD, 16GB RAM, macOS, teclado español, midnight',
     5699.00, 4, "538556/MacBook-Air-13.6-M5-512GB-16GB-RAM-midnight-frontal.jpg?v=639117843971100000"),
    ("10", "03", "Apple", "MDHF4E-A",
     'MacBook Air 13.6" Chip M5, 1TB SSD, 16GB RAM, macOS, teclado español, midnight',
     6799.00, 3, "538542/MacBook-Air-13.6-M5-512GB-16GB-RAM-midnight-frontal.jpg?v=639117844153370000"),
    ("10", "03", "Apple", "PS17570363",
     'MacBook Neo 13" Chip A18 Pro, 256GB SSD, 8GB RAM, macOS, teclado inglés, silver',
     3299.00, 5, "554030/image-d10740f46c534eb883cc8879f0e3248b.jpg?v=639185442022630000"),
    ("10", "03", "Apple", "ISMBPM5141TB02",
     'MacBook Pro 14" Chip M5, CPU 10 núcleos, GPU 10 núcleos, 1TB SSD, 16GB RAM, macOS, space black',
     7739.00, 3, "540058/image-65d47eba995e4667b13047c39fbc6d6c.jpg?v=639120641467670000"),

    # ---------------- 20 Celulares
    ("20", "01", "Samsung", "SM-A165MZKALTP",
     'Celular Samsung Galaxy A16 4G 128GB, 4GB RAM, cámara trasera 50MP y frontal 13MP, 6.7", negro',
     699.00, 28, "507482/SM-A165MZKALTP_1.jpg.jpg?v=639123124565670000"),
    ("20", "01", "Xiaomi", "74443",
     'Celular Xiaomi Redmi A7 Pro 4G 64GB, 4GB RAM, cámara trasera 13MP y frontal 8MP, 6.9", negro',
     449.00, 30, "547275/74443--1-.jpg?v=639185201286030000"),
    ("20", "01", "Xiaomi", "74451",
     'Celular Xiaomi Redmi A7 Pro 4G 64GB, 4GB RAM, cámara trasera 13MP y frontal 8MP, 6.9", azul',
     449.00, 24, "547286/74451-celular-xiaomi-redmi-lima--1-.jpg?v=639162805714970000"),
    ("20", "01", "Xiaomi", "67515",
     'Celular Xiaomi Redmi 15C 256GB, 4GB RAM, cámara trasera 50MP + frontal 8MP, 6.9", batería de larga duración, negro',
     699.00, 22, "510188/67515.jpg?v=639123011735970000"),
    ("20", "01", "Xiaomi", "XIARD17256NGHP",
     'Celular Xiaomi Redmi 17 4G 128GB, 4GB RAM, cámara 50MP, frontal 8MP, 6.9", negro',
     689.00, 18, "567855/XIARD17256NGHP-f1.jpg?v=639232800911470000"),
    ("20", "01", "Honor", "5109CGPC",
     'Celular Honor X5D 128GB, 4GB RAM, cámara trasera 50MP y frontal 5MP, 6.74", azul',
     479.00, 26, "568393/369.png?v=639233555124000000"),
    ("20", "01", "Motorola", "6830981",
     'Celular Motorola G17 4G 128GB, 4GB RAM, cámara trasera 108MP y frontal 32MP, 6.8", arándano',
     599.00, 20, "564212/393.png?v=639259725071130000"),
    ("20", "02", "Samsung", "MCP-A275G8GB",
     'Celular Samsung Galaxy A27 5G, 256GB, 8GB RAM, cámara principal 50MP, frontal 12MP, 6.7", negro',
     1299.00, 15, "580549/MCP-A275G8GB.jpg?v=639262332098870000"),
    ("20", "02", "Samsung", "SM-A576BDBNLTP",
     "Celular Samsung Galaxy A57 5G 256GB, 8GB RAM, cámara trasera 50MP y frontal 12MP, 6.7'', azul oscuro",
     2099.00, 10, "563645/celular-samsung-galaxy-a57-5g-256gb-8gb-ram-azul-oscuro-frontal-dorsal.jpg?v=639222551945400000"),
    ("20", "02", "Motorola", "PS24716367",
     "Celular Motorola Moto G Max 5G, 256GB, 8GB RAM, cámara trasera 50MP y frontal 32MP, gris",
     1309.00, 12, "581064/image-2b74fce0cc3f448b96f08de072aa9a6a.jpg?v=639263850612830000"),
    ("20", "02", "Honor", "5109CHBS",
     "Celular Honor 600 512GB, 8GB RAM, dorado",
     2499.00, 8, "547312/5109CHBS-f2.jpg?v=639165223766970000"),
    ("20", "03", "Apple", "MTP43BE-A",
     'iPhone 15 5G 128GB, 6GB RAM, cámara trasera 48MP y frontal 12MP, 6.1", azul',
     2599.00, 8, "537452/Proyecto-nuevo--1-.jpg?v=639111099660500000"),
    ("20", "03", "Apple", "MFYP4BE-A",
     'iPhone 17 Pro Max 5G 256GB, 12GB RAM, cámara trasera 48MP, frontal 18MP, 6.9", Deep Blue',
     5499.00, 4, "512333/iphone-17promax-5g-256gb-azul-sierra_1.jpg?v=639023810883930000"),
    ("20", "03", "Samsung", "SM-S942BZVVLTP",
     "Celular Samsung Galaxy S26 5G 512GB, 12GB RAM, cámara trasera 50MP y frontal 12MP, 6.3'', violeta cobalto",
     4499.00, 5, "530684/celular-samsung-galaxy-s26-5g-512gb-12gb-ram-violeta-cobalto-sm-s942bzvvltp.jpg?v=639080823943470000"),
    ("20", "03", "Honor", "PS29541121",
     'Celular Honor Magic 7 Pro 5G, 512GB, 12GB RAM, cámara trasera 50MP y frontal 50MP, 6.8", shadow grey',
     3919.00, 4, "463313/Honor-Magic-7-Pro-shadow-grey_1.jpg?v=638857918322500000"),
    ("20", "03", "Motorola", "PS36291748",
     "Celular Motorola Razr 60 Ultra 5G, 1TB, 16GB RAM, plegable, café",
     4059.00, 3, "581789/322.jpg?v=639264774480870000"),

    # ---------------- 30 Tablets
    ("30", "01", "Apple", "MD4A4CLA",
     'iPad 11" 128GB, Chip A16, cámara 12MP, WiFi',
     1699.00, 6, "507898/MD4A4CLA_1.jpg.jpg?v=639180835627430000"),
    ("30", "01", "Apple", "MH304CL-A",
     'iPad Air 11" 128GB, 12GB RAM, cámara principal 12MP y frontal 12MP, Chip M4, iPadOS, space gray',
     3099.00, 4, "544071/iPad-Air-11-M4-128GB-12GB-RAM-space-gray-frontal-dorsal.jpg?v=639148067145770000"),
    ("30", "01", "Apple", "OGAIR11M7",
     'iPad Air M4 11" 128GB, 12GB RAM, cámara principal 12MP y frontal 12MP, procesador M4, morado',
     2789.00, 4, "533449/DSAKJASD.jpg?v=639086683267600000"),

    # ---------------- 40 Televisores
    ("40", "01", "LG", "RRJ533",
     'Smart TV LG 32" HD, LED, sistema webOS integrado, 32LB650BPSA, 2026',
     549.00, 12, "578653/RRJ533-2.jpg?v=639257932876030000"),
    ("40", "02", "Samsung", "RRJ546",
     'Smart TV Samsung 65", 4K UHD, Crystal UHD, Tizen integrado, U8000H, 2026',
     1469.00, 6, "581524/406.jpg?v=639264650289600000"),
    ("40", "04", "LG", "55QNED70BSAAWF",
     'Smart TV LG 55", 4K Ultra HD, MiniLED, sistema webOS integrado, 55QNED70BSA, 2026',
     1699.00, 5, "547006/55QNED70BSAAWF-1.jpg?v=639162077752370000"),

    # ---------------- 50 Audio
    ("50", "01", "Logitech", "AUD2024101-N",
     "Headset Logitech H151 Stereo con micrófono, conector 3.5 mm",
     109.00, 25, "311836/image-c8fac1c669154876b2b77138e379029c.jpg?v=638315356925930000"),
    ("50", "02", "JBL", "JBLWBUDS2BLK",
     "Audífonos bluetooth True Wireless JBL Wave Buds 2, cancelación de ruido, IP54, hasta 40 horas con estuche de carga, negro",
     179.00, 30, "505664/jblll.jpg.jpg?v=639192171497530000"),
    ("50", "02", "JBL", "JBLTUNE2NG-OF",
     "Audífonos Bluetooth true wireless JBL Tune Flex 2, ANC adaptativa, IP54, hasta 48h con estuche de carga inalámbrica, negro",
     299.00, 18, "558085/168.png?v=639197470403870000"),
    ("50", "03", "JBL", "JBLGOES2BLUAM",
     "Parlante Bluetooth JBL Go Essential 2, 3.1W, resistente al agua IP67, hasta 5 horas de reproducción, azul",
     99.90, 40, "539246/Parlante-JBL-Go-Essential-2-bluetooth-IP67-5h-azul-frontal.jpg?v=639119546150130000"),
    ("50", "03", "JBL", "JBLGOES2BLKAM",
     "Parlante Bluetooth JBL Go Essential 2, 3.1W, resistente al agua IP67, hasta 5 horas de reproducción, negro",
     99.90, 40, "539264/Parlante-JBL-Go-Essential-2-bluetooth-IP67-5h-negro-frontal.jpg?v=639119548097070000"),
    ("50", "03", "JBL", "JBLGO5BLUE-OF",
     "Parlante Bluetooth JBL Go 5 de 4.8W RMS con IP68, Auracast, audio USB-C, hasta 10 horas, azul",
     159.00, 25, "545603/image-f4cf3a049c3e4e50b6ef11bea6a9aea5.jpg?v=639156035240130000"),
    ("50", "03", "JBL", "JBLPBCLUB120AM",
     "Parlante de torre bluetooth JBL PartyBox Club 120, 160W, resistente al agua IPX4, hasta 12 horas, con luces, negro",
     1199.00, 5, "363708/PartyBox-Club-120-negro_1.jpg?v=638503718211030000"),
    ("50", "04", "JBL", "JBLBAR1000M2BLK",
     "Barra de sonido JBL 1000MK2 bluetooth, 960W, subwoofer, sonido 360, Dolby Atmos, negro",
     2999.00, 3, "533030/barra-sonido-jbl-1000mk2-bluetooth-960w-dolby-atmos-negro-jblbar1000m2blk_5.jpg?v=639084077841600000"),

    # ---------------- 60 Smartwatch y Wearables
    ("60", "01", "Apple", "MEQX4AM-A",
     "Apple Watch Series 11 42mm OLED, resistente al agua hasta 50 m, batería hasta 24 horas, grey aluminium",
     2199.00, 5, "512983/apple-watch-series-11-42mm-meqx4am-a.jpg?v=639027093668630000"),
    ("60", "01", "Apple", "MEHJ4AM-A",
     "Apple Watch SE 3 44mm OLED, resistente al agua hasta 50 m, batería hasta 24 horas, blanco estelar",
     1599.00, 6, "533681/529632-800-800.jpg?v=639087802035000000"),
    ("60", "01", "Apple", "ISTAWSE344MM01-1",
     "Smartwatch Apple Watch SE 3, pantalla 44mm OLED, GPS, resistente al agua, 18 horas, midnight",
     1449.00, 6, "546179/image-f562cce285244f7c8610668925d59cbe.jpg?v=639159565267200000"),
    ("60", "02", "Xiaomi", "6932554419707",
     'Smartband Xiaomi Band 10 pantalla 1.72" AMOLED, 5ATM, batería hasta 21 días, 150 modos deportivos, SPO2, negro medianoche',
     179.00, 30, "529354/smartwatch-xiaomi-band-3-pantalla-1-72-amoled-5atm-150-modos-spo2-negro_1.jpg?v=639071186836770000"),
    ("60", "02", "Xiaomi", "6932554419844",
     'Smartband Xiaomi Band 10 pantalla 1.72" AMOLED, 5ATM, batería hasta 21 días, 150 modos deportivos, SPO2, rosa místico',
     179.00, 20, "505198/smartwatch-xiaomi-band-3-pantalla-1-72-amoled-5atm-150-modos-spo2-rosa_1.jpg?v=639003719387930000"),
    ("60", "02", "Huawei", "55020GUR",
     "Smartband Huawei Band 11 pantalla 1.62'' AMOLED, resistencia al agua 5ATM, batería hasta 14 días, morado",
     199.00, 15, "532856/smartband-huawei-band-11-pantalla-1-62-amoled-5atm-morado-55020gur_5.jpg?v=639083462158700000"),
    ("60", "02", "Huawei", "55020GUS",
     "Smartband Huawei Band 11 pantalla 1.62'' AMOLED, resistencia al agua 5ATM, batería hasta 14 días, negro",
     199.00, 15, "532906/smartband-huawei-band-11-pantalla-1-62-amoled-5atm-negro-55020gus_5.jpg?v=639083471486600000"),

    # ---------------- 70 Gamer
    ("70", "01", "Sony", "TWVS4001023-L",
     "Consola PlayStation 5 Pro Digital 2TB SSD, 16GB RAM, mando DualSense y cables",
     4509.00, 3, "565594/TWVS4001023-L-f1.jpg?v=639228569865800000"),
    ("70", "02", "Nintendo", "TWVS4001063-L",
     'Consola Nintendo Switch 2 256GB, pantalla 7.9" Full HD, Joy-Con 2, GameChat integrado',
     2099.00, 6, "533472/dsadsadaswe.jpg?v=639086713826070000"),
    ("70", "02", "Nintendo", "TWVS4001152-L",
     "Consola Nintendo Switch 2 Choose Your Game Bundle, Full HD, 256GB, 12GB RAM, negro",
     2299.00, 4, "555886/image-a10a19685eb54aa8ad0644981f617bcd.jpg?v=639192265822330000"),
    ("70", "02", "Nintendo", "TWVSSWITCH2BUND",
     "Consola Nintendo Switch 2 Bundle Mario Kart World físico, Full HD, 256GB, 12GB RAM, negro",
     2769.00, 4, "543707/Nintendo%20Switch%202%20Mario%20Kart%20World%20256GB%20negra%20kit.jpg?v=639144853908700000"),
    ("70", "03", "Sony", "LPCM2309017-LP",
     "Estación de carga PlayStation 5 DualSense",
     139.00, 15, "282688/image-4dee4d7f575e4aeba01c1e6cdcea4571.jpg?v=638235563442530000"),

    # ---------------- 80 Accesorios de Cómputo
    ("80", "01", "Logitech", "910-004940",
     "Mouse inalámbrico Logitech M170 USB 2.4GHz, 1000 DPI, 3 botones, negro",
     49.90, 50, "523224/910-004940.jpg?v=639258853051030000"),
    ("80", "01", "Logitech", "GC03343",
     "Mouse gamer Logitech G Pro X Superlight 2 Wireless Lightspeed, 32000 dpi, recargable, negro",
     555.00, 8, "403774/Pro-X-SuperLight-2-negro_1.jpg?v=638682484983070000"),
    ("80", "02", "Logitech", "920-009845",
     "Teclado inalámbrico Logitech ERGO K860, bluetooth, ergonómico, teclado numérico, negro",
     559.00, 6, "187929/920-009845_1.jpg?v=637630154067370000"),
    ("80", "03", "Teros", "MON238FTE2415S",
     'Monitor plano 23.8" Teros TE-2415 Panel IPS, FHD, 120Hz, 1ms, entradas HDMI/DisplayPort',
     359.00, 10, "517474/monitor-plano-23.8-teros-te-2415_1.jpg?v=639039192051370000"),
    ("80", "03", "Teros", "MON27CTE2734S",
     'Monitor curvo Teros TE-2734S 27" Panel VA, FHD, 144Hz, 1ms, entradas HDMI/DP',
     449.00, 8, "541746/Monitor-curvo-Teros-27-FHD-144Hz-1ms-VA-frontal.jpg?v=639135056901000000"),
    ("80", "03", "Asus", "PA278QGV",
     'Monitor plano ASUS ProArt PA278QGV 27", IPS, QHD, 120Hz, 5ms, HDMI/DP, HDR-10, 95% DCI-P3, negro',
     1049.00, 4, "555699/379.png?v=639191444469630000"),
    ("80", "04", "Logitech", "097855157430-I",
     "Audífono gamer Logitech G Pro X Wireless Lightspeed Blue Voice, negro",
     725.00, 5, "455303/image-eab64e3a7d844ee6bc39de1571a450f0.jpg?v=638839564181170000"),
    ("80", "04", "Coolbox Teraware", "TERAKITTR-2",
     "Kit inalámbrico Coolbox Teraware Mouse bluetooth 1600 DPI + Headset inalámbrico, negro",
     99.90, 20, "548909/ChatGPT-Image-12-jun-2026-04_35_34-p.m..png?v=639197346488930000"),
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
    """Elimina roles retirados (Cajero, Almacenero) sin perder usuarios ni ventas.

    Si el rol tiene reemplazo, sus usuarios y ventas pasan al rol nuevo.
    Si no lo tiene (Almacenero), solo se retira de los usuarios; quien se quede
    sin roles deberá recibir uno nuevo desde la gestión de usuarios.
    """
    for codigo_obsoleto, codigo_reemplazo in ROLES_OBSOLETOS.items():
        viejo = Rol.query.filter_by(codigo=codigo_obsoleto).first()
        if not viejo:
            continue
        nuevo = Rol.query.filter_by(codigo=codigo_reemplazo).first() if codigo_reemplazo else None
        if not nuevo and Venta.query.filter_by(rol_id=viejo.id).first():
            # Sin reemplazo y con ventas históricas: se conserva para no romper el historial.
            continue
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
    # Vendedor demo: vende y cobra en su propia caja
    # (jperezl@coolbox.com.pe / Vendedor123!)
    _crear_usuario_demo("70123456", "Juan Daniel", "Perez", "Lozano", "Vendedor123!",
                        [rol.get("vendedor")])
    # Supervisor de ventas demo con dos roles: al ingresar elige con cuál trabajar.
    # Como Supervisor es el único rol de tienda que anula ventas.
    # (mtorresr@coolbox.com.pe / Supervisor123!)
    _crear_usuario_demo("70987654", "Maria Elena", "Torres", "Rojas", "Supervisor123!",
                        [rol.get("supervisor"), rol.get("vendedor")])
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
