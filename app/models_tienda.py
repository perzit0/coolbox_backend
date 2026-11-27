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

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
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
    tipo_documento = db.Column(db.String(10))  # DNI | CE | PASAPORTE
    nro_documento = db.Column(db.String(15))
    celular = db.Column(db.String(15))
    password_hash = db.Column(db.String(255))  # opcional: puede entrar con Google o código
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
            "id": self.id,
            "codigo": self.codigo,
            "nombre": self.nombre,
            "direccion": self.direccion,
            "distrito": self.distrito,
            "lat": self.lat,
            "lng": self.lng,
            "horario": self.horario,
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
    tipo = db.Column(db.String(20), nullable=False)  # ver services/promociones.TIPOS
    valor = db.Column(db.Numeric(10, 2))  # %, S/ o precio según el tipo
    lleva = db.Column(db.Integer)  # N de "lleva N paga M" o mínimo de unidades
    paga = db.Column(db.Integer)  # M de "lleva N paga M"
    codigo_cupon = db.Column(db.String(30), unique=True)
    monto_minimo = db.Column(db.Numeric(10, 2))  # cupón: compra mínima
    patrocinador = db.Column(db.String(80))  # marca/empresa que auspicia (Samsung, Epson...)
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
            "id": self.id,
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "tipo": self.tipo,
            "valor": float(self.valor) if self.valor is not None else None,
            "lleva": self.lleva,
            "paga": self.paga,
            "codigo_cupon": self.codigo_cupon,
            "monto_minimo": float(self.monto_minimo) if self.monto_minimo is not None else None,
            "patrocinador": self.patrocinador,
            "alcance": self.alcance,
            "marca_id": self.marca_id,
            "marca": self.marca.nombre if self.marca else None,
            "familia_id": self.familia_id,
            "familia": self.familia.nombre if self.familia else None,
            "canal": self.canal,
            "fecha_inicio": iso(self.fecha_inicio),
            "fecha_fin": iso(self.fecha_fin),
            "activa": self.activa,
            "destacada": self.destacada,
            "estado": self.estado(),
            "etiqueta": etiqueta(self),
            "cantidad_productos": len(self.productos),
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
    producto = relationship("Producto", lazy="joined")

    def to_dict(self):
        p = self.producto
        return {
            "producto_id": self.producto_id,
            "sku": p.sku if p else None,
            "nombre": p.nombre if p else None,
            "precio": float(p.precio) if p else None,
            "imagen_url": p.imagen_url if p else None,
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
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)  # suma de precios regulares
    descuento = db.Column(db.Numeric(10, 2), nullable=False, default=0)  # promociones de producto
    descuento_cupon = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    cupon = db.Column(db.String(30))
    total = db.Column(db.Numeric(10, 2), nullable=False)
    base = db.Column(db.Numeric(10, 2), nullable=False)
    igv = db.Column(db.Numeric(10, 2), nullable=False)
    # Pago
    metodo_pago = db.Column(db.String(20), nullable=False)  # tarjeta | yape | plin | pagoefectivo
    marca_tarjeta = db.Column(db.String(20))
    ultimos4 = db.Column(db.String(4))
    nro_operacion = db.Column(db.String(30))
    cip = db.Column(db.String(12))  # código PagoEfectivo
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
    comprobantes = relationship(
        "PedidoComprobante", back_populates="pedido", cascade="all, delete-orphan", order_by="PedidoComprobante.id"
    )
    devoluciones = relationship(
        "PedidoDevolucion", back_populates="pedido", order_by="PedidoDevolucion.id", lazy="selectin"
    )

    def comprobante(self, tipo):
        return next((c for c in self.comprobantes if c.tipo == tipo), None)

    @property
    def estado_devolucion(self):
        """None, "parcial" o "total" según lo devuelto después de la entrega."""
        if not self.devoluciones:
            return None
        return "total" if all(d.cantidad_devuelta >= d.cantidad for d in self.detalles) else "parcial"

    def to_dict(self, include_detalles=True):
        d = {
            "id": self.id,
            "codigo": self.codigo,
            "estado": self.estado,
            "email": self.email,
            "nombres": self.nombres,
            "apellidos": self.apellidos,
            "cliente_nombre": f"{self.nombres} {self.apellidos}",
            "tipo_documento": self.tipo_documento,
            "nro_documento": self.nro_documento,
            "celular": self.celular,
            "sede": self.sede.to_dict() if self.sede else None,
            "subtotal": float(self.subtotal),
            "descuento": float(self.descuento or 0),
            "descuento_cupon": float(self.descuento_cupon or 0),
            "cupon": self.cupon,
            "total": float(self.total),
            "base": float(self.base),
            "igv": float(self.igv),
            "metodo_pago": self.metodo_pago,
            "marca_tarjeta": self.marca_tarjeta,
            "ultimos4": self.ultimos4,
            "nro_operacion": self.nro_operacion,
            "cip": self.cip,
            "cip_expira": iso(self.cip_expira),
            "fecha": iso(self.fecha),
            "fecha_pago": iso(self.fecha_pago),
            "fecha_listo": iso(self.fecha_listo),
            "fecha_entrega": iso(self.fecha_entrega),
            "entregado_por": self.entregado_por.nombre_completo if self.entregado_por else None,
            "motivo_cancelacion": self.motivo_cancelacion,
            "cantidad_items": sum(d.cantidad for d in self.detalles),
            "comprobantes": [c.to_dict() for c in self.comprobantes],
            "devoluciones": [d.to_dict() for d in self.devoluciones],
            "monto_devuelto": float(sum((d.total for d in self.devoluciones), 0)),
            "estado_devolucion": self.estado_devolucion,
        }
        if include_detalles:
            d["detalles"] = [x.to_dict() for x in self.detalles]
        return d


