"""Gestor documental de comprobantes electrónicos.

Cada archivo que entrega la SUNAT por la API de facturación (PDF, XML firmado y CDR)
se descarga una sola vez, se guarda en la carpeta local con la estructura
<tipo>/<AAAA>/<MM>/<DD>/<serie-numero>.<ext> y se copia al almacenamiento en la nube
(Supabase Storage). Esta tabla es el índice de esa carpeta: qué archivo es, de qué
cliente, de qué venta o pedido, su tamaño y su huella SHA-256 para verificar que no
fue alterado. Las escrituras se hacen con sp_archivo_registrar (db/procedimientos.sql).
"""

from sqlalchemy import CheckConstraint

from .extensions import db
from .models import ahora, iso

TIPOS = ("boleta", "factura", "nota_credito")
FORMATOS = ("pdf", "xml", "cdr")


class ComprobanteArchivo(db.Model):
    __tablename__ = "comprobante_archivo"
    __table_args__ = (
        CheckConstraint("tipo IN ('boleta', 'factura', 'nota_credito')", name="ck_comprobante_archivo_tipo"),
        CheckConstraint("formato IN ('pdf', 'xml', 'cdr')", name="ck_comprobante_archivo_formato"),
    )

    id = db.Column(db.Integer, primary_key=True)
    ruta = db.Column(db.String(300), nullable=False, unique=True)  # relativa a COMPROBANTES_DIR
    tipo = db.Column(db.String(15), nullable=False)
    formato = db.Column(db.String(4), nullable=False)
    serie = db.Column(db.String(10))
    numero = db.Column(db.String(20))
    fecha_emision = db.Column(db.Date, nullable=False, index=True)
    cliente_documento = db.Column(db.String(15), index=True)
    cliente_nombre = db.Column(db.String(200))
    origen = db.Column(db.String(20))  # venta, devolucion, pedido, pedido_devolucion
    origen_codigo = db.Column(db.String(20))  # V-000001, D-000001, P-000001, DW-000001
    bytes = db.Column(db.Integer, nullable=False, default=0)
    sha256 = db.Column(db.String(64), nullable=False)
    en_nube = db.Column(db.Boolean, nullable=False, default=False)
    fecha_guardado = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)

    def to_dict(self):
        return {
            "id": self.id,
            "ruta": self.ruta,
            "nombre": self.ruta.rsplit("/", 1)[-1],
            "tipo": self.tipo,
            "formato": self.formato,
            "comprobante": f"{self.serie}-{self.numero}" if self.serie and self.numero else None,
            "fecha_emision": self.fecha_emision.isoformat() if self.fecha_emision else None,
            "cliente_documento": self.cliente_documento,
            "cliente_nombre": self.cliente_nombre,
            "origen": self.origen,
            "origen_codigo": self.origen_codigo,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "en_nube": self.en_nube,
            "fecha_guardado": iso(self.fecha_guardado),
        }


class RespaldoEquipo(db.Model):
    """PC que guarda la copia local de los comprobantes (agente de respaldo)."""

    __tablename__ = "respaldo_equipo"
    id = db.Column(db.Integer, primary_key=True)
    equipo = db.Column(db.String(80), nullable=False, unique=True)  # nombre de la PC
    carpeta = db.Column(db.String(200))  # carpeta local donde guarda
    archivos = db.Column(db.Integer, nullable=False, default=0)  # archivos que tiene
    bytes = db.Column(db.BigInteger, nullable=False, default=0)
    descargados = db.Column(db.Integer, nullable=False, default=0)  # en la última pasada
    pendientes = db.Column(db.Integer, nullable=False, default=0)
    errores = db.Column(db.Integer, nullable=False, default=0)
    ultimo_contacto = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)
