"""Modelos de dominio Coolbox.

Diseñado para tienda física con proyección a venta en línea. Un usuario
puede tener uno o varios roles (Administrador, Vendedor, Almacenero,
Supervisor de Ventas). Cada rol otorga permisos concretos. Todos ingresan por
el mismo login; si el usuario tiene más de un rol, elige con cuál trabajar
(el rol Administrador se elige igual que los demás) y ese rol define las
acciones que puede realizar durante la sesión.

Catálogo: Familia -> Subfamilia, más Marca. El SKU de 8 dígitos se arma con
esos códigos (ver app/services/catalogo.py).
"""
from datetime import datetime, date, timezone

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

    roles = relationship("Rol", secondary=usuario_rol, backref="usuarios")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

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
    TIPOS = ("inicial", "entrada", "salida", "ajuste", "venta", "anulacion")

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


class Venta(db.Model):
    __tablename__ = "venta"
    METODOS_PAGO = ("efectivo", "tarjeta", "yape", "plin", "transferencia")

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), nullable=False, unique=True)  # V-000001
    usuario_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False)
    rol_id = db.Column(db.Integer, ForeignKey("rol.id", ondelete="RESTRICT"), nullable=False)
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)
    # Solo nombre de referencia: no se solicita DNI/RUC porque no hay validación con RENIEC/SUNAT.
    cliente_nombre = db.Column(db.String(180))
    metodo_pago = db.Column(db.String(30), nullable=False, default="efectivo")
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

    usuario = relationship("Usuario", foreign_keys=[usuario_id])
    anulada_por = relationship("Usuario", foreign_keys=[anulada_por_id])
    rol = relationship("Rol")
    detalles = relationship("VentaDetalle", back_populates="venta", cascade="all, delete-orphan")

    def to_dict(self, include_detalles=True):
        data = {
            "id": self.id,
            "codigo": self.codigo,
            "usuario_id": self.usuario_id,
            "usuario_nombre": self.usuario.nombre_completo if self.usuario else None,
            "rol": self.rol.nombre if self.rol else None,
            "fecha": iso(self.fecha),
            "cliente_nombre": self.cliente_nombre,
            "metodo_pago": self.metodo_pago,
            "subtotal": float(self.subtotal),
            "igv": float(self.igv),
            "total": float(self.total),
            "monto_recibido": float(self.monto_recibido) if self.monto_recibido is not None else None,
            "vuelto": float(self.vuelto) if self.vuelto is not None else None,
            "estado": self.estado,
            "anulada_por": self.anulada_por.nombre_completo if self.anulada_por else None,
            "fecha_anulacion": iso(self.fecha_anulacion),
            "motivo_anulacion": self.motivo_anulacion,
            "cantidad_items": sum(d.cantidad for d in self.detalles),
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
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

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
        }


# Alias para el código que aún habla de "Categoria"
Categoria = Familia
