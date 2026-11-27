# Coolbox — Backend

API REST en Flask para el sistema de gestión de tienda Coolbox (Perfiles,
Usuarios, Roles, Productos y Ventas). Usa PostgreSQL en Supabase, se
despliega en Render y sirve al frontend en Vercel.

## Arranque local

1. Instala Python 3.12 y crea un entorno virtual: `python -m venv .venv`, luego `.\.venv\Scripts\activate` en Windows.
2. Instala dependencias: `pip install -r requirements.txt`.
3. Copia `.env.example` a `.env` y configura `DATABASE_URL`, `SECRET_KEY`, `JWT_SECRET_KEY` y `FRONTEND_ORIGIN`.
4. Inicia la API: `flask --app wsgi:app run --debug`.

Al arrancar por primera vez el backend crea las tablas y carga el catálogo
maestro (permisos, roles, familias/subfamilias/marcas y 39 productos reales de
coolbox.pe con su imagen oficial y SKU).
Endpoints de salud: `GET /health` y `GET /health/db` (prueba la conexión a la BD).

### Usuarios iniciales

Ninguna contraseña ni PIN está escrito en el código ni en este repositorio.

- Si la BD no tiene administradores, se crea uno con `ADMIN_INICIAL_EMAIL` y
  `ADMIN_INICIAL_PASSWORD`; en su primer ingreso debe cambiar la contraseña.
- Con `SEED_USUARIOS_DEMO=1` se crean además un cajero (`jperezl@...`) y una
  supervisora con dos roles (`mtorresr@...`), con la clave `DEMO_PASSWORD` y el
  PIN `DEMO_PIN`. Cada usuario cambia su clave y su PIN en *Mi cuenta*.

## Variables de entorno

Todas las reglas de negocio, límites de seguridad, credenciales y URLs externas
se leen del entorno (`config.py`). La lista completa, con sus valores por
defecto, está en `.env.example`. Las principales:

| Variable | Uso |
| --- | --- |
| `DATABASE_URL` | Cadena de conexión Postgres. En Supabase usa el **Session Pooler**. |
| `SECRET_KEY`, `JWT_SECRET_KEY` | Claves aleatorias de al menos 32 caracteres. Sin ellas se genera una temporal (las sesiones se cierran al reiniciar). |
| `FRONTEND_ORIGIN` | URLs exactas del frontend y la tienda en Vercel, separadas por coma. |
| `LIMITE_LOGIN`, `LOGIN_MAX_INTENTOS`, `PIN_MAX_INTENTOS`... | Límites contra fuerza bruta (por IP y por cuenta). |
| `IGV_RATE`, `ZONA_HORARIA_HORAS` | Tasa del IGV y zona horaria del negocio. |
| `CUADRE_CIEGO`, `LIMITE_EFECTIVO_CAJA`, `CAJA_FONDO_MAXIMO` | Reglas de caja. |
| `POS_PROVEEDOR` | POS de la tienda (simulado en este proyecto). |
| `SUNAT_*`, `COMPROBANTES_DIR` | API de facturación y carpeta de comprobantes. |
| `CONSULTA_TOKEN` | Token del servicio de consulta de DNI/RUC (autocompleta el cliente). |
| `DEVOLUCION_DIAS_MAX` | Plazo de devolución (caja y pedidos online entregados). |
| `INTEGRACION_*` | API de clientes con el grupo pareado: claves de entrada, URL y clave de su API, reintentos. |

## Seguridad

- Contraseñas y PIN con hash; bloqueo por intentos fallidos por cuenta y límite de peticiones por IP
  (`flask-limiter`) en ingreso, PIN, códigos por correo y consultas externas.
- El ingreso no revela si un correo existe (mismo mensaje y mismo tiempo de respuesta).
- JWT firmado con clave del entorno; el rol activo viaja en el token y se valida en cada endpoint.
- CORS restringido a los orígenes de `FRONTEND_ORIGIN`; cabeceras de seguridad (nosniff, frame DENY,
  CSP, HSTS en https) y límite de tamaño de petición.
- Sin SQL en el código de la aplicación: los módulos nuevos (maestro de clientes, integración, devolución de
  pedidos online) usan procedimientos almacenados de `db/procedimientos.sql`; el resto usa el ORM con
  parámetros. Las únicas sentencias de texto son las migraciones ligeras de estructura
  (`app/services/schema.py`), con nombres fijos del propio código.