class PedidoComprobante(db.Model):
    """Comprobante electrónico de un pedido online: la boleta al quedar pagado y, si el pedido
    pagado se cancela, la nota de crédito que la anula. Una fila por documento (tabla aparte
    para no repetir columnas en el pedido). Los nombres de columna son los mismos que en
    Venta y Devolucion para usar el mismo servicio SUNAT."""

    __tablename__ = "pedido_comprobante"
    __table_args__ = (UniqueConstraint("pedido_id", "tipo", name="uq_pedido_comprobante_tipo"),)
    TIPOS = ("boleta", "nota_credito")

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(15), nullable=False)
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora)
    motivo = db.Column(db.String(240))  # solo nota de crédito
    comprobante_serie = db.Column(db.String(10))
    comprobante_numero = db.Column(db.String(20))
    comprobante_api_id = db.Column(db.Integer)
    comprobante_pdf = db.Column(db.String(300))
    comprobante_xml = db.Column(db.String(300))
    comprobante_cdr = db.Column(db.String(300))
    estado_sunat = db.Column(db.String(15), nullable=False, default="pendiente")
    sunat_mensaje = db.Column(db.String(300))
    sunat_intentos = db.Column(db.Integer, nullable=False, default=0)

    pedido = relationship("Pedido", back_populates="comprobantes")

    @property
    def codigo(self):
        return f"{self.pedido.codigo}-{'NC' if self.tipo == 'nota_credito' else 'BV'}"

    def to_dict(self):
        return {
            "id": self.id,
            "tipo": self.tipo,
            "numero": f"{self.comprobante_serie}-{self.comprobante_numero}" if self.comprobante_serie else None,
            "estado_sunat": self.estado_sunat,
            "sunat_mensaje": self.sunat_mensaje,
            "tiene_pdf": bool(self.comprobante_pdf),
            "tiene_xml": bool(self.comprobante_xml),
            "tiene_cdr": bool(self.comprobante_cdr),
            "fecha": iso(self.fecha),
        }


class PedidoDetalle(db.Model):
    __tablename__ = "pedido_detalle"
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False, index=True)
    producto_id = db.Column(db.Integer, ForeignKey("producto.id", ondelete="RESTRICT"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)  # precio regular
    descuento = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)  # neto
    promocion_id = db.Column(db.Integer)
    promocion_nombre = db.Column(db.String(120))

    pedido = relationship("Pedido", back_populates="detalles")
    producto = relationship("Producto", lazy="joined")
    devueltos = relationship("PedidoDevolucionDetalle", back_populates="pedido_detalle", lazy="selectin")

    @property
    def cantidad_devuelta(self):
        return sum(d.cantidad for d in self.devueltos)

    def to_dict(self):
        p = self.producto
        return {
            "id": self.id,
            "producto_id": self.producto_id,
            "sku": p.sku if p else None,
            "nombre": p.nombre if p else None,
            "marca": p.marca.nombre if p and p.marca else None,
            "imagen_url": p.imagen_url if p else None,
            "cantidad": self.cantidad,
            "precio_unitario": float(self.precio_unitario),
            "descuento": float(self.descuento or 0),
            "subtotal": float(self.subtotal),
            "promocion_nombre": self.promocion_nombre,
            "cantidad_devuelta": self.cantidad_devuelta,
        }


