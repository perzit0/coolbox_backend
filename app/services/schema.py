"""Migraciones ligeras para bases ya creadas.

`db.create_all()` crea tablas nuevas pero no agrega columnas a tablas que ya
existen (como la BD de Supabase en producción). Aquí se agregan las columnas
nuevas de forma idempotente, funcionando tanto en PostgreSQL como en SQLite.
"""
from sqlalchemy import inspect, text

from ..extensions import db

# tabla -> {columna: definición SQL}
COLUMNAS_NUEVAS = {
    "producto": {"imagen_url": "TEXT"},
}


def aplicar_migraciones_ligeras():
    inspector = inspect(db.engine)
    tablas = set(inspector.get_table_names())
    for tabla, columnas in COLUMNAS_NUEVAS.items():
        if tabla not in tablas:
            continue
        existentes = {c["name"] for c in inspector.get_columns(tabla)}
        for columna, definicion in columnas.items():
            if columna not in existentes:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}"))
