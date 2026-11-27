"""Emisión de comprobantes electrónicos con la API de facturación (fact_api, Laravel + Greenter).

Documentos: boleta (DNI/CE), factura (RUC) y nota de crédito (devoluciones).

Flujo por documento (requerimiento del profesor):
  1. Login en la API (token Bearer).
  2. POST /boletas | /invoices | /credit-notes -> crea el documento (id, serie y correlativo).
  3. POST /{id}/send-sunat                     -> firma y envía a la SUNAT (entorno beta).
  4. POST /{id}/generate-pdf                   -> genera el PDF.
  5. GET  /{id}/download-pdf | -xml | -cdr     -> PDF, XML firmado y CDR (constancia de la SUNAT)
     se guardan en comprobantes/<tipo>/<AAAA>/<MM>/<DD>/ y en la nube (ver services/archivos.py).
La ruta de los archivos queda en la base de datos y las consultas posteriores leen el archivo local;
si se perdió, se recupera de la nube y, como último recurso, se vuelve a descargar de la API.

Si un paso falla, el id del documento en la API se conserva y el reintento continúa desde ese
punto, sin crear un documento duplicado (los correlativos de la SUNAT no se pueden reutilizar).

Venta y Devolucion comparten los nombres de columnas del comprobante (comprobante_serie,
comprobante_api_id, estado_sunat...), por eso el mismo flujo sirve para los dos.
"""

import logging
import time
from decimal import ROUND_HALF_UP, Decimal

import requests
from flask import current_app, send_file

from ..errors import ApiError
from ..extensions import db
from . import archivos
from .tiempo import a_local

log = logging.getLogger("coolbox.sunat")

# SUNAT admite hasta 10 decimales en el valor unitario; con 6 el total del comprobante
# coincide al céntimo con lo cobrado (con 2 decimales se pierden céntimos al quitar el IGV).
DECIMALES_VALOR = Decimal("0.000001")

# documento -> (ruta en la API, carpeta local, código SUNAT del tipo de comprobante)
DOCUMENTOS = {
    "boleta": ("/boletas", "boletas", "03"),
    "factura": ("/invoices", "facturas", "01"),
    "nota_credito": ("/credit-notes", "notas_credito", "07"),
}


class SunatError(Exception):
    pass


def tipo_para(documento):
    """RUC (11 dígitos) -> factura; DNI (8) o CE (9) -> boleta."""
    return "factura" if len(documento or "") == 11 else "boleta"


def _cfg(clave, defecto=""):
    return current_app.config.get(clave, defecto)


def _url(ruta):
    return _cfg("SUNAT_API_URL").rstrip("/") + _cfg("SUNAT_API_PREFIX").rstrip("/") + ruta


