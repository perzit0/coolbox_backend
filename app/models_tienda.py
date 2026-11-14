"""Modelos de la tienda online.

- Cliente: comprador de la tienda web. A diferencia de los colaboradores
  (correo @coolbox.com.pe generado por el sistema), el cliente usa su propio
  correo (Gmail, Outlook, Hotmail, Yahoo...). Puede entrar con contraseña,
  con Google o con un código enviado a su correo, o comprar como invitado
  dejando solo su correo.
- Sede: tienda física donde el cliente recoge su pedido (sin delivery).
- Promocion: promoción que el administrador crea y aplica por un tiempo
  (2x1, 3x2, % de descuento, precio especial...). Aplica a la tienda online,
  a la caja o a ambas.
- Pedido: compra online con recojo en sede.
"""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .models import ahora, iso


class Cliente(db.Model):
    __tablename__ = "cliente"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(160), nullable=False, unique=True, index=True)
    nombres = db.Column(db.String(120))
    apellidos = db.Column(db.String(160))
    tipo_documento = db.Column(db.String(10))      # DNI | CE | PASAPORTE
    nro_documento = db.Column(db.String(15))
    celular = db.Column(db.String(15))
    password_hash = db.Column(db.String(255))      # opcional: puede entrar con Google o código
    google_sub = db.Column(db.String(64), unique=True)
    acepta_terminos = db.Column(db.Boolean, nullable=False, default=False)
    acepta_promociones = db.Column(db.Boolean, nullable=False, default=False)
    email_verificado = db.Column(db.Boolean, nullable=False, default=False)
    fecha_registro = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)
    ultimo_acceso = db.Column(db.DateTime(timezone=True))

    pedidos = relationship("Pedido", back_populates="cliente", order_by="Pedido.id.desc()")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, raw or "")

    @property
    def perfil_completo(self):
        return all([self.nombres, self.apellidos, self.tipo_documento, self.nro_documento, self.celular])

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "nombres": self.nombres,
            "apellidos": self.apellidos,
            "tipo_documento": self.tipo_documento,
            "nro_documento": self.nro_documento,
            "celular": self.celular,
            "acepta_terminos": self.acepta_terminos,
            "acepta_promociones": self.acepta_promociones,
            "tiene_password": bool(self.password_hash),
            "con_google": bool(self.google_sub),
            "perfil_completo": self.perfil_completo,
        }


class CodigoAcceso(db.Model):
    """Código de 6 dígitos enviado al correo del cliente para ingresar sin contraseña."""
    __tablename__ = "codigo_acceso"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(160), nullable=False, index=True)
    codigo_hash = db.Column(db.String(255), nullable=False)
    intentos = db.Column(db.Integer, nullable=False, default=0)
    usado = db.Column(db.Boolean, nullable=False, default=False)
    expira_en = db.Column(db.DateTime(timezone=True), nullable=False)
    creado_en = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)


