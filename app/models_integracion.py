"""Maestro de clientes e integración de clientes con el grupo pareado.

- MaestroCliente: un registro por cliente, único por tipo + número de documento
  (recomendación del profesor: el DNI solo no basta, una persona y una empresa pueden
  coincidir en el número). Se alimenta de la caja, de la tienda online, del
  mantenimiento manual y de la integración. Las escrituras se hacen con los
  procedimientos almacenados de db/procedimientos.sql.
- IntegracionLote: cada carga de clientes recibida del otro grupo.
- ClienteStage: tabla de desembarco (stage). Guarda tal cual cada registro recibido y,
  al procesarlo, el resultado: insertado, actualizado, sin cambios, duplicado o rechazado.
- ApiTraza: traza de cada llamada a la API de clientes, entrante o saliente
  (hora, quién, resultado, intento, duración y cantidad de registros).
"""

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from .extensions import db
from .models import ahora, iso

TIPOS_DOCUMENTO = ("DNI", "CE", "RUC", "PASAPORTE")
ORIGENES = ("caja", "tienda", "manual", "integracion")

# JSONB en PostgreSQL (Supabase); JSON simple en SQLite para las pruebas locales.
JSON_DOC = db.JSON().with_variant(JSONB(), "postgresql")


class MaestroCliente(db.Model):
    __tablename__ = "maestro_cliente"
    __table_args__ = (
        UniqueConstraint("tipo_documento", "nro_documento", name="uq_maestro_cliente_documento"),
        CheckConstraint("tipo_documento IN ('DNI', 'CE', 'RUC', 'PASAPORTE')", name="ck_maestro_cliente_tipo"),
        CheckConstraint("origen IN ('caja', 'tienda', 'manual', 'integracion')", name="ck_maestro_cliente_origen"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tipo_documento = db.Column(db.String(10), nullable=False)
    nro_documento = db.Column(db.String(15), nullable=False)
    nombres = db.Column(db.String(120))
    apellidos = db.Column(db.String(160))
    razon_social = db.Column(db.String(200))  # empresas (RUC)
    email = db.Column(db.String(160))
    telefono = db.Column(db.String(20))
    direccion = db.Column(db.String(240))
    origen = db.Column(db.String(15), nullable=False, default="manual")
    origen_detalle = db.Column(db.String(80))  # p. ej. "Grupo 5" o el código de la venta
    activo = db.Column(db.Boolean, nullable=False, default=True)
    fecha_registro = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)
    fecha_actualizacion = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora, index=True)


class IntegracionLote(db.Model):
    __tablename__ = "integracion_lote"
    id = db.Column(db.Integer, primary_key=True)
    origen = db.Column(db.String(80), nullable=False)  # sistema que envía los clientes
    modo = db.Column(db.String(15), nullable=False)  # consulta | recepcion | archivo
    url = db.Column(db.String(300))
    estado = db.Column(db.String(15), nullable=False, default="en_proceso")  # en_proceso | completado | error
    intentos = db.Column(db.Integer, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)
    insertados = db.Column(db.Integer, nullable=False, default=0)
    actualizados = db.Column(db.Integer, nullable=False, default=0)
    sin_cambios = db.Column(db.Integer, nullable=False, default=0)
    duplicados = db.Column(db.Integer, nullable=False, default=0)
    rechazados = db.Column(db.Integer, nullable=False, default=0)
    mensaje = db.Column(db.String(300))
    usuario_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    fecha_inicio = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora, index=True)
    fecha_fin = db.Column(db.DateTime(timezone=True))


class ClienteStage(db.Model):
    __tablename__ = "cliente_stage"
    id = db.Column(db.Integer, primary_key=True)
    lote_id = db.Column(db.Integer, ForeignKey("integracion_lote.id", ondelete="CASCADE"), nullable=False, index=True)
    fila = db.Column(db.Integer, nullable=False)
    payload = db.Column(JSON_DOC)  # registro tal como llegó
    tipo_documento = db.Column(db.String(20))
    nro_documento = db.Column(db.String(20))
    nombres = db.Column(db.String(200))
    apellidos = db.Column(db.String(200))
    razon_social = db.Column(db.String(200))
    email = db.Column(db.String(200))
    telefono = db.Column(db.String(40))
    direccion = db.Column(db.String(240))
    estado = db.Column(db.String(15), nullable=False, default="pendiente")
    mensaje = db.Column(db.String(200))
    cliente_id = db.Column(db.Integer, ForeignKey("maestro_cliente.id", ondelete="SET NULL"))
    fecha_carga = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)


class ApiTraza(db.Model):
    __tablename__ = "api_traza"
    id = db.Column(db.Integer, primary_key=True)
    direccion = db.Column(db.String(10), nullable=False)  # entrada | salida
    sistema = db.Column(db.String(80))  # quién llamó (entrada) o a quién se llamó (salida)
    metodo = db.Column(db.String(8), nullable=False)
    endpoint = db.Column(db.String(300), nullable=False)
    estado_http = db.Column(db.Integer)
    exito = db.Column(db.Boolean, nullable=False, default=False)
    intento = db.Column(db.Integer, nullable=False, default=1)
    duracion_ms = db.Column(db.Integer)
    registros = db.Column(db.Integer)
    mensaje = db.Column(db.String(300))
    ip = db.Column(db.String(64))
    lote_id = db.Column(db.Integer, ForeignKey("integracion_lote.id", ondelete="SET NULL"))
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora, index=True)


def fila_a_dict(fila):
    """Convierte una fila devuelta por un procedimiento en un dict serializable."""
    datos = {}
    for clave, valor in dict(fila).items():
        datos[clave] = iso(valor) if hasattr(valor, "isoformat") and hasattr(valor, "hour") else valor
    return datos
