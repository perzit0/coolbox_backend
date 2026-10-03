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
        },
        "venta": {
            "monto_recibido": "NUMERIC(10, 2)",
            "vuelto": "NUMERIC(10, 2)",
            "anulada_por_id": "INTEGER",
            "fecha_anulacion": t["fecha"],
            "motivo_anulacion": "VARCHAR(240)",
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
