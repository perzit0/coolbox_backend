"""Migraciones ligeras para bases ya creadas.

`db.create_all()` crea tablas nuevas pero no agrega columnas a tablas que ya
existen (como la BD de Supabase en producción). Aquí se agregan las columnas
e índices nuevos de forma idempotente, funcionando tanto en PostgreSQL como
en SQLite.
"""

from sqlalchemy import inspect, text

from ..extensions import db


def _tipos(dialecto):
    fecha = "TIMESTAMP WITH TIME ZONE" if dialecto == "postgresql" else "DATETIME"
    return {
        "fecha": fecha,
        "bool_false": "BOOLEAN NOT NULL DEFAULT FALSE",
    }


def _columnas_nuevas(dialecto):
    t = _tipos(dialecto)
    # tabla -> {columna: definición SQL}
    return {
        "categoria": {"codigo": "VARCHAR(2)"},
        "producto": {
            "imagen_url": "TEXT",
            "sku": "VARCHAR(8)",
            "subfamilia_id": "INTEGER",
            "marca_id": "INTEGER",
        },
        "usuario": {
            "debe_cambiar_password": t["bool_false"],
            "intentos_fallidos": "INTEGER NOT NULL DEFAULT 0",
            "bloqueado_hasta": t["fecha"],
            "ultimo_acceso": t["fecha"],
            "pin_hash": "VARCHAR(255)",
            "pin_intentos": "INTEGER NOT NULL DEFAULT 0",
            "pin_bloqueado_hasta": t["fecha"],
        },
        "venta": {
            "monto_recibido": "NUMERIC(10, 2)",
            "vuelto": "NUMERIC(10, 2)",
            "anulada_por_id": "INTEGER",
            "fecha_anulacion": t["fecha"],
            "motivo_anulacion": "VARCHAR(240)",
            "caja_id": "INTEGER",
            "cliente_documento": "VARCHAR(15)",
            "descuento": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
            "autorizada_por_id": "INTEGER",
            "tipo_comprobante": "VARCHAR(10)",
            "comprobante_serie": "VARCHAR(10)",
            "comprobante_numero": "VARCHAR(20)",
            "comprobante_api_id": "INTEGER",
            "comprobante_pdf": "VARCHAR(300)",
            "comprobante_xml": "VARCHAR(300)",
            "estado_sunat": "VARCHAR(15) NOT NULL DEFAULT 'pendiente'",
            "sunat_mensaje": "VARCHAR(300)",
            "sunat_intentos": "INTEGER NOT NULL DEFAULT 0",
        },
        "venta_detalle": {
            "descuento": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
            "promocion_id": "INTEGER",
            "promocion_nombre": "VARCHAR(120)",
        },
        "venta_pago": {
            "marca_tarjeta": "VARCHAR(20)",
        },
        "caja": {
            "conciliacion_json": "TEXT",
            "autorizacion_cierre_id": "INTEGER",
        },
    }


INDICES_UNICOS = [
    ("uq_producto_sku", "producto", "sku"),
    ("uq_categoria_codigo", "categoria", "codigo"),
]


def aplicar_migraciones_ligeras():
    dialecto = db.engine.dialect.name
    inspector = inspect(db.engine)
    tablas = set(inspector.get_table_names())

    for tabla, columnas in _columnas_nuevas(dialecto).items():
        if tabla not in tablas:
            continue
        existentes = {c["name"] for c in inspector.get_columns(tabla)}
        for columna, definicion in columnas.items():
            if columna not in existentes:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}"))

    # Índices únicos (permiten varios NULL tanto en PostgreSQL como en SQLite).
    # Solo se crean si la columna aún no tiene una restricción única propia.
    inspector = inspect(db.engine)
    for nombre, tabla, columna in INDICES_UNICOS:
        if tabla not in tablas:
            continue
        unicos = [u["column_names"] for u in inspector.get_unique_constraints(tabla)]
        unicos += [i["column_names"] for i in inspector.get_indexes(tabla) if i.get("unique")]
        if [columna] not in unicos:
            with db.engine.begin() as conn:
                conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {nombre} ON {tabla} ({columna})"))

    _activar_rls(dialecto)


def _activar_rls(dialecto):
    """Supabase publica las tablas de `public` por su API REST. Con RLS activo y sin políticas,
    esa API no puede leer ni escribir nada. El backend se conecta como dueño de las tablas,
    así que no le afecta. Es idempotente: solo actúa en tablas que aún no tienen RLS."""
    if dialecto != "postgresql":
        return
    with db.engine.begin() as conn:
        tablas = (
            conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND NOT rowsecurity"))
            .scalars()
            .all()
        )
        for tabla in tablas:
            conn.execute(text(f'ALTER TABLE public."{tabla}" ENABLE ROW LEVEL SECURITY'))
