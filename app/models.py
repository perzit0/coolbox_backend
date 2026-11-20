"""Modelos de dominio Coolbox.

Diseñado para tienda física con proyección a venta en línea. Un usuario
puede tener uno o varios roles (Administrador, Cajero,
Supervisor de Caja). Cada rol otorga permisos concretos. Todos ingresan por
el mismo login; si el usuario tiene más de un rol, elige con cuál trabajar
(el rol Administrador se elige igual que los demás) y ese rol define las
acciones que puede realizar durante la sesión.

Catálogo: Familia -> Subfamilia, más Marca. El SKU de 8 dígitos se arma con
esos códigos (ver app/services/catalogo.py).
"""
from datetime import datetime, date, timezone
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .services.catalogo import descomponer_sku


def ahora():
    """Fecha/hora actual en UTC (con zona horaria)."""
    return datetime.now(timezone.utc)


def iso(dt):
    """Serializa fechas como ISO-8601 en UTC (SQLite las devuelve sin zona)."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# Tabla puente Usuario <-> Rol (relación N a N)
usuario_rol = db.Table(
    "usuario_rol",
    db.Column("usuario_id", db.Integer, ForeignKey("usuario.id", ondelete="CASCADE"), primary_key=True),
    db.Column("rol_id", db.Integer, ForeignKey("rol.id", ondelete="CASCADE"), primary_key=True),
)

# Tabla puente Rol <-> Permiso
rol_permiso = db.Table(
    "rol_permiso",
    db.Column("rol_id", db.Integer, ForeignKey("rol.id", ondelete="CASCADE"), primary_key=True),
    db.Column("permiso_id", db.Integer, ForeignKey("permiso.id", ondelete="CASCADE"), primary_key=True),
)


class Permiso(db.Model):
    """Permiso atómico que un rol puede otorgar."""
    __tablename__ = "permiso"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(60), nullable=False, unique=True)
    descripcion = db.Column(db.String(200), nullable=False)

    def to_dict(self):
        return {"id": self.id, "codigo": self.codigo, "descripcion": self.descripcion}


class Rol(db.Model):
    """Rol funcional dentro de Coolbox."""
    __tablename__ = "rol"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(40), nullable=False, unique=True)
    nombre = db.Column(db.String(80), nullable=False)
    descripcion = db.Column(db.String(240), nullable=False)
    es_admin = db.Column(db.Boolean, nullable=False, default=False)

    permisos = relationship("Permiso", secondary=rol_permiso, backref="roles")

    def to_dict(self, include_permisos=True):
        data = {
            "id": self.id,
            "codigo": self.codigo,
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "es_admin": self.es_admin,
        }
        if include_permisos:
            data["permisos"] = sorted(p.codigo for p in self.permisos)
        return data


class Usuario(db.Model):
    """Personal de la tienda. El correo se genera automáticamente."""
    __tablename__ = "usuario"
    id = db.Column(db.Integer, primary_key=True)
    dni = db.Column(db.String(15), nullable=False, unique=True)
    nombres = db.Column(db.String(120), nullable=False)
    apellido_paterno = db.Column(db.String(80), nullable=False)
    apellido_materno = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(180), nullable=False, unique=True)
    telefono = db.Column(db.String(20))
    direccion = db.Column(db.String(200))
    password_hash = db.Column(db.String(255), nullable=False)
    estado = db.Column(db.String(12), nullable=False, default="activo")  # activo | inactivo
    fecha_ingreso = db.Column(db.Date, nullable=False, default=date.today)
    creado_en = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)
    # Seguridad de la cuenta
    debe_cambiar_password = db.Column(db.Boolean, nullable=False, default=False)
    intentos_fallidos = db.Column(db.Integer, nullable=False, default=0)
    bloqueado_hasta = db.Column(db.DateTime(timezone=True))
    ultimo_acceso = db.Column(db.DateTime(timezone=True))
    # PIN de supervisor: autoriza acciones restringidas en la caja de otro usuario
    pin_hash = db.Column(db.String(255))
    pin_intentos = db.Column(db.Integer, nullable=False, default=0)
    pin_bloqueado_hasta = db.Column(db.DateTime(timezone=True))

    roles = relationship("Rol", secondary=usuario_rol, backref="usuarios")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    def set_pin(self, raw):
        self.pin_hash = generate_password_hash(raw)
        self.pin_intentos = 0
        self.pin_bloqueado_hasta = None

    def check_pin(self, raw):
        return bool(self.pin_hash) and check_password_hash(self.pin_hash, raw or "")

    @property
    def puede_autorizar(self):
        """Tiene algún rol con el permiso caja.autorizar (Supervisor de Caja o Administrador)."""
        return any(p.codigo == "caja.autorizar" for r in self.roles for p in r.permisos)

    @property
    def nombre_completo(self):
        return f"{self.nombres} {self.apellido_paterno} {self.apellido_materno}".strip()

    @property
    def es_administrador(self):
        return any(r.es_admin for r in self.roles)

    def to_dict(self, include_roles=True):
        data = {
            "id": self.id,
            "dni": self.dni,
            "nombres": self.nombres,
            "apellido_paterno": self.apellido_paterno,
            "apellido_materno": self.apellido_materno,
            "nombre_completo": self.nombre_completo,
            "email": self.email,
            "telefono": self.telefono,
            "direccion": self.direccion,
            "estado": self.estado,
            "fecha_ingreso": self.fecha_ingreso.isoformat() if self.fecha_ingreso else None,
            "es_administrador": self.es_administrador,
            "debe_cambiar_password": bool(self.debe_cambiar_password),
            "ultimo_acceso": iso(self.ultimo_acceso),
            "puede_autorizar": self.puede_autorizar,
            "tiene_pin": bool(self.pin_hash),
        }
        if include_roles:
            data["roles"] = [r.to_dict(include_permisos=True) for r in self.roles]
        return data


class Familia(db.Model):
    """Familia de productos (primer nivel del SKU). Tabla histórica `categoria`."""
    __tablename__ = "categoria"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(2), unique=True)  # "10", "20", ...
    nombre = db.Column(db.String(80), nullable=False, unique=True)
    descripcion = db.Column(db.String(200))

    productos = relationship("Producto", back_populates="familia")
    subfamilias = relationship("Subfamilia", back_populates="familia", order_by="Subfamilia.codigo")

    def to_dict(self, include_subfamilias=False):
        data = {"id": self.id, "codigo": self.codigo, "nombre": self.nombre, "descripcion": self.descripcion}
        if include_subfamilias:
            data["subfamilias"] = [s.to_dict() for s in self.subfamilias]
        return data


class Subfamilia(db.Model):
    """Subfamilia dentro de una familia (segundo nivel del SKU)."""
    __tablename__ = "subfamilia"
    __table_args__ = (UniqueConstraint("familia_id", "codigo", name="uq_subfamilia_codigo"),)
    id = db.Column(db.Integer, primary_key=True)
    familia_id = db.Column(db.Integer, ForeignKey("categoria.id", ondelete="CASCADE"), nullable=False)
    codigo = db.Column(db.String(2), nullable=False)
    nombre = db.Column(db.String(80), nullable=False)

    familia = relationship("Familia", back_populates="subfamilias")

    def to_dict(self):
        return {"id": self.id, "familia_id": self.familia_id, "codigo": self.codigo, "nombre": self.nombre}


class Marca(db.Model):
    """Marca comercial (tercer nivel del SKU)."""
    __tablename__ = "marca"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(2), nullable=False, unique=True)
    nombre = db.Column(db.String(80), nullable=False, unique=True)

    def to_dict(self):
        return {"id": self.id, "codigo": self.codigo, "nombre": self.nombre}


class Producto(db.Model):
    __tablename__ = "producto"
    __table_args__ = (
        CheckConstraint("precio >= 0", name="ck_producto_precio"),
        CheckConstraint("stock >= 0", name="ck_producto_stock"),
    )
    id = db.Column(db.Integer, primary_key=True)
    # SKU interno de 8 dígitos (familia + subfamilia + marca + correlativo)
    sku = db.Column(db.String(8), unique=True)
    # Código de referencia del fabricante / proveedor (p. ej. "HP15FC0025-T")
    codigo = db.Column(db.String(30), nullable=False, unique=True)
    nombre = db.Column(db.String(180), nullable=False)
    descripcion = db.Column(db.String(500))
    categoria_id = db.Column(db.Integer, ForeignKey("categoria.id", ondelete="RESTRICT"), nullable=False)
    subfamilia_id = db.Column(db.Integer, ForeignKey("subfamilia.id", ondelete="RESTRICT"))
    marca_id = db.Column(db.Integer, ForeignKey("marca.id", ondelete="RESTRICT"))
    precio = db.Column(db.Numeric(10, 2), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    stock_minimo = db.Column(db.Integer, nullable=False, default=5)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    # URL pública (https://...) o imagen subida como data URL (data:image/jpeg;base64,...)
    imagen_url = db.Column(db.Text)

    familia = relationship("Familia", back_populates="productos")
    subfamilia = relationship("Subfamilia")
    marca = relationship("Marca")

    @property
    def estado_stock(self):
        if self.stock <= 0:
            return "agotado"
        if self.stock <= self.stock_minimo:
            return "bajo"
        return "normal"

    def to_dict(self):
        return {
            "id": self.id,
            "sku": self.sku,
            "sku_partes": descomponer_sku(self.sku),
            "codigo": self.codigo,
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "familia_id": self.categoria_id,
            "familia": self.familia.nombre if self.familia else None,
            "subfamilia_id": self.subfamilia_id,
            "subfamilia": self.subfamilia.nombre if self.subfamilia else None,
            "marca_id": self.marca_id,
            "marca": self.marca.nombre if self.marca else None,
            # Compatibilidad con versiones anteriores del frontend
            "categoria_id": self.categoria_id,
            "categoria": self.familia.nombre if self.familia else None,
            "precio": float(self.precio),
            "stock": self.stock,
            "stock_minimo": self.stock_minimo,
            "estado_stock": self.estado_stock,
            "activo": self.activo,
            "imagen_url": self.imagen_url,
        }


class MovimientoStock(db.Model):
    """Kardex: cada cambio de stock queda registrado con su motivo y autor."""
    __tablename__ = "movimiento_stock"
    TIPOS = ("inicial", "entrada", "salida", "ajuste", "venta", "anulacion", "pedido_web", "cancel_web")

    id = db.Column(db.Integer, primary_key=True)
    producto_id = db.Column(db.Integer, ForeignKey("producto.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(12), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)  # positivo entra, negativo sale
    stock_anterior = db.Column(db.Integer, nullable=False)
    stock_nuevo = db.Column(db.Integer, nullable=False)
    motivo = db.Column(db.String(240))
    usuario_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    venta_id = db.Column(db.Integer, ForeignKey("venta.id", ondelete="SET NULL"))
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora, index=True)

    producto = relationship("Producto")
    usuario = relationship("Usuario")
    venta = relationship("Venta")

    def to_dict(self):
        return {
            "id": self.id,
            "producto_id": self.producto_id,
            "producto_sku": self.producto.sku if self.producto else None,
            "producto_nombre": self.producto.nombre if self.producto else None,
            "tipo": self.tipo,
            "cantidad": self.cantidad,
            "stock_anterior": self.stock_anterior,
            "stock_nuevo": self.stock_nuevo,
            "motivo": self.motivo,
            "usuario_nombre": self.usuario.nombre_completo if self.usuario else "Sistema",
            "venta_codigo": self.venta.codigo if self.venta else None,
            "fecha": iso(self.fecha),
        }


class Caja(db.Model):
    """Turno de caja de un cajero.

    El cajero abre su caja con un fondo inicial en efectivo, registra sus
    ventas en ella y al terminar el turno la cierra con un arqueo: cuenta el
    efectivo y el sistema calcula la diferencia contra lo esperado.
    """
    __tablename__ = "caja"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), nullable=False, unique=True)  # C-000001
    usuario_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False, index=True)
    estado = db.Column(db.String(10), nullable=False, default="abierta")  # abierta | cerrada
    fecha_apertura = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)
    monto_apertura = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    # Arqueo (se completa al cerrar)
    fecha_cierre = db.Column(db.DateTime(timezone=True))
    efectivo_esperado = db.Column(db.Numeric(10, 2))
    efectivo_contado = db.Column(db.Numeric(10, 2))
    diferencia = db.Column(db.Numeric(10, 2))
    conteo_json = db.Column(db.Text)  # {"200": 1, "100": 3, ...} denominaciones contadas
    observacion_cierre = db.Column(db.String(240))
    # Conciliación de medios electrónicos: {"tarjeta": {"sistema": x, "declarado": y, "diferencia": z}, ...}
    conciliacion_json = db.Column(db.Text)
    autorizacion_cierre_id = db.Column(db.Integer)  # si cerró con diferencia, quién lo autorizó

    usuario = relationship("Usuario")
    ventas = relationship("Venta", back_populates="caja", order_by="Venta.id")
    movimientos = relationship("MovimientoCaja", back_populates="caja", order_by="MovimientoCaja.id")

    def to_dict(self):
        def num(v):
            return float(v) if v is not None else None
        return {
            "id": self.id,
            "codigo": self.codigo,
            "usuario_id": self.usuario_id,
            "usuario_nombre": self.usuario.nombre_completo if self.usuario else None,
            "estado": self.estado,
            "fecha_apertura": iso(self.fecha_apertura),
            "monto_apertura": num(self.monto_apertura),
            "fecha_cierre": iso(self.fecha_cierre),
            "efectivo_esperado": num(self.efectivo_esperado),
            "efectivo_contado": num(self.efectivo_contado),
            "diferencia": num(self.diferencia),
            "observacion_cierre": self.observacion_cierre,
        }


class Venta(db.Model):
    __tablename__ = "venta"
    # efectivo | tarjeta (POS) | qr (QR del POS: Yape/Plin/otras billeteras) | yape | plin (directos) | mixto
    METODOS_PAGO = ("efectivo", "tarjeta", "qr", "yape", "plin")

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), nullable=False, unique=True)  # V-000001
    usuario_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False)
    rol_id = db.Column(db.Integer, ForeignKey("rol.id", ondelete="RESTRICT"), nullable=False)
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)
    # Nombre o razón social del cliente: obligatorio al registrar la venta.
    cliente_nombre = db.Column(db.String(180))
    # DNI/CE/RUC del cliente: obligatorio en toda boleta o factura.
    cliente_documento = db.Column(db.String(15))
    # Método único, o "mixto" si se pagó con varios (el detalle está en venta_pago).
    metodo_pago = db.Column(db.String(30), nullable=False, default="efectivo")
    descuento = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    igv = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    monto_recibido = db.Column(db.Numeric(10, 2))  # solo pagos en efectivo
    vuelto = db.Column(db.Numeric(10, 2))
    estado = db.Column(db.String(15), nullable=False, default="completada")  # completada | anulada
    # Trazabilidad de la anulación
    anulada_por_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    fecha_anulacion = db.Column(db.DateTime(timezone=True))
    motivo_anulacion = db.Column(db.String(240))
    # Supervisor que autorizó con PIN (anulación hecha desde la caja del cajero o descuento mayor al permitido)
    autorizada_por_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    # Caja (turno) en la que se cobró la venta
    caja_id = db.Column(db.Integer, ForeignKey("caja.id", ondelete="SET NULL"), index=True)
    # Comprobante electrónico (SUNAT): boleta o factura, con sus archivos guardados localmente
    tipo_comprobante = db.Column(db.String(10))      # boleta | factura
    comprobante_serie = db.Column(db.String(10))     # B001 | F001
    comprobante_numero = db.Column(db.String(20))
    comprobante_api_id = db.Column(db.Integer)        # id del comprobante en la API de facturación
    comprobante_pdf = db.Column(db.String(300))      # ruta relativa del PDF guardado
    comprobante_xml = db.Column(db.String(300))      # ruta relativa del XML guardado
    estado_sunat = db.Column(db.String(15), nullable=False, default="pendiente")  # pendiente | aceptado | error
    sunat_mensaje = db.Column(db.String(300))
    sunat_intentos = db.Column(db.Integer, nullable=False, default=0)

    caja = relationship("Caja", back_populates="ventas")
    usuario = relationship("Usuario", foreign_keys=[usuario_id])
    anulada_por = relationship("Usuario", foreign_keys=[anulada_por_id])
    autorizada_por = relationship("Usuario", foreign_keys=[autorizada_por_id])
    rol = relationship("Rol")
    detalles = relationship("VentaDetalle", back_populates="venta", cascade="all, delete-orphan")
    pagos = relationship("VentaPago", back_populates="venta", cascade="all, delete-orphan", order_by="VentaPago.id")

    def pagos_efectivos(self):
        """[(metodo, monto)] de la venta. Las ventas antiguas no tienen filas en venta_pago."""
        if self.pagos:
            return [(p.metodo, Decimal(str(p.monto))) for p in self.pagos]
        return [(self.metodo_pago, Decimal(str(self.total)))]

    def to_dict(self, include_detalles=True):
        data = {
            "id": self.id,
            "codigo": self.codigo,
            "usuario_id": self.usuario_id,
            "usuario_nombre": self.usuario.nombre_completo if self.usuario else None,
            "rol": self.rol.nombre if self.rol else None,
            "fecha": iso(self.fecha),
            "cliente_nombre": self.cliente_nombre,
            "cliente_documento": self.cliente_documento,
            "metodo_pago": self.metodo_pago,
            "pagos": [p.to_dict() for p in self.pagos] or [
                {"metodo": self.metodo_pago, "monto": float(self.total), "proveedor": None, "nro_operacion": None,
                 "monto_recibido": float(self.monto_recibido) if self.monto_recibido is not None else None,
                 "vuelto": float(self.vuelto) if self.vuelto is not None else None}],
            "descuento": float(self.descuento or 0),
            "subtotal": float(self.subtotal),
            "igv": float(self.igv),
            "total": float(self.total),
            "monto_recibido": float(self.monto_recibido) if self.monto_recibido is not None else None,
            "vuelto": float(self.vuelto) if self.vuelto is not None else None,
            "estado": self.estado,
            "anulada_por": self.anulada_por.nombre_completo if self.anulada_por else None,
            "fecha_anulacion": iso(self.fecha_anulacion),
            "motivo_anulacion": self.motivo_anulacion,
            "autorizada_por": self.autorizada_por.nombre_completo if self.autorizada_por else None,
            "cantidad_items": sum(d.cantidad for d in self.detalles),
            "caja_id": self.caja_id,
            "caja_codigo": self.caja.codigo if self.caja else None,
            "caja_estado": self.caja.estado if self.caja else None,
            "tipo_comprobante": self.tipo_comprobante,
            "comprobante": f"{self.comprobante_serie}-{self.comprobante_numero}" if self.comprobante_serie else None,
            "estado_sunat": self.estado_sunat,
            "sunat_mensaje": self.sunat_mensaje,
            "tiene_pdf": bool(self.comprobante_pdf),
        }
        if include_detalles:
            data["detalles"] = [d.to_dict() for d in self.detalles]
        return data


class VentaDetalle(db.Model):
    __tablename__ = "venta_detalle"
    __table_args__ = (UniqueConstraint("venta_id", "producto_id", name="uq_venta_producto"),)
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, ForeignKey("venta.id", ondelete="CASCADE"), nullable=False)
    producto_id = db.Column(db.Integer, ForeignKey("producto.id", ondelete="RESTRICT"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)  # neto de la promoción
    # Promoción aplicada a la línea (2x1, % de descuento...). Se guarda el nombre
    # para que el ticket siga mostrándolo aunque la promoción se edite o venza.
    descuento = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    promocion_id = db.Column(db.Integer)
    promocion_nombre = db.Column(db.String(120))

    venta = relationship("Venta", back_populates="detalles")
    producto = relationship("Producto")

    def to_dict(self):
        return {
            "id": self.id,
            "producto_id": self.producto_id,
            "producto_sku": self.producto.sku if self.producto else None,
            "producto_codigo": self.producto.codigo if self.producto else None,
            "producto_nombre": self.producto.nombre if self.producto else None,
            "producto_imagen_url": self.producto.imagen_url if self.producto else None,
            "cantidad": self.cantidad,
            "precio_unitario": float(self.precio_unitario),
            "subtotal": float(self.subtotal),
            "descuento": float(self.descuento or 0),
            "promocion_nombre": self.promocion_nombre,
        }


class VentaPago(db.Model):
    """Cada medio de pago usado en una venta (permite pago mixto).

    Para tarjeta, QR, Yape y Plin se guarda el N° de operación del voucher o
    de la app: con él se concilia contra el cierre del POS o el reporte de Yape.
    Nunca se guarda el número de la tarjeta.
    """
    __tablename__ = "venta_pago"
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, ForeignKey("venta.id", ondelete="CASCADE"), nullable=False, index=True)
    metodo = db.Column(db.String(20), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)       # importe aplicado a la venta
    monto_recibido = db.Column(db.Numeric(10, 2))              # solo efectivo
    vuelto = db.Column(db.Numeric(10, 2))
    proveedor = db.Column(db.String(30))                       # OpenPay, Niubiz, Izipay... / app de la billetera
    tipo_tarjeta = db.Column(db.String(10))                    # debito | credito
    marca_tarjeta = db.Column(db.String(20))                   # Visa | Mastercard | American Express | Diners Club
    nro_operacion = db.Column(db.String(30), index=True)

    venta = relationship("Venta", back_populates="pagos")

    def to_dict(self):
        def num(v):
            return float(v) if v is not None else None
        return {
            "id": self.id,
            "metodo": self.metodo,
            "monto": float(self.monto),
            "monto_recibido": num(self.monto_recibido),
            "vuelto": num(self.vuelto),
            "proveedor": self.proveedor,
            "tipo_tarjeta": self.tipo_tarjeta,
            "marca_tarjeta": self.marca_tarjeta,
            "nro_operacion": self.nro_operacion,
        }


class MovimientoCaja(db.Model):
    """Retiro parcial de efectivo (sangría) autorizado por un supervisor."""
    __tablename__ = "movimiento_caja"
    id = db.Column(db.Integer, primary_key=True)
    caja_id = db.Column(db.Integer, ForeignKey("caja.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(10), nullable=False, default="retiro")  # retiro
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    motivo = db.Column(db.String(240))
    usuario_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    supervisor_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    autorizacion_id = db.Column(db.Integer)
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)

    caja = relationship("Caja", back_populates="movimientos")
    usuario = relationship("Usuario", foreign_keys=[usuario_id])
    supervisor = relationship("Usuario", foreign_keys=[supervisor_id])

    def to_dict(self):
        return {
            "id": self.id,
            "tipo": self.tipo,
            "monto": float(self.monto),
            "motivo": self.motivo,
            "usuario_nombre": self.usuario.nombre_completo if self.usuario else None,
            "supervisor_nombre": self.supervisor.nombre_completo if self.supervisor else None,
            "fecha": iso(self.fecha),
        }


class Autorizacion(db.Model):
    """Bitácora de autorizaciones con PIN de supervisor (aprobadas y rechazadas).

    Una autorización aprobada entrega un token de un solo uso, válido por
    pocos segundos y solo para la acción (y la venta) indicada.
    """
    __tablename__ = "autorizacion"
    id = db.Column(db.Integer, primary_key=True)
    accion = db.Column(db.String(30), nullable=False, index=True)
    solicitante_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    supervisor_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    caja_id = db.Column(db.Integer, ForeignKey("caja.id", ondelete="SET NULL"), index=True)
    venta_id = db.Column(db.Integer, ForeignKey("venta.id", ondelete="SET NULL"))
    monto = db.Column(db.Numeric(10, 2))
    motivo = db.Column(db.String(60))
    comentario = db.Column(db.String(240))
    detalle = db.Column(db.String(240))        # p. ej. producto retirado del carrito
    resultado = db.Column(db.String(20), nullable=False)  # aprobada | pin_invalido | bloqueado | rechazada
    token_hash = db.Column(db.String(64), index=True)
    usada = db.Column(db.Boolean, nullable=False, default=False)
    expira_en = db.Column(db.DateTime(timezone=True))
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora, index=True)

    solicitante = relationship("Usuario", foreign_keys=[solicitante_id])
    supervisor = relationship("Usuario", foreign_keys=[supervisor_id])
    venta = relationship("Venta")

    def to_dict(self):
        return {
            "id": self.id,
            "accion": self.accion,
            "solicitante_nombre": self.solicitante.nombre_completo if self.solicitante else None,
            "supervisor_nombre": self.supervisor.nombre_completo if self.supervisor else None,
            "caja_id": self.caja_id,
            "venta_codigo": self.venta.codigo if self.venta else None,
            "monto": float(self.monto) if self.monto is not None else None,
            "motivo": self.motivo,
            "comentario": self.comentario,
            "detalle": self.detalle,
            "resultado": self.resultado,
            "usada": self.usada,
            "fecha": iso(self.fecha),
        }


# Alias para el código que aún habla de "Categoria"
Categoria = Familia


# Modelos de la tienda online (clientes, sedes, promociones y pedidos)
from .models_tienda import (  # noqa: E402,F401
    Cliente, CodigoAcceso, Pedido, PedidoDetalle, Promocion, PromocionProducto, Sede,
)
