"""Modelos de dominio Coolbox.

Diseñado para tienda física con proyección a venta en línea. Un usuario
puede tener uno o varios roles (Administrador, Vendedor, Almacenero,
Supervisor de Ventas). Cada rol otorga permisos concretos. Todos ingresan por
el mismo login; si el usuario tiene más de un rol, elige con cuál trabajar
(el rol Administrador se elige igual que los demás) y ese rol define las
acciones que puede realizar durante la sesión.
"""
from datetime import datetime, date
from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


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
            data["permisos"] = [p.codigo for p in self.permisos]
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
    creado_en = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

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
        }
        if include_roles:
            data["roles"] = [r.to_dict(include_permisos=True) for r in self.roles]
        return data


class Categoria(db.Model):
    __tablename__ = "categoria"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False, unique=True)
    descripcion = db.Column(db.String(200))

    productos = relationship("Producto", back_populates="categoria")

    def to_dict(self):
        return {"id": self.id, "nombre": self.nombre, "descripcion": self.descripcion}


class Producto(db.Model):
    __tablename__ = "producto"
    __table_args__ = (CheckConstraint("precio >= 0", name="ck_producto_precio"),)
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(30), nullable=False, unique=True)
    nombre = db.Column(db.String(180), nullable=False)
    descripcion = db.Column(db.String(500))
    marca = db.Column(db.String(80))
    categoria_id = db.Column(db.Integer, ForeignKey("categoria.id", ondelete="RESTRICT"), nullable=False)
    precio = db.Column(db.Numeric(10, 2), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    stock_minimo = db.Column(db.Integer, nullable=False, default=5)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    # URL pública (https://...) o imagen subida como data URL (data:image/jpeg;base64,...)
    imagen_url = db.Column(db.Text)

    categoria = relationship("Categoria", back_populates="productos")

    def to_dict(self):
        return {
            "id": self.id,
            "codigo": self.codigo,
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "marca": self.marca,
            "categoria_id": self.categoria_id,
            "categoria": self.categoria.nombre if self.categoria else None,
            "precio": float(self.precio),
            "stock": self.stock,
            "stock_minimo": self.stock_minimo,
            "activo": self.activo,
            "imagen_url": self.imagen_url,
        }


class Venta(db.Model):
    __tablename__ = "venta"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), nullable=False, unique=True)  # V-000001
    usuario_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False)
    rol_id = db.Column(db.Integer, ForeignKey("rol.id", ondelete="RESTRICT"), nullable=False)
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    # Solo nombre de referencia: no se solicita DNI/RUC porque no hay validación con RENIEC/SUNAT.
    cliente_nombre = db.Column(db.String(180))
    metodo_pago = db.Column(db.String(30), nullable=False, default="efectivo")
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    igv = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    estado = db.Column(db.String(15), nullable=False, default="completada")  # completada | anulada

    usuario = relationship("Usuario")
    rol = relationship("Rol")
    detalles = relationship("VentaDetalle", back_populates="venta", cascade="all, delete-orphan")

    def to_dict(self, include_detalles=True):
        data = {
            "id": self.id,
            "codigo": self.codigo,
            "usuario_id": self.usuario_id,
            "usuario_nombre": self.usuario.nombre_completo if self.usuario else None,
            "rol": self.rol.nombre if self.rol else None,
            "fecha": self.fecha.isoformat() if self.fecha else None,
            "cliente_nombre": self.cliente_nombre,
            "metodo_pago": self.metodo_pago,
            "subtotal": float(self.subtotal),
            "igv": float(self.igv),
            "total": float(self.total),
            "estado": self.estado,
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
            "producto_codigo": self.producto.codigo if self.producto else None,
            "producto_nombre": self.producto.nombre if self.producto else None,
            "producto_imagen_url": self.producto.imagen_url if self.producto else None,
            "cantidad": self.cantidad,
            "precio_unitario": float(self.precio_unitario),
            "subtotal": float(self.subtotal),
        }
