"""API pública de la tienda online (clientes).

Catálogo y carrito (sin sesión):
    GET  /api/tienda/config                 -> qué formas de ingreso están activas
    GET  /api/tienda/catalogo               -> familias y marcas con productos
    GET  /api/tienda/inicio                 -> banners de promociones y secciones del inicio
    GET  /api/tienda/productos              -> búsqueda (q, familia, marca, promo, orden, page)
    GET  /api/tienda/productos/<sku>        -> detalle + venta cruzada
    POST /api/tienda/carrito                -> precios del carrito con promociones y cupón + sugerencias
    GET  /api/tienda/sedes?lat=&lng=        -> sedes de recojo, la más cercana primero
Clientes (cualquier correo: Gmail, Outlook, Hotmail, Yahoo...):
    POST /api/clientes/registro | /login | /google | /codigo | /codigo/verificar
    GET/PUT /api/clientes/me     GET /api/clientes/me/pedidos
Pedidos (recojo en sede):
    POST /api/tienda/pedidos                -> compra (requiere cuenta de cliente; emite boleta si está pagado)
    GET  /api/tienda/pedidos/<codigo>?email= -> seguimiento
"""

import json
import math
import re
import secrets
import urllib.parse
import urllib.request
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required, verify_jwt_in_request
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash

from ..errors import ApiError
from ..extensions import db, limiter
from ..models import (
    Cliente,
    CodigoAcceso,
    Familia,
    Marca,
    Pedido,
    PedidoComprobante,
    PedidoDetalle,
    Producto,
    Sede,
    Venta,
    VentaDetalle,
    ahora,
)
from ..services import correo
from ..services import promociones as promo_svc
from ..services.inventario import mover_stock
from ..services import sunat as sunat_svc
from ..services.pagos import MARCAS_TARJETA

bp = Blueprint("tienda", __name__)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
DOMINIOS_TIPICOS = {
    "gmial.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gmail.co": "gmail.com",
    "gamil.com": "gmail.com",
    "hotmial.com": "hotmail.com",
    "hotmail.co": "hotmail.com",
    "outlok.com": "outlook.com",
    "outlook.co": "outlook.com",
    "yahoo.co": "yahoo.com",
    "yaho.com": "yahoo.com",
}

# Venta cruzada: qué familias complementan a cada familia (código de familia)
COMPLEMENTOS = {
    "10": ["80", "50", "15"],  # laptops -> mouse/teclado/monitor, audífonos, impresoras
    "15": ["10", "80"],
    "20": ["80", "50", "60"],  # celulares -> cargadores, audífonos, smartwatch
    "30": ["50", "80"],
    "40": ["50", "90", "70"],  # TV -> audio, smart home, consolas
    "50": ["20", "60"],
    "60": ["20", "50"],
    "70": ["50", "40", "80"],
    "80": ["50", "80", "60"],
    "90": ["90", "15", "50"],
}


# ------------------------------------------------------------------ helpers


def _publico(p, promos):
    d = {
        "id": p.id,
        "sku": p.sku,
        "codigo": p.codigo,
        "nombre": p.nombre,
        "marca": p.marca.nombre if p.marca else None,
        "marca_id": p.marca_id,
        "familia": p.familia.nombre if p.familia else None,
        "familia_id": p.categoria_id,
        "familia_codigo": p.familia.codigo if p.familia else None,
        "subfamilia": p.subfamilia.nombre if p.subfamilia else None,
        "imagen_url": p.imagen_url,
        "stock": p.stock,
        "disponible": p.stock > 0,
    }
    d.update(promo_svc.info_precio(p, promos))
    return d


def _activos():
    return Producto.query.options(
        joinedload(Producto.marca), joinedload(Producto.familia), joinedload(Producto.subfamilia)
    ).filter(Producto.activo.is_(True), Producto.sku.isnot(None))


