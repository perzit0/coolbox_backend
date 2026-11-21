"""Invocación de los procedimientos almacenados de db/procedimientos.sql.

El código no arma consultas SQL: solo indica el nombre del procedimiento y sus
parámetros. SQLAlchemy los envía como parámetros enlazados (bind parameters), así
que ningún dato del usuario se mezcla con el texto de la consulta.

Los errores de negocio que lanza un procedimiento (sp_error) llegan con el código en
MESSAGE, el texto para el usuario en DETAIL y el estado HTTP en HINT; aquí se
convierten en ApiError.
"""

import json

from sqlalchemy import func, literal, literal_column, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError

from ..errors import ApiError
from ..extensions import db

# Solo se pueden invocar los procedimientos de esta lista.
PERMITIDOS = frozenset(
    {
        "sp_cliente_registrar",
        "sp_cliente_guardar",
        "sp_cliente_estado",
        "sp_cliente_obtener",
        "sp_cliente_buscar",
        "sp_clientes_listar",
        "sp_clientes_resumen",
        "sp_clientes_exportar",
        "sp_maestro_sincronizar",
        "sp_lote_crear",
        "sp_lote_cerrar",
        "sp_lote_intentos",
        "sp_lote_obtener",
        "sp_lotes_listar",
        "sp_stage_cargar",
        "sp_stage_procesar",
        "sp_stage_listar",
        "sp_traza_registrar",
        "sp_traza_listar",
        "sp_pedido_devolver",
    }
)


def disponibles():
    """Los procedimientos existen solo en PostgreSQL (Supabase)."""
    return db.engine.dialect.name == "postgresql"


def exigir():
    if not disponibles():
        raise ApiError(
            "requiere_postgresql",
            "Esta función usa procedimientos almacenados: configure DATABASE_URL con PostgreSQL (Supabase).",
            503,
        )


def json_param(valor):
    """Parámetro de tipo jsonb (SQLAlchemy lo serializa a JSON)."""
    return literal(json.loads(json.dumps(valor, default=str)), JSONB)


def _llamada(nombre, args):
    if nombre not in PERMITIDOS:
        raise ValueError(f"Procedimiento no permitido: {nombre}")
    return getattr(func, nombre)(*args)


def _ejecutar(consulta, sesion):
    try:
        return sesion.execute(consulta)
    except DBAPIError as exc:
        if sesion is db.session:  # una conexión propia (engine.begin) se revierte sola
            sesion.rollback()
        diag = getattr(exc.orig, "diag", None)
        if getattr(exc.orig, "sqlstate", None) == "P0001" and diag is not None:
            estado = int(diag.message_hint) if (diag.message_hint or "").isdigit() else 409
            raise ApiError(
                diag.message_primary or "error_negocio", diag.message_detail or "Operación no válida.", estado
            )
        raise


def valor(nombre, *args, sesion=None):
    """Procedimiento que devuelve un solo valor (id, conteo...)."""
    return _ejecutar(select(_llamada(nombre, args)), sesion or db.session).scalar()


def filas(nombre, *args, sesion=None):
    """Procedimiento que devuelve filas: lista de dicts (columna -> valor)."""
    consulta = select(literal_column("*")).select_from(_llamada(nombre, args))
    return [dict(f) for f in _ejecutar(consulta, sesion or db.session).mappings().all()]


def fila(nombre, *args, sesion=None):
    resultado = filas(nombre, *args, sesion=sesion)
    return resultado[0] if resultado else None


def columna(nombre, campo, *args, sesion=None):
    """Lista con el valor de una columna de cada fila (p. ej. los jsonb de un listado)."""
    return [f[campo] for f in filas(nombre, *args, sesion=sesion)]