- La API de clientes para el otro grupo exige clave (`X-API-Key`), tiene límite de peticiones y deja traza
  de cada llamada.
- Los PDF/XML solo se entregan si están dentro de `COMPROBANTES_DIR`; las imágenes solo aceptan https o
  PNG/JPEG/WebP/GIF en base64 (no SVG).

## Reglas del negocio implementadas

- **Correo automático**: `Juan Daniel Pérez Lozano → jperezl@coolbox.com.pe`. Si ya existe, se agrega un contador (`jperezl1`, `jperezl2`, ...). Ver `app/services/email_generator.py`.
- **Login único**: `POST /api/auth/login` para todo el personal. Con un solo rol, el rol queda activo al ingresar; con varios roles (el Administrador incluido) se elige en `POST /api/auth/seleccionar-rol`.
- **Selección de rol**: al iniciar sesión, el usuario elige uno de sus roles (`POST /api/auth/seleccionar-rol`). El rol activo viaja en el JWT y controla qué endpoints puede usar.
- **Roles predefinidos**: Administrador, **Cajero** y **Supervisor de Caja**. Un usuario puede tener varios roles asignados. Los códigos internos siguen siendo `vendedor` y `supervisor` para no romper usuarios ni ventas existentes; solo cambió el nombre visible. Almacenero se retiró (entradas, salidas y kardex quedan en el Administrador).
- **Autorización con PIN en la caja** (`app/services/autorizacion.py`): el cajero no sale de su sesión. El supervisor elige su nombre, escribe su PIN (exactamente 6 dígitos, guardado con hash) y elige un motivo. La API entrega un token de **un solo uso, válido `PIN_TOKEN_SEGUNDOS`**, solo para esa acción (y esa venta). `PIN_MAX_INTENTOS` PIN errados bloquean el PIN de ese supervisor `PIN_MINUTOS_BLOQUEO` minutos. Nadie puede autorizarse a sí mismo. Todo intento queda en la bitácora `autorizacion`.
- **Acciones que piden PIN**: retirar o restar un producto ya registrado en el carrito, cancelar la venta en curso, anular una venta cobrada (desde la caja del cajero), retiro parcial de efectivo, reimpresión de un comprobante y cierre de caja con diferencia.
- **Anulación de ventas**: el Supervisor de Caja y el Administrador anulan directamente (`ventas.anular`); el Cajero anula ventas de **su** caja abierta con el PIN de un supervisor.
- **Devoluciones (después de la compra)**: en caja y con PIN de supervisor, sobre ventas con boleta o factura aceptada por SUNAT, dentro de `DEVOLUCION_DIAS_MAX` días. Total o por producto, en una o varias devoluciones; el importe es proporcional a lo cobrado (con promociones). Reembolso por un medio usado en la compra (efectivo de la gaveta o extorno con N° de operación). Cada producto vuelve al stock o no (falla → servicio técnico). Se emite una **nota de crédito** (motivo 06 devolución total, 07 por ítem) con serie `SUNAT_SERIE_NC_BOLETA` / `SUNAT_SERIE_NC_FACTURA`; PDF y XML se guardan en `comprobantes/notas_credito/AAAA-MM/DD/`. Una venta con devoluciones ya no se puede anular.
- **Precios**: los fija la empresa (precio de lista y promociones vigentes). En caja no hay descuentos manuales.
- **Medios de pago**: efectivo (recibido y vuelto), tarjeta en el POS de la tienda (`POS_PROVEEDOR`; débito o crédito; N° de operación del voucher), Yape y Plin. El POS es **simulado**: la caja muestra "inserte su tarjeta / escanee el QR" y el cajero confirma o cancela el pago; el N° de operación lo genera ese POS de prueba. **Pago mixto** de hasta 3 medios; la suma debe ser exacta. Un N° de operación no puede usarse dos veces (evita capturas de Yape reutilizadas). Nunca se guarda el número de tarjeta.
- **Cliente**: documento (DNI, CE o RUC) obligatorio en toda venta. Con DNI o RUC el nombre se completa solo consultando RENIEC/SUNAT (`CONSULTA_TOKEN`); con CE, o si la consulta no está disponible, se escribe a mano.
- **Caja**: retiro parcial (sangría) con PIN cuando la gaveta supera el límite; **cuadre ciego** (el cajero cuenta antes de ver lo esperado); conciliación de tarjeta/QR/Yape/Plin contra el cierre del POS y los reportes de las apps; si hay diferencia se exige observación y PIN. Efectivo esperado = fondo + cobros en efectivo − devoluciones en efectivo − retiros.
- **Imágenes de producto**: campo `imagen_url` (URL https o foto subida como data URL, máx. ~1 MB). La columna se agrega sola en BD existentes (`app/services/schema.py`).
- **Ventas en tienda**: al registrar una venta se descuenta stock; al anularla se devuelve stock. El código correlativo es `V-000001`.