def _venta_cruzada(base_productos, promos, excluir_ids, limite=8):
    """Productos de familias complementarias, primero los que tienen promoción y stock.

    Se sugieren complementos de menor precio que lo que el cliente ya lleva (a un cargador
    no se le sugiere una laptop, a una laptop sí un mouse o una impresora).
    """
    familias = {p.familia.codigo for p in base_productos if p.familia}
    tope = max(float(max(p.precio for p in base_productos)) * 0.6, 150.0) if base_productos else 0
    codigos = []
    for f in familias:
        codigos += COMPLEMENTOS.get(f, [])
    if not codigos:
        return []
    candidatos = (
        _activos()
        .join(Familia, Producto.categoria_id == Familia.id)
        .filter(
            Familia.codigo.in_(codigos),
            Producto.stock > 0,
            ~Producto.id.in_(excluir_ids or [0]),
            Producto.precio <= tope,
        )
        .all()
    )
    excluir_sub = {p.subfamilia_id for p in base_productos}
    candidatos = [c for c in candidatos if c.subfamilia_id not in excluir_sub]

    def orden(p):
        info = promo_svc.info_precio(p, promos)
        return (info["promocion"] is None, codigos.index(p.familia.codigo), float(p.precio))

    candidatos.sort(key=orden)
    # Variedad: no más de 3 por familia
    elegidos, por_fam = [], {}
    for p in candidatos:
        if por_fam.get(p.categoria_id, 0) >= 3:
            continue
        por_fam[p.categoria_id] = por_fam.get(p.categoria_id, 0) + 1
        elegidos.append(p)
        if len(elegidos) >= limite:
            break
    return [_publico(p, promos) for p in elegidos]


def _promo_publica(p):
    return {
        "id": p.id,
        "nombre": p.nombre,
        "descripcion": p.descripcion,
        "tipo": p.tipo,
        "etiqueta": promo_svc.etiqueta(p),
        "patrocinador": p.patrocinador,
        "fecha_inicio": promo_svc._aware(p.fecha_inicio).isoformat(),
        "fecha_fin": promo_svc._aware(p.fecha_fin).isoformat(),
        "alcance": p.alcance,
        "marca_id": p.marca_id,
        "familia_id": p.familia_id,
    }


def _cliente_actual(opcional=False):
    if opcional:
        try:
            verify_jwt_in_request(optional=True)
        except Exception:
            return None
    try:
        claims = get_jwt()
    except Exception:
        return None
    if not claims or claims.get("tipo") != "cliente":
        if opcional:
            return None
        raise ApiError("sesion_cliente_requerida", "Inicia sesión con tu cuenta de cliente.", 401)
    cid = int(str(get_jwt_identity()).split(":")[1])
    cliente = db.session.get(Cliente, cid)
    if not cliente and not opcional:
        raise ApiError("sesion_cliente_requerida", "Tu sesión ya no es válida. Ingresa de nuevo.", 401)
    return cliente


def _token(cliente):
    cliente.ultimo_acceso = ahora()
    db.session.commit()
    t = create_access_token(
        identity=f"cliente:{cliente.id}",
        additional_claims={"tipo": "cliente"},
        expires_delta=timedelta(days=current_app.config.get("CLIENTE_TOKEN_DIAS", 30)),
    )
    return jsonify(access_token=t, cliente=cliente.to_dict())


