"""Emisión de comprobantes electrónicos con la API de facturación (fact_api, Laravel + Greenter).

Documentos: boleta (DNI/CE), factura (RUC) y nota de crédito (devoluciones).

Flujo por documento (requerimiento del profesor):
  1. Login en la API (token Bearer).
  2. POST /boletas | /invoices | /credit-notes -> crea el documento (id, serie y correlativo).
  3. POST /{id}/send-sunat                     -> firma y envía a la SUNAT (entorno beta).
  4. POST /{id}/generate-pdf                   -> genera el PDF.
  5. GET  /{id}/download-pdf | download-xml    -> se guardan en comprobantes/<tipo>/<AAAA-MM>/<DD>/.
La ruta de los archivos queda en la base de datos y las consultas posteriores leen el archivo local.

Si un paso falla, el id del documento en la API se conserva y el reintento continúa desde ese
punto, sin crear un documento duplicado (los correlativos de la SUNAT no se pueden reutilizar).

Venta y Devolucion comparten los nombres de columnas del comprobante (comprobante_serie,
comprobante_api_id, estado_sunat...), por eso el mismo flujo sirve para los dos.
"""

import logging
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import requests
from flask import current_app

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


def _cliente(venta):
    doc = venta.cliente_documento
    return {"tipo_documento": _tipo_doc_cliente(doc), "numero_documento": doc, "razon_social": venta.cliente_nombre}


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


def archivo_local(ruta):
    """Ruta absoluta de un comprobante guardado, solo si está dentro de COMPROBANTES_DIR
    (evita que una ruta manipulada en la BD entregue otros archivos del servidor)."""
    if not ruta:
        return None
    base = Path(_cfg("COMPROBANTES_DIR")).resolve()
    archivo = Path(ruta).resolve()
    if base != archivo and base not in archivo.parents:
        log.warning("Ruta de comprobante fuera de %s: %s", base, ruta)
        return None
    return archivo if archivo.is_file() else None


def _carpeta(fecha, nombre):
    """comprobantes/<tipo>/<AAAA-MM>/<DD>/ según la fecha local del comprobante."""
    local = a_local(fecha)
    ruta = Path(_cfg("COMPROBANTES_DIR")) / nombre / local.strftime("%Y-%m") / local.strftime("%d")
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


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


def _guardar(doc, ruta, carpeta_nombre, headers):
    base_url = _url(f"{ruta}/{doc.comprobante_api_id}")
    timeout = _cfg("SUNAT_TIMEOUT", 30)
    requests.post(base_url + "/generate-pdf", headers=headers, timeout=timeout).raise_for_status()
    carpeta = _carpeta(doc.fecha, carpeta_nombre)
    base = re.sub(r"[^A-Za-z0-9_-]", "", f"{doc.comprobante_serie}-{doc.comprobante_numero}") or doc.codigo
    for formato, columna in (("pdf", "comprobante_pdf"), ("xml", "comprobante_xml")):
        resp = requests.get(f"{base_url}/download-{formato}", headers=headers, timeout=timeout)
        resp.raise_for_status()
        destino = carpeta / f"{base}.{formato}"
        destino.write_bytes(resp.content)
        setattr(doc, columna, destino.as_posix())


def _emitir(doc, documento, armar_cuerpo):
    """Crea (si hace falta), envía a la SUNAT y guarda PDF y XML. El llamador hace commit."""
    if not _cfg("SUNAT_API_URL"):
        doc.estado_sunat, doc.sunat_mensaje = "pendiente", "API de facturación no configurada."
        return doc
    ruta, carpeta, _ = DOCUMENTOS[documento]
    ultimo_error = None
    for intento in range(1, max(_cfg("SUNAT_REINTENTOS", 3), 1) + 1):
        doc.sunat_intentos = (doc.sunat_intentos or 0) + 1
        try:
            headers = _login()
            if not doc.comprobante_api_id:
                _crear(doc, ruta, armar_cuerpo(), headers)
            mensaje = _enviar(doc, ruta, headers)
            _guardar(doc, ruta, carpeta, headers)
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
            ultimo_error = str(exc)[:280]
            log.warning("%s %s: intento %s fallido: %s", documento, doc.codigo, intento, ultimo_error)
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