## API principal

| Método y ruta                              | Descripción                                                          |
| ------------------------------------------ | -------------------------------------------------------------------- |
| `POST /api/auth/login`                     | Ingreso único; si hay varios roles, `requiere_seleccion_rol: true`.  |
| `POST /api/auth/seleccionar-rol`           | Fija el rol activo y emite un nuevo JWT.                             |
| `GET  /api/auth/me`                        | Perfil del usuario y su rol activo.                                  |
| `GET  /api/roles`                          | Catálogo de roles.                                                   |
| `GET  /api/usuarios`                       | Lista de usuarios (permiso `usuarios.ver`).                          |
| `POST /api/usuarios`                       | Crea usuario, genera correo y devuelve la contraseña temporal.       |
| `PATCH /api/usuarios/:id`                  | Edita datos y roles del usuario.                                     |
| `POST /api/usuarios/:id/reset-password`    | Genera una nueva contraseña temporal.                                |
| `DELETE /api/usuarios/:id`                 | Marca al usuario como inactivo.                                      |
| `GET  /api/categorias`                     | Categorías del catálogo.                                             |
| `GET  /api/productos`                      | Catálogo (permiso `productos.ver`). Filtros: `q`, `categoria_id`.    |
| `POST /api/productos`                      | Crea producto (permiso `productos.crear`).                           |
| `PATCH /api/productos/:id`                 | Edita producto.                                                      |
| `POST /api/productos/:id/stock`            | Ajusta stock (`delta` o `stock` absoluto).                           |
| `GET  /api/ventas`                         | Últimas 200 ventas (permiso `ventas.ver`).                           |
| `POST /api/ventas`                         | Registra venta y descuenta stock (permiso `ventas.crear`).           |
| `POST /api/ventas/:id/anular`              | Anula venta y devuelve stock (`ventas.anular`, o cajero con `autorizacion_token`). |
| `GET  /api/autorizaciones/supervisores`    | Supervisores activos que pueden autorizar (para el modal del cajero). |
| `POST /api/autorizaciones`                 | Valida el PIN y devuelve el token de un solo uso.                    |
| `GET  /api/autorizaciones`                 | Bitácora de autorizaciones (permiso `caja.supervisar`).              |
| `PUT  /api/auth/pin`                       | El supervisor crea o cambia su PIN (exige su contraseña).            |
| `POST /api/cajas/retiro`                   | Retiro parcial de efectivo con `autorizacion_token`.                 |
| `POST /api/cajas/cerrar`                   | Arqueo + conciliación. Con diferencia responde 409 `diferencia_detectada` hasta recibir observación y token. |
| `GET  /api/ventas/reporte/resumen`         | Resumen operacional (permiso `reportes.ver`).                        |

## Despliegue en Render + Supabase

1. Crea un proyecto en **Supabase** y copia la cadena de conexión **Session Pooler** (menú Project Settings → Database → Connection string → Session pooler). Ejemplo:
   ```
   postgresql://postgres.abcdxyz:TU_PASSWORD@aws-0-us-east-1.pooler.supabase.com:5432/postgres
   ```
   No uses el "Direct connection" ni el "Transaction pooler" desde Render — el primero suele quedar filtrado por el firewall gratuito y el segundo no soporta prepared statements por defecto.
2. Sube este directorio como repositorio independiente `coolbox-backend` a GitHub.
3. En Render, elige **New → Blueprint** e importa `render.yaml`. Render solo pedirá el valor de `DATABASE_URL` (que copiaste de Supabase).
4. Espera al primer despliegue: Render ejecuta `pip install`, arranca `gunicorn` y el backend crea las tablas y carga el catálogo maestro automáticamente en la primera petición.
5. Cuando el frontend esté publicado en Vercel, actualiza `FRONTEND_ORIGIN` con esa URL exacta (por ejemplo `https://coolbox-frontend.vercel.app`) y **Manual Deploy → Deploy latest commit** para refrescar CORS.

