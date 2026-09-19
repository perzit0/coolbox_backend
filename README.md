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
maestro (permisos, roles, categorías, 20 productos reales y usuarios demo).
El endpoint de salud está expuesto en `GET /health`.

Usuarios demo:

- Administrador — `admin@coolbox.com.pe` / `Admin123!`
- Vendedor — `jperezl@coolbox.com.pe` / `Vendedor123!` (en una BD nueva también tiene el rol Almacenero, para probar la selección de rol)
- Supervisor de Ventas — `mtorresr@coolbox.com.pe` / `Supervisor123!`

## Variables de entorno

| Variable          | Uso                                                                          |
| ----------------- | ---------------------------------------------------------------------------- |
| `DATABASE_URL`    | Cadena de conexión Postgres. En Supabase usa el **Session Pooler**.          |
| `SECRET_KEY`      | Clave interna de Flask.                                                      |
| `JWT_SECRET_KEY`  | Firma independiente de tokens JWT.                                           |
| `FRONTEND_ORIGIN` | URL exacta del frontend Vercel. Acepta varias separadas por coma.            |
| `EMAIL_DOMAIN`    | Dominio institucional (por defecto `coolbox.com.pe`).                         |
| `IGV_RATE`        | Tasa del IGV para descomposición del total (por defecto `0.18`).             |

## Reglas del negocio implementadas

- **Correo automático**: `Juan Daniel Pérez Lozano → jperezl@coolbox.com.pe`. Si ya existe, se agrega un contador (`jperezl1`, `jperezl2`, ...). Ver `app/services/email_generator.py`.
- **Login único**: `POST /api/auth/login` para todo el personal. Con un solo rol, el rol queda activo al ingresar; con varios roles (el Administrador incluido) se elige en `POST /api/auth/seleccionar-rol`.
- **Selección de rol**: al iniciar sesión, el usuario elige uno de sus roles (`POST /api/auth/seleccionar-rol`). El rol activo viaja en el JWT y controla qué endpoints puede usar.
- **Roles predefinidos**: Administrador, Vendedor, Almacenero, Supervisor de Ventas. Un usuario puede tener varios roles asignados. El rol Cajero se retiró (el Vendedor cobra): al arrancar, el seed lo elimina y reasigna sus usuarios y ventas a Vendedor.
- **Anulación de ventas**: solo Supervisor de Ventas y Administrador (`ventas.anular`).
- **Ventas sin DNI/RUC**: solo se guarda un nombre de cliente opcional (no hay validación con RENIEC/SUNAT).
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
| `POST /api/ventas/:id/anular`              | Anula venta y devuelve stock (permiso `ventas.anular`).              |
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