def _login():
    resp = requests.post(
        _cfg("SUNAT_API_URL").rstrip("/") + _cfg("SUNAT_LOGIN_PATH"),
        json={"email": _cfg("SUNAT_API_EMAIL"), "password": _cfg("SUNAT_API_PASSWORD")},
        headers={"Accept": "application/json"},
        timeout=_cfg("SUNAT_TIMEOUT", 30),
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise SunatError("La API no devolvió un token de acceso.")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _tipo_doc_cliente(doc):
    """Catálogo SUNAT 06: 6 RUC, 1 DNI, 4 carnet de extranjería."""
    doc = doc or ""
    return "6" if len(doc) == 11 else ("1" if len(doc) == 8 else "4")


def _tasa_igv():
    return Decimal(str(_cfg("IGV_RATE", 0.18)))


def _valor_unitario(precio_con_igv):
    """Valor unitario SIN IGV. Los precios de Coolbox ya incluyen IGV."""
    return float((Decimal(precio_con_igv) / (1 + _tasa_igv())).quantize(DECIMALES_VALOR, rounding=ROUND_HALF_UP))


def factor_descuento(venta):
    """Proporción que reparte el descuento global de la venta entre sus líneas.

    Las líneas guardan su subtotal neto de promociones; el descuento global (con PIN)
    se aplica sobre el total. Para que el comprobante cuadre con lo cobrado, cada
    línea se multiplica por total / suma de subtotales.
    """
    suma = sum((Decimal(str(d.subtotal)) for d in venta.detalles), Decimal("0"))
    return (Decimal(str(venta.total)) / suma) if suma else Decimal("1")


def _detalle(producto, producto_id, cantidad, precio_con_igv):
    return {
        "codigo": producto.sku if producto else str(producto_id),
        "descripcion": producto.nombre if producto else "Producto",
        "unidad": "NIU",
        "cantidad": cantidad,
        "mto_valor_unitario": _valor_unitario(precio_con_igv),
        "porcentaje_igv": float(_tasa_igv() * 100),
        "tip_afe_igv": "10",  # gravado, operación onerosa
    }


MONTO_BOLETA_SIN_DOCUMENTO = Decimal("700")  # SUNAT: boletas hasta S/ 700 pueden no identificar al cliente


def _cliente(venta):
    doc = (venta.cliente_documento or "").strip()
    if doc:
        return {"tipo_documento": _tipo_doc_cliente(doc), "numero_documento": doc, "razon_social": venta.cliente_nombre}
    # Ventas antiguas registradas como "Público general" (antes de pedir el documento)
    if venta.tipo_comprobante != "factura" and Decimal(str(venta.total)) <= MONTO_BOLETA_SIN_DOCUMENTO:
        return {"tipo_documento": "0", "numero_documento": "00000000", "razon_social": "CLIENTES VARIOS"}
    raise ApiError(
        "documento_requerido",
        "Esta venta no tiene DNI ni RUC del cliente y supera S/ 700: la SUNAT exige identificarlo, "
        "así que no se puede emitir su comprobante. Queda como comprobante interno.",
        400,
    )


def _cuerpo_comun(serie, fecha):
    return {
        "company_id": _cfg("SUNAT_COMPANY_ID", 1),
        "branch_id": _cfg("SUNAT_BRANCH_ID", 1),
        "serie": serie,
        # La fecha de emisión es la del día en Perú (la BD guarda UTC: a las 8 p. m. ya es "mañana" en UTC)
        "fecha_emision": a_local(fecha).strftime("%Y-%m-%d"),
        "moneda": "PEN",
        "forma_pago_tipo": "Contado",
        "usuario_creacion": _cfg("SUNAT_API_EMAIL"),
    }


def _cuerpo_comprobante(venta):
    """Boleta o factura de una venta."""
    tipo = venta.tipo_comprobante
    factor = factor_descuento(venta)
    cuerpo = _cuerpo_comun(_cfg("SUNAT_SERIE_FACTURA" if tipo == "factura" else "SUNAT_SERIE_BOLETA"), venta.fecha)
    cuerpo.update(
        {
            "tipo_operacion": "0101",
            "metodo_envio": "individual",
            "client": _cliente(venta),
            "detalles": [
                _detalle(d.producto, d.producto_id, d.cantidad, Decimal(str(d.subtotal)) / d.cantidad * factor)
                for d in venta.detalles
            ],
        }
    )
    return cuerpo


def _cuerpo_nota(devolucion):
    """Nota de crédito que modifica la boleta o factura de la venta."""
    venta = devolucion.venta
    es_factura = venta.tipo_comprobante == "factura"
    cuerpo = _cuerpo_comun(_cfg("SUNAT_SERIE_NC_FACTURA" if es_factura else "SUNAT_SERIE_NC_BOLETA"), devolucion.fecha)
    cuerpo.update(
        {
            "tipo_doc_afectado": "01" if es_factura else "03",
            "num_doc_afectado": f"{venta.comprobante_serie}-{venta.comprobante_numero}",
            "cod_motivo": devolucion.cod_motivo,
            "des_motivo": devolucion.motivo[:250],
            "client": _cliente(venta),
            "detalles": [
                _detalle(
                    d.venta_detalle.producto,
                    d.venta_detalle.producto_id,
                    d.cantidad,
                    Decimal(str(d.monto)) / d.cantidad,
                )
                for d in devolucion.detalles
            ],
        }
    )
    return cuerpo


# Documentos del comprador en la tienda online -> catálogo 06 de SUNAT
_DOC_TIENDA = {"DNI": "1", "CE": "4", "PASAPORTE": "7"}


def _cliente_pedido(pedido):
    return {
        "tipo_documento": _DOC_TIENDA.get(pedido.tipo_documento, "1"),
        "numero_documento": pedido.nro_documento,
        "razon_social": f"{pedido.nombres} {pedido.apellidos}"[:180],
    }


def _detalles_pedido(pedido):
    factor = factor_descuento(pedido)  # reparte el cupón entre las líneas
    return [
        _detalle(d.producto, d.producto_id, d.cantidad, Decimal(str(d.subtotal)) / d.cantidad * factor)
        for d in pedido.detalles
    ]


def _cuerpo_boleta_pedido(comprobante):
    """Boleta de un pedido online (la tienda online solo pide DNI, CE o pasaporte)."""
    pedido = comprobante.pedido
    cuerpo = _cuerpo_comun(_cfg("SUNAT_SERIE_BOLETA"), comprobante.fecha)
    cuerpo.update(
        {
            "tipo_operacion": "0101",
            "metodo_envio": "individual",
            "client": _cliente_pedido(pedido),
            "detalles": _detalles_pedido(pedido),
        }
    )
    return cuerpo


def _cuerpo_nota_pedido(nota):
    """Nota de crédito por anulación de un pedido pagado que no se entregó (motivo 01)."""
    pedido = nota.pedido
    boleta = pedido.comprobante("boleta")
    cuerpo = _cuerpo_comun(_cfg("SUNAT_SERIE_NC_BOLETA"), nota.fecha)
    cuerpo.update(
        {
            "tipo_doc_afectado": "03",
            "num_doc_afectado": f"{boleta.comprobante_serie}-{boleta.comprobante_numero}",
            "cod_motivo": "01",
            "des_motivo": (nota.motivo or "Anulación del pedido")[:250],
            "client": _cliente_pedido(pedido),
            "detalles": _detalles_pedido(pedido),
        }
    )
    return cuerpo


def _cuerpo_nota_devolucion_pedido(devolucion):
    """Nota de crédito por la devolución de productos de un pedido ya entregado (06 total, 07 por ítem)."""
    pedido = devolucion.pedido
    boleta = pedido.comprobante("boleta")
    cuerpo = _cuerpo_comun(_cfg("SUNAT_SERIE_NC_BOLETA"), devolucion.fecha)
    cuerpo.update(
        {
            "tipo_doc_afectado": "03",
            "num_doc_afectado": f"{boleta.comprobante_serie}-{boleta.comprobante_numero}",
            "cod_motivo": devolucion.cod_motivo,
            "des_motivo": devolucion.motivo[:250],
            "client": _cliente_pedido(pedido),
            "detalles": [
                _detalle(
                    d.pedido_detalle.producto,
                    d.pedido_detalle.producto_id,
                    d.cantidad,
                    Decimal(str(d.monto)) / d.cantidad,
                )
                for d in devolucion.detalles
            ],
        }
    )
    return cuerpo


def archivo_local(ruta):
    """Archivo local de un comprobante (si falta en la carpeta, se baja de la nube)."""
    return archivos.obtener(ruta)


def _mensaje_error(resp):
    """Texto legible del error que devuelve la API."""
    try:
        return str(resp.json().get("message") or resp.text)[:250]
    except ValueError:
        return resp.text[:250]


def _crear(doc, ruta, cuerpo, headers):
    resp = requests.post(_url(ruta), json=cuerpo, headers=headers, timeout=_cfg("SUNAT_TIMEOUT", 30))
    if resp.status_code >= 400:
        raise SunatError(f"No se pudo crear el documento: {_mensaje_error(resp)}")
    datos = resp.json().get("data") or {}
    if not datos.get("id"):
        raise SunatError("La API no devolvió el id del documento.")
    doc.comprobante_api_id = datos["id"]
    doc.comprobante_serie = datos.get("serie") or cuerpo["serie"]
    doc.comprobante_numero = str(datos.get("correlativo") or "")


def _enviar(doc, ruta, headers):
    resp = requests.post(
        _url(f"{ruta}/{doc.comprobante_api_id}/send-sunat"), headers=headers, timeout=_cfg("SUNAT_TIMEOUT", 30)
    )
    if resp.status_code == 400 and "ya fue aceptad" in _mensaje_error(resp).lower():
        return "Aceptado por SUNAT (beta)"
    if resp.status_code >= 400:
        raise SunatError(f"SUNAT rechazó o no recibió el documento: {_mensaje_error(resp)}")
    return (resp.json().get("message") or "Aceptado por SUNAT (beta)")[:300]


# formato -> (columna del documento, ¿obligatorio?). La CDR es la constancia de recepción de la
# SUNAT; si la API no la tiene disponible, el comprobante igual queda con su PDF y XML.
FORMATOS = {"pdf": ("comprobante_pdf", True), "xml": ("comprobante_xml", True), "cdr": ("comprobante_cdr", False)}


def _meta(doc, documento):
    """Datos del índice del gestor documental: cliente y venta/pedido de origen."""
    clase = type(doc).__name__
    if clase == "Venta":
        doc_cli, nombre, origen, codigo = doc.cliente_documento, doc.cliente_nombre, "venta", doc.codigo
    elif clase == "Devolucion":
        venta = doc.venta
        doc_cli, nombre, origen, codigo = venta.cliente_documento, venta.cliente_nombre, "devolucion", doc.codigo
    else:  # PedidoComprobante o PedidoDevolucion
        pedido = doc.pedido
        doc_cli, nombre = pedido.nro_documento, f"{pedido.nombres} {pedido.apellidos}"
        origen = "pedido" if clase == "PedidoComprobante" else "pedido_devolucion"
        codigo = pedido.codigo if clase == "PedidoComprobante" else doc.codigo
    return {
        "tipo": documento,
        "serie": doc.comprobante_serie,
        "numero": doc.comprobante_numero,
        "fecha_emision": a_local(doc.fecha).date().isoformat(),
        "cliente_documento": doc_cli,
        "cliente_nombre": (nombre or "")[:200],
        "origen": origen,
        "origen_codigo": codigo,
    }


MAX_ARCHIVO = 15 * 1024 * 1024  # un PDF/XML/CDR de comprobante pesa KB, no decenas de MB


def _guardar(doc, documento, ruta, headers):
    """Descarga PDF, XML y CDR y los guarda en el gestor documental. Devuelve el mensaje de la CDR."""
    base_url = _url(f"{ruta}/{doc.comprobante_api_id}")
    timeout = _cfg("SUNAT_TIMEOUT", 30)
    requests.post(base_url + "/generate-pdf", headers=headers, timeout=timeout).raise_for_status()
    meta = _meta(doc, documento)
    mensaje_cdr = None
    for formato, (columna, obligatorio) in FORMATOS.items():
        try:
            resp = requests.get(f"{base_url}/download-{formato}", headers=headers, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            if obligatorio:
                raise
            log.warning("%s %s: la API no entregó la CDR: %s", documento, doc.codigo, exc)
            continue
        if not resp.content or len(resp.content) > MAX_ARCHIVO:
            if obligatorio:
                raise SunatError(f"La API de facturación entregó un {formato.upper()} vacío o demasiado grande.")
            log.warning("%s %s: CDR vacía o demasiado grande, se omite", documento, doc.codigo)
            continue
        rel = archivos.ruta_relativa(
            documento, doc.fecha, doc.comprobante_serie, doc.comprobante_numero, formato, doc.codigo
        )
        archivos.guardar(rel, resp.content, formato, meta)
        setattr(doc, columna, rel)
        if formato == "cdr":
            codigo, descripcion = archivos.leer_cdr(resp.content)
            if descripcion:
                mensaje_cdr = f"{descripcion} (CDR código {codigo})"[:300]
    return mensaje_cdr


def _mensaje_amigable(exc):
    """Mensaje para el usuario sin hosts, rutas ni trazas internas (el detalle va al log)."""
    if isinstance(exc, SunatError):
        return str(exc)[:280]
    if isinstance(exc, requests.Timeout):
        return "La API de facturación tardó demasiado en responder. Pulsa Reintentar."
    if isinstance(exc, requests.ConnectionError):
        return "No se pudo conectar con la API de facturación (¿túnel o servidor apagado?). Pulsa Reintentar."
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"La API de facturación respondió con error HTTP {exc.response.status_code}."
    return "No se pudo emitir el comprobante. Pulsa Reintentar."


def _emitir(doc, documento, armar_cuerpo):
    """Crea (si hace falta), envía a la SUNAT y guarda PDF y XML. El llamador hace commit."""
    if not _cfg("SUNAT_API_URL"):
        doc.estado_sunat, doc.sunat_mensaje = "pendiente", "API de facturación no configurada."
        return doc
    ruta, _, _ = DOCUMENTOS[documento]
    ultimo_error = None
    for intento in range(1, max(_cfg("SUNAT_REINTENTOS", 3), 1) + 1):
        doc.sunat_intentos = (doc.sunat_intentos or 0) + 1
        try:
            headers = _login()
            if not doc.comprobante_api_id:
                _crear(doc, ruta, armar_cuerpo(), headers)
            mensaje = _enviar(doc, ruta, headers)
            mensaje = _guardar(doc, documento, ruta, headers) or mensaje
            doc.estado_sunat, doc.sunat_mensaje = "aceptado", mensaje
            log.info(
                "%s %s: %s-%s aceptado (intento %s)",
                documento,
                doc.codigo,
                doc.comprobante_serie,
                doc.comprobante_numero,
                intento,
            )
            return doc
        except (requests.RequestException, SunatError, ValueError) as exc:
            ultimo_error = _mensaje_amigable(exc)
            log.warning("%s %s: intento %s fallido: %s", documento, doc.codigo, intento, str(exc)[:300])
            if intento < max(_cfg("SUNAT_REINTENTOS", 3), 1):
                time.sleep(min(_cfg("SUNAT_ESPERA", 1) * intento, 5))
    doc.estado_sunat, doc.sunat_mensaje = "error", ultimo_error
    return doc


def emitir(venta):
    """Boleta o factura de una venta."""
    venta.tipo_comprobante = venta.tipo_comprobante or tipo_para(venta.cliente_documento)
    return _emitir(venta, venta.tipo_comprobante, lambda: _cuerpo_comprobante(venta))


def emitir_nota_credito(devolucion):
    """Nota de crédito de una devolución."""
    return _emitir(devolucion, "nota_credito", lambda: _cuerpo_nota(devolucion))


def emitir_pedido(comprobante):
    """Boleta o nota de crédito de un pedido online (fila de PedidoComprobante)."""
    armar = _cuerpo_boleta_pedido if comprobante.tipo == "boleta" else _cuerpo_nota_pedido
    return _emitir(comprobante, comprobante.tipo, lambda: armar(comprobante))


def emitir_nota_devolucion_pedido(devolucion):
    """Nota de crédito de la devolución de un pedido online entregado."""
    return _emitir(devolucion, "nota_credito", lambda: _cuerpo_nota_devolucion_pedido(devolucion))


def _recuperar(doc, documento):
    """Último recurso: el archivo no está en la carpeta ni en la nube. Si el comprobante fue
    aceptado, se vuelve a descargar de la API y queda otra vez guardado."""
    if doc.estado_sunat != "aceptado" or not doc.comprobante_api_id or not _cfg("SUNAT_API_URL"):
        return False
    try:
        _guardar(doc, documento, DOCUMENTOS[documento][0], _login())
        db.session.commit()
        log.info("%s %s: archivos recuperados desde la API de facturación", documento, doc.codigo)
        return True
    except (requests.RequestException, SunatError, ValueError, OSError) as exc:
        db.session.rollback()
        log.warning("%s %s: no se pudieron recuperar los archivos: %s", documento, doc.codigo, exc)
        return False


def entregar(doc, documento, formato, mensaje="El comprobante aún no está disponible."):
    """Respuesta HTTP con el PDF, XML o CDR guardado (carpeta local -> nube -> API)."""
    columna = FORMATOS.get(formato, (None,))[0]
    if doc is None or not columna:
        raise ApiError("comprobante_no_disponible", mensaje, 404)
    archivo = archivos.obtener(getattr(doc, columna))
    if not archivo and _recuperar(doc, documento):
        archivo = archivos.obtener(getattr(doc, columna))
    if not archivo:
        if formato == "cdr" and doc.estado_sunat == "aceptado":
            mensaje = "La API de facturación no entregó la CDR de este comprobante."
        raise ApiError("comprobante_no_disponible", mensaje, 404)
    if formato == "cdr":
        return send_file(archivo, mimetype="application/zip", as_attachment=True, download_name=archivo.name)
    return send_file(archivo, as_attachment=False)