class PedidoDevolucion(db.Model):
    """Devolución de productos de un pedido online ya entregado.

    El cliente vuelve a la sede con el producto dentro del plazo (DEVOLUCION_DIAS_MAX desde la
    entrega). El registro y el reingreso al stock los hace el procedimiento sp_pedido_devolver;
    el dinero se extorna al mismo medio con el que pagó y ante la SUNAT se emite una nota de
    crédito que modifica la boleta del pedido (06 si es total, 07 si es por ítem). Las columnas
    del comprobante se llaman igual que en Venta para usar el mismo servicio SUNAT.
    """

    __tablename__ = "pedido_devolucion"
    __table_args__ = (CheckConstraint("total > 0", name="ck_pedido_devolucion_total"),)

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), nullable=False, unique=True)  # DW-000001
    pedido_id = db.Column(db.Integer, ForeignKey("pedido.id", ondelete="RESTRICT"), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False)
    autorizada_por_id = db.Column(db.Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    fecha = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora, index=True)
    motivo = db.Column(db.String(240), nullable=False)
    cod_motivo = db.Column(db.String(2), nullable=False)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    metodo_reembolso = db.Column(db.String(20), nullable=False)
    nro_operacion = db.Column(db.String(30))
    comprobante_serie = db.Column(db.String(10))
    comprobante_numero = db.Column(db.String(20))
    comprobante_api_id = db.Column(db.Integer)
    comprobante_pdf = db.Column(db.String(300))
    comprobante_xml = db.Column(db.String(300))
    comprobante_cdr = db.Column(db.String(300))
    estado_sunat = db.Column(db.String(15), nullable=False, default="pendiente")
    sunat_mensaje = db.Column(db.String(300))
    sunat_intentos = db.Column(db.Integer, nullable=False, default=0)

    pedido = relationship("Pedido", back_populates="devoluciones")
    usuario = relationship("Usuario", foreign_keys=[usuario_id])
    autorizada_por = relationship("Usuario", foreign_keys=[autorizada_por_id])
    detalles = relationship(
        "PedidoDevolucionDetalle",
        back_populates="devolucion",
        cascade="all, delete-orphan",
        order_by="PedidoDevolucionDetalle.id",
        lazy="selectin",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "codigo": self.codigo,
            "fecha": iso(self.fecha),
            "motivo": self.motivo,
            "cod_motivo": self.cod_motivo,
            "total": float(self.total),
            "metodo_reembolso": self.metodo_reembolso,
            "nro_operacion": self.nro_operacion,
            "usuario_nombre": self.usuario.nombre_completo if self.usuario else None,
            "autorizada_por": self.autorizada_por.nombre_completo if self.autorizada_por else None,
            "nota_credito": f"{self.comprobante_serie}-{self.comprobante_numero}" if self.comprobante_serie else None,
            "estado_sunat": self.estado_sunat,
            "sunat_mensaje": self.sunat_mensaje,
            "tiene_pdf": bool(self.comprobante_pdf),
            "tiene_xml": bool(self.comprobante_xml),
            "tiene_cdr": bool(self.comprobante_cdr),
            "detalles": [d.to_dict() for d in self.detalles],
        }


class PedidoDevolucionDetalle(db.Model):
    __tablename__ = "pedido_devolucion_detalle"
    __table_args__ = (
        CheckConstraint("cantidad > 0", name="ck_pedido_devolucion_detalle_cantidad"),
        UniqueConstraint("devolucion_id", "pedido_detalle_id", name="uq_pedido_devolucion_linea"),
    )
    id = db.Column(db.Integer, primary_key=True)
    devolucion_id = db.Column(
        db.Integer, ForeignKey("pedido_devolucion.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pedido_detalle_id = db.Column(
        db.Integer, ForeignKey("pedido_detalle.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    cantidad = db.Column(db.Integer, nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    reingresa_stock = db.Column(db.Boolean, nullable=False, default=True)

    devolucion = relationship("PedidoDevolucion", back_populates="detalles")
    pedido_detalle = relationship("PedidoDetalle", back_populates="devueltos")

    def to_dict(self):
        pd = self.pedido_detalle
        return {
            "id": self.id,
            "pedido_detalle_id": self.pedido_detalle_id,
            "producto_nombre": pd.producto.nombre if pd and pd.producto else None,
            "producto_sku": pd.producto.sku if pd and pd.producto else None,
            "cantidad": self.cantidad,
            "monto": float(self.monto),
            "reingresa_stock": self.reingresa_stock,
        }


def _aware(dt):
    from datetime import timezone

    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
