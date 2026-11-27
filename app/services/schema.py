"""Migraciones ligeras para bases ya creadas.

`db.create_all()` crea tablas nuevas pero no agrega columnas a tablas que ya
existen (como la BD de Supabase en producción). Aquí se agregan las columnas
e índices nuevos de forma idempotente, funcionando tanto en PostgreSQL como
en SQLite. En PostgreSQL además se instalan los procedimientos almacenados.
"""

from pathlib import Path

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
            "comprobante_cdr": "VARCHAR(300)",
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
        # Constancia de recepción (CDR) de la SUNAT, guardada junto al PDF y el XML
        "devolucion": {"comprobante_cdr": "VARCHAR(300)"},
        "pedido_comprobante": {"comprobante_cdr": "VARCHAR(300)"},
        "pedido_devolucion": {"comprobante_cdr": "VARCHAR(300)"},
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


ARCHIVO_PROCEDIMIENTOS = Path(__file__).resolve().parents[2] / "db" / "procedimientos.sql"


def instalar_procedimientos():
    """Crea los procedimientos almacenados, triggers y RLS de db/procedimientos.sql.

    El archivo se ejecuta completo en una sola transacción: si algo falla, quedan los
    procedimientos anteriores. SQLite (pruebas locales) no tiene procedimientos.
    """
    if db.engine.dialect.name != "postgresql":
        return False
    contenido = ARCHIVO_PROCEDIMIENTOS.read_text(encoding="utf-8")
    conexion = db.engine.raw_connection()
    try:
        with conexion.cursor() as cursor:
            cursor.execute(contenido)
        conexion.commit()
        return True
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()