class Sede(db.Model):
    __tablename__ = "sede"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(10), nullable=False, unique=True)
    nombre = db.Column(db.String(120), nullable=False)
    direccion = db.Column(db.String(200), nullable=False)
    distrito = db.Column(db.String(80), nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    horario = db.Column(db.String(60))
    activa = db.Column(db.Boolean, nullable=False, default=True)

    def to_dict(self, distancia_km=None):
        d = {
            "id": self.id, "codigo": self.codigo, "nombre": self.nombre, "direccion": self.direccion,
            "distrito": self.distrito, "lat": self.lat, "lng": self.lng, "horario": self.horario,
            "activa": self.activa,
        }
        if distancia_km is not None:
            d["distancia_km"] = round(distancia_km, 2)
        return d


class Promocion(db.Model):
    __tablename__ = "promocion"
    __table_args__ = (CheckConstraint("fecha_fin > fecha_inicio", name="ck_promocion_fechas"),)
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    descripcion = db.Column(db.String(300))
    tipo = db.Column(db.String(20), nullable=False)         # ver services/promociones.TIPOS
    valor = db.Column(db.Numeric(10, 2))                     # %, S/ o precio según el tipo
    lleva = db.Column(db.Integer)                            # N de "lleva N paga M" o mínimo de unidades
    paga = db.Column(db.Integer)                             # M de "lleva N paga M"
    codigo_cupon = db.Column(db.String(30), unique=True)
    monto_minimo = db.Column(db.Numeric(10, 2))              # cupón: compra mínima
    patrocinador = db.Column(db.String(80))                  # marca/empresa que auspicia (Samsung, Epson...)
    alcance = db.Column(db.String(12), nullable=False, default="productos")  # productos | marca | familia | todo
    marca_id = db.Column(db.Integer, ForeignKey("marca.id", ondelete="SET NULL"))
    familia_id = db.Column(db.Integer, ForeignKey("categoria.id", ondelete="SET NULL"))
    canal = db.Column(db.String(10), nullable=False, default="todos")  # todos | online | tienda
    fecha_inicio = db.Column(db.DateTime(timezone=True), nullable=False)
    fecha_fin = db.Column(db.DateTime(timezone=True), nullable=False)
    activa = db.Column(db.Boolean, nullable=False, default=True)
    destacada = db.Column(db.Boolean, nullable=False, default=False)  # aparece en el banner del inicio
    creado_por_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    creado_en = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)

    productos = relationship("PromocionProducto", back_populates="promocion", cascade="all, delete-orphan")
    marca = relationship("Marca")
    familia = relationship("Familia")
    creado_por = relationship("Usuario")

    def estado(self, momento=None):
        momento = momento or ahora()
        ini, fin = _aware(self.fecha_inicio), _aware(self.fecha_fin)
        if not self.activa:
            return "desactivada"
        if momento < ini:
            return "programada"
        if momento >= fin:
            return "vencida"
        return "vigente"

    def to_dict(self, con_productos=False):
        from .services.promociones import etiqueta
        d = {
            "id": self.id, "nombre": self.nombre, "descripcion": self.descripcion, "tipo": self.tipo,
            "valor": float(self.valor) if self.valor is not None else None,
            "lleva": self.lleva, "paga": self.paga, "codigo_cupon": self.codigo_cupon,
            "monto_minimo": float(self.monto_minimo) if self.monto_minimo is not None else None,
            "patrocinador": self.patrocinador, "alcance": self.alcance,
            "marca_id": self.marca_id, "marca": self.marca.nombre if self.marca else None,
            "familia_id": self.familia_id, "familia": self.familia.nombre if self.familia else None,
            "canal": self.canal, "fecha_inicio": iso(self.fecha_inicio), "fecha_fin": iso(self.fecha_fin),
            "activa": self.activa, "destacada": self.destacada, "estado": self.estado(),
            "etiqueta": etiqueta(self), "cantidad_productos": len(self.productos),
            "creado_por": self.creado_por.nombre_completo if self.creado_por else None,
        }
        if con_productos:
            d["productos"] = [pp.to_dict() for pp in self.productos]
        return d


class PromocionProducto(db.Model):
    __tablename__ = "promocion_producto"
    promocion_id = db.Column(db.Integer, ForeignKey("promocion.id", ondelete="CASCADE"), primary_key=True)
    producto_id = db.Column(db.Integer, ForeignKey("producto.id", ondelete="CASCADE"), primary_key=True)
    precio_especial = db.Column(db.Numeric(10, 2))  # solo para el tipo PRECIO_ESPECIAL

    promocion = relationship("Promocion", back_populates="productos")
    producto = relationship("Producto")

    def to_dict(self):
        p = self.producto
        return {
            "producto_id": self.producto_id, "sku": p.sku if p else None, "nombre": p.nombre if p else None,
            "precio": float(p.precio) if p else None, "imagen_url": p.imagen_url if p else None,
            "precio_especial": float(self.precio_especial) if self.precio_especial is not None else None,
        }


