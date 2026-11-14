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
maestro (permisos, roles, familias/subfamilias/marcas, 39 productos reales de
coolbox.pe con su imagen oficial y SKU, y usuarios demo). Si la BD ya tenía
la versión anterior, los 20 productos antiguos (LAP001, CEL001...) se
convierten en su equivalente real conservando stock y ventas.
Endpoints de salud: `GET /health` y `GET /health/db` (prueba la conexión a la BD).

Usuarios demo:

- Administrador — `admin@coolbox.com.pe` / `Admin123!`
- Cajero — `jperezl@coolbox.com.pe` / `Vendedor123!`
- Supervisor de Caja — `mtorresr@coolbox.com.pe` / `Supervisor123!` · **PIN 482615** (en una BD nueva también tiene el rol Cajero, para probar la selección de rol)
- El Administrador demo tiene **PIN 739142**. Los PIN demo solo se asignan si el usuario aún no tiene uno; cada supervisor puede cambiarlo en Mi cuenta.

## Variables de entorno

| Variable          | Uso                                                                          |
| ----------------- | ---------------------------------------------------------------------------- |
| `DATABASE_URL`    | Cadena de conexión Postgres. En Supabase usa el **Session Pooler**.          |
| `SECRET_KEY`      | Clave interna de Flask.                                                      |
| `JWT_SECRET_KEY`  | Firma independiente de tokens JWT.                                           |
| `FRONTEND_ORIGIN` | URL exacta del frontend Vercel. Acepta varias separadas por coma.            |
| `EMAIL_DOMAIN`    | Dominio institucional (por defecto `coolbox.com.pe`).                         |
| `IGV_RATE`        | Tasa del IGV para descomposición del total (por defecto `0.18`).             |
| `CUADRE_CIEGO`    | `1` (defecto): el cajero no ve el efectivo esperado hasta cerrar. `0` lo muestra. |
| `LIMITE_EFECTIVO_CAJA` | Efectivo en gaveta que dispara el aviso de retiro parcial (defecto `1500`). |
| `DESCUENTO_MAX_CAJERO` | % de descuento que el cajero aplica sin PIN (defecto `5`).              |
| `DESCUENTO_MAX_TOTAL`  | % máximo de descuento aun con PIN (defecto `30`).                        |
| `MONTO_BOLETA_CON_DNI` | Total desde el que se exige DNI/CE/RUC del cliente (defecto `700`, SUNAT). |

## Reglas del negocio implementadas

- **Correo automático**: `Juan Daniel Pérez Lozano → jperezl@coolbox.com.pe`. Si ya existe, se agrega un contador (`jperezl1`, `jperezl2`, ...). Ver `app/services/email_generator.py`.
- **Login único**: `POST /api/auth/login` para todo el personal. Con un solo rol, el rol queda activo al ingresar; con varios roles (el Administrador incluido) se elige en `POST /api/auth/seleccionar-rol`.
- **Selección de rol**: al iniciar sesión, el usuario elige uno de sus roles (`POST /api/auth/seleccionar-rol`). El rol activo viaja en el JWT y controla qué endpoints puede usar.
- **Roles predefinidos**: Administrador, **Cajero** y **Supervisor de Caja**. Un usuario puede tener varios roles asignados. Los códigos internos siguen siendo `vendedor` y `supervisor` para no romper usuarios ni ventas existentes; solo cambió el nombre visible. Almacenero se retiró (entradas, salidas y kardex quedan en el Administrador).
- **Autorización con PIN en la caja** (`app/services/autorizacion.py`): el cajero no sale de su sesión. El supervisor elige su nombre, escribe su PIN (exactamente 6 dígitos, guardado con hash) y elige un motivo. La API entrega un token de **un solo uso, válido 90 s**, solo para esa acción (y esa venta). Tres PIN errados bloquean el PIN de ese supervisor 5 minutos. Nadie puede autorizarse a sí mismo. Todo intento queda en la bitácora `autorizacion`.
- **Acciones que piden PIN**: retirar o restar un producto ya registrado en el carrito, cancelar la venta en curso, descuento mayor al 5 %, anular una venta cobrada (desde la caja del cajero), retiro parcial de efectivo, reimpresión de un comprobante y cierre de caja con diferencia.
- **Anulación de ventas**: el Supervisor de Caja y el Administrador anulan directamente (`ventas.anular`); el Cajero anula ventas de **su** caja abierta con el PIN de un supervisor.
- **Medios de pago reales**: efectivo (recibido y vuelto), tarjeta en POS (OpenPay, Niubiz, Izipay, Culqi; débito o crédito; N° de operación del voucher), QR del POS, Yape y Plin directos (N° de operación). **Pago mixto** de hasta 3 medios; la suma debe ser exacta. Un N° de operación no puede usarse dos veces (evita capturas de Yape reutilizadas). Nunca se guarda el número de tarjeta.
- **Cliente**: nombre y documento opcionales; para ventas mayores a S/ 700 se exige DNI, CE o RUC (regla de SUNAT para boletas).
- **Caja**: retiro parcial (sangría) con PIN cuando la gaveta supera el límite; **cuadre ciego** (el cajero cuenta antes de ver lo esperado); conciliación de tarjeta/QR/Yape/Plin contra el cierre del POS y los reportes de las apps; si hay diferencia se exige observación y PIN. Efectivo esperado = fondo + cobros en efectivo − retiros.
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