### Errores comunes al desplegar (y cómo se resolvieron)

- **`ModuleNotFoundError: No module named 'app'` durante preDeploy** — se movió el seed al *app factory* (`app/__init__.py`), así corre dentro del contexto de Flask, no como script suelto.
- **`psycopg2` faltante** — se usa `psycopg[binary] 3.x` (más moderno y con wheels para Python 3.12), y `config.database_url()` traduce `postgres://` y `postgresql://` a `postgresql+psycopg://`.
- **CORS bloqueado** — el after-request añade siempre los encabezados y se maneja explícitamente `OPTIONS` antes de llegar al blueprint.
- **Timeouts de Supabase** — `SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}` renueva conexiones muertas.
- **Health check en `/`** — se agregó `GET /` para que Render deje de marcar la app como inactiva; el health-check oficial sigue en `/health`.


## Novedades v2.0

**SKU de 8 dígitos** (`app/services/catalogo.py`): `FF SS MM CC` = familia +
subfamilia + marca + correlativo. Ej. `10020401` = Laptops (10) · Laptops gamer
(02) · HP (04) · primer producto (01). Se genera al registrar el producto y es
inmutable. Endpoints: `GET /api/familias`, `GET/POST /api/marcas`,
`GET /api/productos/sku-preview`.

| Familia | Código | Subfamilias |
| --- | --- | --- |
| Laptops | 10 | 01 Uso personal y oficina · 02 Gamer · 03 Ultradelgadas y premium |
| Celulares | 20 | 01 Gama de entrada · 02 Gama media · 03 Gama alta |
| Tablets | 30 | 01 Tablets · 02 Para niños · 03 Tabletas gráficas |
| Televisores | 40 | 01 LED HD/FHD · 02 LED 4K · 03 QLED/NanoCell/QNED · 04 Mini LED/OLED |
| Audio | 50 | 01 On ear · 02 True wireless · 03 Parlantes portátiles · 04 Barras de sonido |
| Smartwatch y Wearables | 60 | 01 Smartwatch · 02 Smartband |
| Gamer | 70 | 01 Consolas · 02 Consolas portátiles · 03 Mandos y accesorios · 04 Sillas gamer |
| Accesorios de Cómputo | 80 | 01 Mouse · 02 Teclados · 03 Monitores · 04 Headsets |

**Kardex** (`movimiento_stock`): cada cambio de stock (stock inicial, entrada,
salida, ajuste por conteo, venta y anulación) guarda cantidad, saldo anterior y
nuevo, motivo y usuario. `POST /api/productos/<id>/stock` acepta
`{tipo, cantidad, motivo}`; `GET /api/productos/<id>/movimientos` y
`GET /api/movimientos`. Nuevo permiso `kardex.ver`.

**Seguridad de cuentas**: contraseña temporal obligatoria de cambiar en el
primer ingreso y tras un restablecimiento (`POST /api/auth/cambiar-password`),
política mínima (8+ caracteres con letras y números, distinta del DNI),
bloqueo de 10 min tras 5 intentos fallidos (`POST /api/usuarios/<id>/desbloquear`),
registro de último acceso, el administrador no puede desactivarse ni quitarse
su propio rol. DNI de 8 dígitos y celular de 9 dígitos validados.

**Ventas**: vuelto para pagos en efectivo, anulación con motivo obligatorio
(guarda quién y cuándo anuló), historial con filtros por fecha (hora de Lima),
estado, método de pago y vendedor, con paginación.

**Reportes** (`GET /api/reportes/resumen?desde&hasta`): total, IGV, ticket
promedio, ventas por día, por familia, por método de pago, por vendedor y top
10 de productos. `GET /api/reportes/inventario`: inventario valorizado y
productos por reponer.

**Migración automática**: al arrancar se agregan las columnas/tablas nuevas
sobre la BD existente de Supabase (no hay que tocar nada a mano).

## Novedades v3.0 — Módulo de Ventas y Caja