def _email(valor):
    email = (valor or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 160:
        raise ApiError("email_invalido", "Ingresa un correo electrónico válido (por ejemplo nombre@gmail.com).", 400)
    dominio = email.split("@")[1]
    if dominio in DOMINIOS_TIPICOS:
        raise ApiError("email_sospechoso", f"¿Quisiste decir {email.split('@')[0]}@{DOMINIOS_TIPICOS[dominio]}?", 400)
    return email


def _password(raw):
    raw = raw or ""
    minimo = current_app.config.get("PASSWORD_LONGITUD_MINIMA", 8)
    if len(raw) < minimo or len(raw) > 128 or not re.search(r"[A-Za-z]", raw) or not re.search(r"\d", raw):
        raise ApiError(
            "password_debil", f"La contraseña debe tener al menos {minimo} caracteres, con letras y números.", 400
        )
    return raw


def _limite():
    return current_app.config["LIMITE_LOGIN"]


def _validar_perfil(data):
    nombres = (data.get("nombres") or "").strip()
    apellidos = (data.get("apellidos") or "").strip()
    if len(nombres) < 2 or len(apellidos) < 2:
        raise ApiError("perfil_incompleto", "Ingresa tus nombres y apellidos.", 400)
    tipo = (data.get("tipo_documento") or "DNI").upper()
    nro = (data.get("nro_documento") or "").strip().upper()
    if tipo == "DNI" and not re.fullmatch(r"\d{8}", nro):
        raise ApiError("documento_invalido", "El DNI debe tener 8 dígitos.", 400)
    if tipo == "CE" and not re.fullmatch(r"[A-Z0-9]{9,12}", nro):
        raise ApiError("documento_invalido", "El carné de extranjería debe tener de 9 a 12 caracteres.", 400)
    if tipo == "PASAPORTE" and not re.fullmatch(r"[A-Z0-9]{6,12}", nro):
        raise ApiError("documento_invalido", "El pasaporte debe tener de 6 a 12 caracteres.", 400)
    if tipo not in ("DNI", "CE", "PASAPORTE"):
        raise ApiError("documento_invalido", "Tipo de documento no válido.", 400)
    celular = re.sub(r"\D", "", data.get("celular") or "")
    if not re.fullmatch(r"9\d{8}", celular):
        raise ApiError("celular_invalido", "El celular debe tener 9 dígitos y empezar con 9.", 400)
    return {
        "nombres": nombres[:120],
        "apellidos": apellidos[:160],
        "tipo_documento": tipo,
        "nro_documento": nro,
        "celular": celular,
    }


def _distancia_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _lineas(items, bloquear=False):
    if not isinstance(items, list) or not items:
        raise ApiError("carrito_vacio", "Tu carrito está vacío.", 400)
    cantidades = {}
    for it in items[:50]:
        try:
            pid = int(it.get("producto_id"))
            q = int(it.get("cantidad") or 0)
        except (TypeError, ValueError, AttributeError):
            raise ApiError("carrito_invalido", "El carrito tiene datos no válidos.", 400)
        if q > 0:
            cantidades[pid] = min(cantidades.get(pid, 0) + q, 20)
    q = Producto.query.filter(Producto.id.in_(cantidades.keys()))
    if bloquear:
        q = q.with_for_update()
    productos = {p.id: p for p in q.all()}
    return [(productos[pid], q) for pid, q in cantidades.items() if pid in productos and productos[pid].activo]


def _cotizacion_publica(cot):
    items = []
    for it in cot["items"]:
        p = it["producto"]
        info = _publico(p, cot["promos"])
        info.update(
            {
                "cantidad": it["cantidad"],
                "bruto": float(it["bruto"]),
                "descuento": float(it["descuento"]),
                "neto": float(it["neto"]),
                "sin_stock": p.stock < it["cantidad"],
                "promocion_aplicada": (
                    {"nombre": it["promocion"].nombre, "etiqueta": promo_svc.etiqueta(it["promocion"])}
                    if it["promocion"]
                    else None
                ),
            }
        )
        items.append(info)
    return {
        "items": items,
        "subtotal": float(cot["subtotal"]),
        "descuento": float(cot["descuento"]),
        "descuento_cupon": float(cot["descuento_cupon"]),
        "total": float(cot["total"]),
        "cupon": cot["cupon"],
        "cantidad": sum(i["cantidad"] for i in items),
    }


# ------------------------------------------------------------------ catálogo


@bp.get("/tienda/config")
def config():
    return jsonify(
        google_client_id=current_app.config.get("GOOGLE_CLIENT_ID") or None,
        codigo_por_correo=correo.disponible(),
        pagoefectivo_horas=current_app.config.get("PAGOEFECTIVO_HORAS", 24),
    )


@bp.get("/tienda/catalogo")
def catalogo():
    conteo_f = dict(
        db.session.query(Producto.categoria_id, func.count(Producto.id))
        .filter(Producto.activo.is_(True))
        .group_by(Producto.categoria_id)
        .all()
    )
    conteo_m = dict(
        db.session.query(Producto.marca_id, func.count(Producto.id))
        .filter(Producto.activo.is_(True))
        .group_by(Producto.marca_id)
        .all()
    )
    familias = [
        {
            "id": f.id,
            "codigo": f.codigo,
            "nombre": f.nombre,
            "productos": conteo_f.get(f.id, 0),
            "subfamilias": [{"id": s.id, "nombre": s.nombre} for s in f.subfamilias],
        }
        for f in Familia.query.order_by(Familia.codigo).all()
        if conteo_f.get(f.id)
    ]
    marcas = [
        {"id": m.id, "nombre": m.nombre, "productos": conteo_m.get(m.id, 0)}
        for m in Marca.query.order_by(Marca.nombre).all()
        if conteo_m.get(m.id)
    ]
    return jsonify(familias=familias, marcas=marcas)


@bp.get("/tienda/inicio")
def inicio():
    promos = promo_svc.vigentes("online")
    productos = _activos().all()
    publicos = {p.id: _publico(p, promos) for p in productos}

    banners = []
    for pr in sorted([p for p in promos if p.destacada], key=lambda p: p.fecha_fin):
        incluidos = [publicos[p.id] for p in productos if promo_svc.aplica_a(pr, p) and p.stock > 0]
        incluidos.sort(key=lambda x: -x["descuento_pct"])
        if incluidos:
            b = _promo_publica(pr)
            b["max_descuento"] = max(x["descuento_pct"] for x in incluidos)
            b["productos"] = incluidos[:3]
            banners.append(b)

    ofertas = sorted(
        [x for x in publicos.values() if x["descuento_pct"] > 0 and x["disponible"]], key=lambda x: -x["descuento_pct"]
    )[:12]
    ultimos = [publicos[p.id] for p in sorted(productos, key=lambda p: -p.id) if p.stock > 0][:12]
    vendidos_ids = [
        pid
        for (pid, _) in db.session.query(VentaDetalle.producto_id, func.sum(VentaDetalle.cantidad))
        .join(Venta)
        .filter(Venta.estado == "completada")
        .group_by(VentaDetalle.producto_id)
        .order_by(func.sum(VentaDetalle.cantidad).desc())
        .limit(12)
        .all()
    ]
    vendidos = [publicos[i] for i in vendidos_ids if i in publicos]
    if len(vendidos) < 8:  # sin ventas suficientes: los de mayor rotación esperada (más stock)
        extra = [publicos[p.id] for p in sorted(productos, key=lambda p: -p.stock) if p.id not in vendidos_ids]
        vendidos += extra[: 12 - len(vendidos)]
    con_promo = [_promo_publica(p) for p in sorted(promos, key=lambda p: p.fecha_fin)]
    return jsonify(banners=banners, ofertas=ofertas, ultimos=ultimos, mas_vendidos=vendidos[:12], promociones=con_promo)


@bp.get("/tienda/productos")
def buscar():
    promos = promo_svc.vigentes("online")
    q = _activos()
    texto = (request.args.get("q") or "").strip()
    if texto:
        for palabra in texto.split()[:5]:
            like = f"%{palabra}%"
            q = q.filter(
                db.or_(
                    Producto.nombre.ilike(like),
                    Producto.sku.ilike(like),
                    Producto.codigo.ilike(like),
                    Producto.marca.has(Marca.nombre.ilike(like)),
                )
            )
    if request.args.get("familia", type=int):
        q = q.filter(Producto.categoria_id == request.args.get("familia", type=int))
    if request.args.get("marca", type=int):
        q = q.filter(Producto.marca_id == request.args.get("marca", type=int))
    items = [_publico(p, promos) for p in q.all()]
    promo_id = request.args.get("promocion", type=int)
    if promo_id:
        items = [i for i in items if i["promocion"] and i["promocion"]["id"] == promo_id]
    if request.args.get("ofertas"):
        items = [i for i in items if i["promocion"]]
    orden = request.args.get("orden") or "relevancia"
    claves = {
        "precio_asc": lambda i: i["precio"],
        "precio_desc": lambda i: -i["precio"],
        "descuento": lambda i: -i["descuento_pct"],
        "nuevos": lambda i: -i["id"],
        "relevancia": lambda i: (not i["disponible"], i["promocion"] is None, -i["descuento_pct"]),
    }
    items.sort(key=claves.get(orden, claves["relevancia"]))
    page = max(1, request.args.get("page", 1, type=int))
    per = min(48, max(1, request.args.get("per_page", 24, type=int)))
    return jsonify(
        productos=items[(page - 1) * per : page * per],
        total=len(items),
        page=page,
        pages=max(1, math.ceil(len(items) / per)),
    )


@bp.get("/tienda/productos/<sku>")
def detalle(sku):
    promos = promo_svc.vigentes("online")
    p = _activos().filter(db.or_(Producto.sku == sku, Producto.codigo == sku)).first()
    if not p:
        raise ApiError("producto_no_encontrado", "El producto no existe o ya no está a la venta.", 404)
    misma_marca = (
        _activos()
        .filter(Producto.marca_id == p.marca_id, Producto.id != p.id, Producto.stock > 0)
        .order_by(Producto.categoria_id != p.categoria_id)
        .limit(10)
        .all()
    )
    misma_familia = (
        _activos()
        .filter(
            Producto.categoria_id == p.categoria_id,
            Producto.id != p.id,
            Producto.marca_id != p.marca_id,
            Producto.stock > 0,
        )
        .limit(10)
        .all()
    )
    complementos = _venta_cruzada([p], promos, [p.id], limite=3)
    d = _publico(p, promos)
    d["descripcion"] = p.descripcion
    # Todas las promociones que aplican (por ejemplo 2x1 además de un precio especial)
    d["promociones"] = [dict(_promo_publica(pr)) for pr in promos if promo_svc.aplica_a(pr, p)]
    return jsonify(
        producto=d,
        llevalos_juntos=complementos,
        misma_marca=[_publico(x, promos) for x in misma_marca],
        misma_familia=[_publico(x, promos) for x in misma_familia],
    )


@bp.post("/tienda/carrito")
def carrito():
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify(
            items=[], subtotal=0, descuento=0, descuento_cupon=0, total=0, cupon=None, cantidad=0, sugerencias=[]
        )
    lineas = _lineas(items)
    cupon_error = None
    try:
        cot = promo_svc.cotizar(lineas, "online", cupon=data.get("cupon"))
    except ApiError as e:
        if not data.get("cupon"):
            raise
        cupon_error = e.detail
        cot = promo_svc.cotizar(lineas, "online")
    out = _cotizacion_publica(cot)
    out["cupon_error"] = cupon_error
    out["sugerencias"] = _venta_cruzada([p for p, _ in lineas], cot["promos"], [p.id for p, _ in lineas], limite=8)
    return jsonify(out)


@bp.get("/tienda/sedes")
def sedes():
    lat, lng = request.args.get("lat", type=float), request.args.get("lng", type=float)
    lista = Sede.query.filter_by(activa=True).all()
    if lat is not None and lng is not None:
        con_dist = sorted(((s, _distancia_km(lat, lng, s.lat, s.lng)) for s in lista), key=lambda x: x[1])
        return jsonify(sedes=[s.to_dict(d) for s, d in con_dist])
    return jsonify(sedes=[s.to_dict() for s in sorted(lista, key=lambda s: s.nombre)])


# ------------------------------------------------------------------ clientes


@bp.post("/clientes/registro")
@limiter.limit(_limite)
def registro():
    data = request.get_json(silent=True) or {}
    email = _email(data.get("email"))
    raw = _password(data.get("password"))
    cliente = Cliente.query.filter_by(email=email).first()
    if cliente and (cliente.password_hash or cliente.google_sub):
        raise ApiError("email_registrado", "Ya existe una cuenta con ese correo. Inicia sesión.", 409)
    # Un correo con compras como invitado no se reclama solo con una contraseña nueva:
    # quien registre la cuenta debe probar que es dueño del correo (código por correo).
    # (Solo si el envío de códigos está configurado; sin correo no habría forma de probarlo.)
    if cliente and cliente.pedidos and not cliente.email_verificado and correo.disponible():
        raise ApiError(
            "verificar_correo", "Ese correo ya tiene compras. Ingresa con el código que te enviamos por correo.", 409
        )
    if not data.get("acepta_terminos"):
        raise ApiError("terminos_requeridos", "Debes aceptar los términos y condiciones.", 400)
    if not cliente:
        cliente = Cliente(email=email)
        db.session.add(cliente)
    cliente.set_password(raw)
    cliente.acepta_terminos = True
    cliente.acepta_promociones = bool(data.get("acepta_promociones"))
    if data.get("nombres"):
        cliente.nombres = data["nombres"].strip()[:120]
    if data.get("apellidos"):
        cliente.apellidos = data["apellidos"].strip()[:160]
    db.session.commit()
    return _token(cliente)


@bp.post("/clientes/login")
@limiter.limit(_limite)
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    cliente = Cliente.query.filter_by(email=email).first()
    if not cliente or not cliente.check_password(data.get("password")):
        raise ApiError("credenciales_invalidas", "Correo o contraseña incorrectos.", 401)
    return _token(cliente)


@bp.post("/clientes/google")
@limiter.limit(_limite)
def login_google():
    """Valida el ID token de Google Identity Services y entra (o crea la cuenta)."""
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    if not client_id:
        raise ApiError("google_no_configurado", "El ingreso con Google no está habilitado.", 503)
    credencial = (request.get_json(silent=True) or {}).get("credential") or ""
    try:
        url = current_app.config["GOOGLE_TOKENINFO_URL"] + "?" + urllib.parse.urlencode({"id_token": credencial})
        with urllib.request.urlopen(url, timeout=10) as r:
            info = json.loads(r.read().decode())
    except Exception:
        raise ApiError("google_invalido", "No se pudo validar tu cuenta de Google. Intenta de nuevo.", 401)
    if info.get("aud") != client_id or info.get("email_verified") not in ("true", True):
        raise ApiError("google_invalido", "No se pudo validar tu cuenta de Google.", 401)
    email = info["email"].lower()
    cliente = Cliente.query.filter_by(google_sub=info["sub"]).first() or Cliente.query.filter_by(email=email).first()
    if not cliente:
        cliente = Cliente(email=email, acepta_terminos=False)
        db.session.add(cliente)
    cliente.google_sub = info["sub"]
    cliente.email_verificado = True
    cliente.nombres = cliente.nombres or (info.get("given_name") or "")[:120] or None
    cliente.apellidos = cliente.apellidos or (info.get("family_name") or "")[:160] or None
    db.session.commit()
    return _token(cliente)


@bp.post("/clientes/codigo")
@limiter.limit(_limite)
def enviar_codigo():
    if not correo.disponible():
        raise ApiError("correo_no_configurado", "El ingreso con código por correo no está habilitado.", 503)
    email = _email((request.get_json(silent=True) or {}).get("email"))
    cfg = current_app.config
    minutos = cfg.get("CODIGO_MINUTOS", 10)
    recientes = CodigoAcceso.query.filter(
        CodigoAcceso.email == email, CodigoAcceso.creado_en > ahora() - timedelta(minutes=minutos)
    ).count()
    if recientes >= cfg.get("CODIGO_MAX_ENVIOS", 3):
        raise ApiError("demasiados_codigos", "Ya enviamos varios códigos. Espera unos minutos.", 429)
    codigo = f"{secrets.randbelow(1000000):06d}"
    db.session.add(
        CodigoAcceso(
            email=email, codigo_hash=generate_password_hash(codigo), expira_en=ahora() + timedelta(minutes=minutos)
        )
    )
    db.session.commit()
    try:
        correo.enviar(
            email,
            "Tu código de acceso",
            f"Tu código para ingresar a la tienda es {codigo}. Vence en {minutos} minutos.",
        )
    except Exception as exc:
        current_app.logger.warning("No se pudo enviar el código: %s", exc)
        raise ApiError("correo_fallo", "No pudimos enviar el correo. Intenta de nuevo.", 502)
    return jsonify(enviado=True, mensaje=f"Enviamos un código de 6 dígitos a {email}.")


@bp.post("/clientes/codigo/verificar")
@limiter.limit(_limite)
def verificar_codigo():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    codigo = (data.get("codigo") or "").strip()
    c = (
        CodigoAcceso.query.filter(
            CodigoAcceso.email == email, CodigoAcceso.usado.is_(False), CodigoAcceso.expira_en > ahora()
        )
        .order_by(CodigoAcceso.id.desc())
        .first()
    )
    if not c or c.intentos >= current_app.config.get("CODIGO_MAX_INTENTOS", 5):
        raise ApiError("codigo_invalido", "El código venció. Pide uno nuevo.", 401)
    if not check_password_hash(c.codigo_hash, codigo):
        c.intentos += 1
        db.session.commit()
        raise ApiError("codigo_invalido", "El código no es correcto.", 401)
    c.usado = True
    cliente = Cliente.query.filter_by(email=email).first()
    if not cliente:
        cliente = Cliente(email=email)
        db.session.add(cliente)
    cliente.email_verificado = True
    db.session.commit()
    return _token(cliente)


@bp.get("/clientes/me")
@jwt_required()
def me():
    return jsonify(cliente=_cliente_actual().to_dict())


@bp.put("/clientes/me")
@jwt_required()
def actualizar_me():
    cliente = _cliente_actual()
    data = request.get_json(silent=True) or {}
    perfil = _validar_perfil(data)
    for k, v in perfil.items():
        setattr(cliente, k, v)
    if "acepta_promociones" in data:
        cliente.acepta_promociones = bool(data["acepta_promociones"])
    if data.get("acepta_terminos"):
        cliente.acepta_terminos = True
    db.session.commit()
    return jsonify(cliente=cliente.to_dict())


@bp.get("/clientes/me/pedidos")
@jwt_required()
def mis_pedidos():
    cliente = _cliente_actual()
    return jsonify(pedidos=[p.to_dict() for p in cliente.pedidos])


# ------------------------------------------------------------------ pedidos


def emitir_boleta_pedido(pedido):
    """Crea (si no existe) y emite la boleta del pedido pagado. No interrumpe el flujo si falla."""
    boleta = pedido.comprobante("boleta")
    if boleta is None:
        boleta = PedidoComprobante(pedido_id=pedido.id, tipo="boleta", fecha=pedido.fecha_pago or ahora())
        db.session.add(boleta)
        db.session.flush()
        db.session.refresh(pedido)
    if boleta.estado_sunat == "aceptado":
        return boleta
    try:
        sunat_svc.emitir_pedido(boleta)
        db.session.commit()
    except Exception as exc:  # la venta online ya está registrada
        db.session.rollback()
        current_app.logger.error("Boleta del pedido %s no emitida: %s", pedido.codigo, exc)
    return boleta


@bp.post("/tienda/pedidos")
@jwt_required()
def crear_pedido():
    """Solo clientes con cuenta: el pedido, su boleta y su seguimiento quedan en la cuenta."""
    data = request.get_json(silent=True) or {}
    cliente = _cliente_actual()
    email = cliente.email
    perfil = _validar_perfil(data)
    if not data.get("acepta_terminos"):
        raise ApiError(
            "terminos_requeridos", "Debes aceptar los términos y condiciones y la política de privacidad.", 400
        )
    sede = db.session.get(Sede, data.get("sede_id"))
    if not sede or not sede.activa:
        raise ApiError("sede_invalida", "Elige la tienda donde recogerás tu pedido.", 400)

    pago = data.get("pago") or {}
    metodo = (pago.get("metodo") or "").lower()
    if metodo not in ("tarjeta", "yape", "plin", "pagoefectivo"):
        raise ApiError("pago_invalido", "Elige un método de pago.", 400)
    if metodo == "tarjeta":
        if pago.get("marca") not in MARCAS_TARJETA or not re.fullmatch(r"\d{4}", str(pago.get("ultimos4") or "")):
            raise ApiError("pago_invalido", "Los datos de la tarjeta no son válidos.", 400)
    if metodo in ("yape", "plin") and not re.fullmatch(r"\d{6,12}", str(pago.get("nro_operacion") or "")):
        raise ApiError(
            "pago_invalido", f"Ingresa el número de operación de {metodo.capitalize()} (6 a 12 dígitos).", 400
        )

    # Los datos del checkout actualizan el perfil de la cuenta
    for k, v in perfil.items():
        setattr(cliente, k, v)
    cliente.acepta_terminos = True
    if data.get("acepta_promociones"):
        cliente.acepta_promociones = True
    db.session.flush()

    lineas = _lineas(data.get("items"), bloquear=True)
    if not lineas:
        raise ApiError("carrito_vacio", "Tu carrito está vacío.", 400)
    cot = promo_svc.cotizar(lineas, "online", cupon=data.get("cupon") or None)
    total = cot["total"]
    base = (total / (1 + Decimal(str(current_app.config.get("IGV_RATE", 0.18))))).quantize(Decimal("0.01"))

    pedido = Pedido(
        codigo=f"TMP-{secrets.token_hex(6)}",
        cliente_id=cliente.id,
        sede_id=sede.id,
        email=email,
        subtotal=cot["subtotal"],
        descuento=cot["descuento"],
        descuento_cupon=cot["descuento_cupon"],
        cupon=cot["cupon"]["codigo"] if cot["cupon"] else None,
        total=total,
        base=base,
        igv=total - base,
        metodo_pago=metodo,
        **perfil,
    )
    if metodo == "tarjeta":
        pedido.marca_tarjeta, pedido.ultimos4 = pago["marca"], str(pago["ultimos4"])
        pedido.nro_operacion = f"{secrets.randbelow(10**8):08d}"
    elif metodo in ("yape", "plin"):
        pedido.nro_operacion = str(pago["nro_operacion"])
    if metodo == "pagoefectivo":
        pedido.estado = "pendiente_pago"
        pedido.cip = f"{secrets.randbelow(10**8):08d}"
        pedido.cip_expira = ahora() + timedelta(hours=current_app.config.get("PAGOEFECTIVO_HORAS", 24))
    else:
        pedido.estado = "pagado"
        pedido.fecha_pago = ahora()
    db.session.add(pedido)
    db.session.flush()
    pedido.codigo = f"P-{pedido.id:06d}"
    for it in cot["items"]:
        p = it["producto"]
        db.session.add(
            PedidoDetalle(
                pedido_id=pedido.id,
                producto_id=p.id,
                cantidad=it["cantidad"],
                precio_unitario=p.precio,
                descuento=it["descuento"],
                subtotal=it["neto"],
                promocion_id=it["promocion"].id if it["promocion"] else None,
                promocion_nombre=it["promocion"].nombre if it["promocion"] else None,
            )
        )
        # Se separa el stock para la sede desde que se hace el pedido
        mover_stock(p, -it["cantidad"], "pedido_web", None, f"Pedido online {pedido.codigo} (recojo en {sede.nombre})")
    db.session.commit()
    # Pagado con tarjeta, Yape o Plin: se emite la boleta. Si la API falla, el pedido queda y se reintenta.
    if pedido.estado == "pagado":
        emitir_boleta_pedido(pedido)
    if correo.disponible():
        try:
            correo.enviar(
                email,
                f"Tu pedido {pedido.codigo}",
                f"Hola {pedido.nombres}, recibimos tu pedido {pedido.codigo} por S/ {pedido.total}. "
                f"Lo recoges en {sede.nombre} ({sede.direccion}) presentando tu {pedido.tipo_documento}.",
            )
        except Exception as exc:
            current_app.logger.warning("No se pudo enviar la confirmación: %s", exc)
    return jsonify(pedido=pedido.to_dict()), 201


@bp.get("/tienda/pedidos/<codigo>")
def ver_pedido(codigo):
    email = (request.args.get("email") or "").strip().lower()
    pedido = Pedido.query.filter_by(codigo=codigo.upper()).first()
    cliente = _cliente_actual(opcional=True)
    if not pedido or not (pedido.email == email or (cliente and cliente.id == pedido.cliente_id)):
        raise ApiError("pedido_no_encontrado", "No encontramos un pedido con ese código y correo.", 404)
    return jsonify(pedido=pedido.to_dict())


@bp.get("/clientes/me/pedidos/<codigo>/comprobante/<tipo>/<formato>")
@jwt_required()
def descargar_comprobante_pedido(codigo, tipo, formato):
    """El cliente descarga la boleta (o nota de crédito) de SU pedido, guardada localmente."""
    cliente = _cliente_actual()
    pedido = Pedido.query.filter_by(codigo=codigo.upper(), cliente_id=cliente.id).first()
    comprobante = pedido.comprobante(tipo) if pedido else None
    ruta = None
    if comprobante:
        ruta = {"pdf": comprobante.comprobante_pdf, "xml": comprobante.comprobante_xml}.get(formato)
    archivo = sunat_svc.archivo_local(ruta)
    if not archivo:
        raise ApiError("comprobante_no_disponible", "El comprobante aún no está disponible.", 404)
    return send_file(archivo, as_attachment=False)