class Pedido(db.Model):
    """Compra online con recojo en sede.

    Estados: pendiente_pago (PagoEfectivo sin pagar) -> pagado -> listo (preparado en la sede)
    -> entregado. También: cancelado (el stock vuelve) y vencido (CIP no pagado a tiempo).
    """
    __tablename__ = "pedido"
    ESTADOS = ("pendiente_pago", "pagado", "listo", "entregado", "cancelado")
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), nullable=False, unique=True)
    cliente_id = db.Column(db.Integer, ForeignKey("cliente.id", ondelete="RESTRICT"), nullable=False, index=True)
    sede_id = db.Column(db.Integer, ForeignKey("sede.id", ondelete="RESTRICT"), nullable=False, index=True)
    estado = db.Column(db.String(20), nullable=False, default="pagado", index=True)
    # Datos del comprador al momento de la compra (el perfil puede cambiar después)
    email = db.Column(db.String(160), nullable=False)
    nombres = db.Column(db.String(120), nullable=False)
    apellidos = db.Column(db.String(160), nullable=False)
    tipo_documento = db.Column(db.String(10), nullable=False)
    nro_documento = db.Column(db.String(15), nullable=False)
    celular = db.Column(db.String(15), nullable=False)
    # Importes (IGV incluido)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)       # suma de precios regulares
    descuento = db.Column(db.Numeric(10, 2), nullable=False, default=0)  # promociones de producto
    descuento_cupon = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    cupon = db.Column(db.String(30))
    total = db.Column(db.Numeric(10, 2), nullable=False)
    base = db.Column(db.Numeric(10, 2), nullable=False)
    igv = db.Column(db.Numeric(10, 2), nullable=False)
    # Pago
    metodo_pago = db.Column(db.String(20), nullable=False)        # tarjeta | yape | plin | pagoefectivo
    marca_tarjeta = db.Column(db.String(20))
    ultimos4 = db.Column(db.String(4))
    nro_operacion = db.Column(db.String(30))
    cip = db.Column(db.String(12))                                # código PagoEfectivo
    cip_expira = db.Column(db.DateTime(timezone=True))
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora, index=True)
    fecha_pago = db.Column(db.DateTime(timezone=True))
    fecha_listo = db.Column(db.DateTime(timezone=True))
    fecha_entrega = db.Column(db.DateTime(timezone=True))
    entregado_por_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    motivo_cancelacion = db.Column(db.String(240))

    cliente = relationship("Cliente", back_populates="pedidos")
    sede = relationship("Sede")
    entregado_por = relationship("Usuario")
    detalles = relationship("PedidoDetalle", back_populates="pedido", cascade="all, delete-orphan")

    def to_dict(self, include_detalles=True):
        d = {
            "id": self.id, "codigo": self.codigo, "estado": self.estado,
            "email": self.email, "nombres": self.nombres, "apellidos": self.apellidos,
            "cliente_nombre": f"{self.nombres} {self.apellidos}",
            "tipo_documento": self.tipo_documento, "nro_documento": self.nro_documento, "celular": self.celular,
            "sede": self.sede.to_dict() if self.sede else None,
            "subtotal": float(self.subtotal), "descuento": float(self.descuento or 0),
            "descuento_cupon": float(self.descuento_cupon or 0), "cupon": self.cupon,
            "total": float(self.total), "base": float(self.base), "igv": float(self.igv),
            "metodo_pago": self.metodo_pago, "marca_tarjeta": self.marca_tarjeta, "ultimos4": self.ultimos4,
            "nro_operacion": self.nro_operacion, "cip": self.cip, "cip_expira": iso(self.cip_expira),
            "fecha": iso(self.fecha), "fecha_pago": iso(self.fecha_pago), "fecha_listo": iso(self.fecha_listo),
            "fecha_entrega": iso(self.fecha_entrega),
            "entregado_por": self.entregado_por.nombre_completo if self.entregado_por else None,
            "motivo_cancelacion": self.motivo_cancelacion,
            "cantidad_items": sum(d.cantidad for d in self.detalles),
        }
        if include_detalles:
            d["detalles"] = [x.to_dict() for x in self.detalles]
        return d


class PedidoDetalle(db.Model):
    __tablename__ = "pedido_detalle"
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False, index=True)
    producto_id = db.Column(db.Integer, ForeignKey("producto.id", ondelete="RESTRICT"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)   # precio regular
    descuento = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)          # neto
    promocion_id = db.Column(db.Integer)
    promocion_nombre = db.Column(db.String(120))

    pedido = relationship("Pedido", back_populates="detalles")
    producto = relationship("Producto")

    def to_dict(self):
        p = self.producto
        return {
            "id": self.id, "producto_id": self.producto_id, "sku": p.sku if p else None,
            "nombre": p.nombre if p else None, "marca": p.marca.nombre if p and p.marca else None,
            "imagen_url": p.imagen_url if p else None, "cantidad": self.cantidad,
            "precio_unitario": float(self.precio_unitario), "descuento": float(self.descuento or 0),
            "subtotal": float(self.subtotal), "promocion_nombre": self.promocion_nombre,
        }


def _aware(dt):
    from datetime import timezone
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