El sistema se concentra en la venta en tienda y el control de caja. Roles: **Administrador**, **Vendedor** (vende y cobra en su caja) y **Supervisor de Ventas** (controla cajas y anula ventas). Se retiró el rol Almacenero.

**Reglas de caja**
- El vendedor abre su caja con un fondo inicial en efectivo (S/ 0.00 a S/ 5,000.00). Solo puede tener una caja abierta.
- Sin caja abierta no se registran ventas; cada venta queda ligada a la caja que la cobró.
- Una caja de un día anterior no recibe ventas hasta cerrarse.
- Cierre con arqueo: se cuentan billetes y monedas; `diferencia = contado − esperado`, donde `esperado = fondo inicial + ventas en efectivo` (las anuladas no suman). Si hay sobrante o faltante, la observación es obligatoria.
- Una venta solo se anula (Supervisor o Administrador) mientras su caja siga abierta.

**Endpoints de caja**
| Método | Ruta | Permiso |
|---|---|---|
| GET | `/api/cajas/mi-caja` | caja.operar |
| POST | `/api/cajas/abrir` | caja.operar |
| POST | `/api/cajas/cerrar` | caja.operar |
| GET | `/api/cajas/mis-cajas` | caja.operar |
| GET | `/api/cajas/<id>` | dueño de la caja o caja.supervisar |
| GET | `/api/cajas` | caja.supervisar |

**Correcciones**
- Dos cajas registrando ventas a la vez ya no provocan error 500 (código provisional único).
- La anulación bloquea la venta: el stock no se devuelve dos veces.
- El login no revela qué correos existen y no cuenta intentos en cuentas desactivadas o bloqueadas.
- `POST /api/auth/liberar-rol`: "Cambiar rol" invalida el rol anterior.

## Novedades v4.0 — Tienda online y promociones
- **Tienda online** (`/api/tienda/*`, repositorio `coolbox_tienda`): catálogo público con SKU,
  inicio con promociones destacadas, venta cruzada, carrito con cupones y pedidos con recojo en
  tienda (26 sedes de Lima, la más cercana primero).
- **Clientes** (`/api/clientes/*`): registro con cualquier correo, inicio con Google
  (`GOOGLE_CLIENT_ID`) y código por correo (`SMTP_*`), ambos opcionales. Su token no sirve en el
  sistema de caja.
- **Promociones** (`/api/promociones`, solo Administrador): porcentaje, monto, precio especial,
  2x1/3x2/3x1/4x3, segunda unidad, por cantidad y cupón; por productos, marca, familia o todo;
  canal online, cajas o ambos; con fecha de inicio y fin. Se aplica la que más ahorra (no se acumulan).
  Las cajas también las aplican (`POST /api/ventas/cotizar`).
- **Pedidos online** (`/api/pedidos-online`): el personal confirma pagos de PagoEfectivo, marca
  pedidos listos, entrega validando el documento del titular o cancela (el stock vuelve).
- Pagos con tarjeta registran la marca: Visa, Mastercard, American Express o Diners Club.
- Catálogo ampliado a 145 productos (impresoras, Starlink, smart home, cargadores...).

## Novedades v5.0 — Procedimientos almacenados e integración de clientes

- **Procedimientos almacenados** (`db/procedimientos.sql`, PostgreSQL): el backend los instala al arrancar
  y los invoca por nombre con parámetros (`app/services/procedimientos.py`), sin armar SQL. Los errores de
  negocio del procedimiento llegan como respuestas HTTP con su mensaje.
- **Maestro de clientes** (`/api/maestro-clientes`): un registro por cliente con clave única tipo + número de
  documento. Se alimenta solo (triggers) de las ventas en caja y de las cuentas de la tienda online, y también
  se mantiene a mano o con la integración. La caja busca primero aquí antes de RENIEC/SUNAT.
- **Integración con el grupo pareado** (`/api/integracion`, ver `docs/API_CLIENTES.md`):
  - Ida: exponemos nuestros clientes con clave, paginados y con `?desde=` para pedir solo lo nuevo; también
    recibimos los que nos envíen por `POST`.
  - Vuelta: leemos su API (página por página, con reintentos 2 s, 4 s, 8 s), guardamos todo en la tabla de
    desembarco `cliente_stage` y pasamos al maestro solo lo válido y no repetido. No se pisan los datos que
    ya teníamos: solo se completan los vacíos.
  - Traza del API (`api_traza`): hora, sistema, resultado, intento, duración y cantidad de registros.
- **Devolución de pedidos online entregados** (`POST /api/pedidos-online/<id>/devoluciones`): dentro del
  plazo desde la entrega, con PIN de supervisor, reembolso al mismo medio de pago y nota de crédito (06 total,
  07 por ítem). El cliente ve y descarga la nota en "Mis compras".
- **Corrección**: en PostgreSQL las ventas en caja y los pedidos online fallaban al bloquear el stock
  (`FOR UPDATE` con la carga conjunta de familia/marca). Ahora se bloquea solo la tabla principal.

## Novedades v5.1 — Gestor documental de comprobantes y trazabilidad del API

Observaciones del profesor: "descarga todo lo que la SUNAT te entrega, guárdalo en una carpeta
estructurada y lee de ahí" y "mantengan la trazabilidad del API, no solo el caso feliz".

**Gestor documental** (`app/services/archivos.py`, pantalla *Comprobantes*):
- Al aceptarse un comprobante se descargan el PDF, el XML firmado y la **CDR** (constancia de
  recepción de la SUNAT) y se guardan en `comprobantes/<tipo>/<AAAA>/<MM>/<DD>/`
  (p. ej. `boletas/2026/11/17/B001-000037-CDR.zip`). El mensaje de la venta se toma de la CDR.
- Los botones PDF / XML / CDR leen la carpeta local: no se consulta la API de facturación.
- Copia en **Supabase Storage** (bucket privado `comprobantes`, misma ruta). Si el disco de Render
  se borró, el archivo se baja de la nube; si tampoco está, se vuelve a descargar de la API.
- Índice `comprobante_archivo` (procedimientos `sp_archivo_*` y `sp_comprobantes_*`): tipo, serie,
  cliente, venta o pedido de origen, tamaño y **huella SHA-256** para verificar que nadie alteró
  el archivo.
- Pantalla *Comprobantes* (Administrador y Supervisor): árbol tipo > año > mes > día, búsqueda por
  cliente o número, estado carpeta/nube por archivo y botón *Verificar*. El Administrador tiene
  *Sincronizar* (ordena rutas antiguas, restaura desde la nube y sube lo que falte).

**Integración**:
- `POST /api/integracion/probar` y botón *Probar conexión*: indica si la API del otro grupo está
  activa (queda en la traza). Si está caída, el maestro sigue funcionando con lo ya importado.
- Cada lectura correcta deja en la traza la huella SHA-256 de lo recibido.

Variables nuevas: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `COMPROBANTES_BUCKET`.

## Novedades v5.2 — Auditoría de seguridad y optimización

- **Dependencias sin vulnerabilidades conocidas**: Flask 3.1.3, Flask-Cors 6.0.5, requests 2.34.2,
  Flask-JWT-Extended 4.7.4, python-dotenv 1.2.4 (`pip-audit` limpio). Frontends: Vite 6.4,
  React Router 7.18 (`npm audit`: 0 vulnerabilidades).
- **Sesiones**: al cambiar o resetear la contraseña, los tokens anteriores dejan de valer
  (claim `sv`); quien hizo el cambio recibe un token nuevo y las demás sesiones se cierran.
- **Login con Google**: se valida también el emisor (`iss`) y que vengan `email` y `sub`.
- **Búsquedas**: los comodines `%` y `_` se escapan (`patron_like` en Python y `sp_like` en SQL).
- **Archivos externos**: límite de tamaño para PDF/XML/CDR (15 MB) y para la respuesta del otro
  grupo (`INTEGRACION_MAX_MB`); la lectura de la CDR ignora entradas gigantes (zip bomb).
- **Mensajes**: si la API de facturación falla, el usuario ve un mensaje claro (sin hosts ni
  trazas internas); el detalle queda en el log. Reintentos con espera creciente (`SUNAT_ESPERA`).
- **Excepciones**: `raise ... from None` en validaciones (sin trazas encadenadas).
- **Frontends**: cabeceras de seguridad en Vercel (nosniff, X-Frame-Options, HSTS, Referrer y
  Permissions-Policy); los XML se abren como texto plano (no pueden ejecutar scripts); carga
  diferida de pantallas (bundle inicial 413 KB → 225 KB).
