"""Emisión de boletas y facturas electrónicas con la API de facturación (fact_api, Laravel + Greenter).

Flujo por comprobante (requerimiento del profesor):
  1. Login en la API (token Bearer).
  2. POST /boletas | /invoices          -> crea el comprobante (devuelve id, serie y correlativo).
  3. POST /{id}/send-sunat              -> firma y envía a la SUNAT (entorno beta).
  4. POST /{id}/generate-pdf            -> genera el PDF.
  5. GET  /{id}/download-pdf | download-xml -> se guardan en comprobantes/<tipo>s/<AAAA-MM>/<DD>/.
La ruta de los archivos queda en la base de datos y las consultas posteriores leen el archivo local.

Si un paso falla, el id del comprobante en la API se conserva en la venta y el reintento continúa desde
ese punto, sin crear un comprobante duplicado (los correlativos de la SUNAT no se pueden reutilizar).
"""
import logging
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import requests
from flask import current_app

log = logging.getLogger("coolbox.sunat")

IGV = Decimal("1.18")
CENTIMOS = Decimal("0.01")


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


def _valor_unitario(subtotal, cantidad):
    """Valor unitario SIN IGV. Los precios de Coolbox ya incluyen IGV."""
    precio_con_igv = Decimal(str(subtotal)) / cantidad
    return float((precio_con_igv / IGV).quantize(CENTIMOS, rounding=ROUND_HALF_UP))


def _cuerpo_comprobante(venta, tipo, serie):
    """Datos de la venta en el formato que espera la API."""
    doc = venta.cliente_documento
    detalles = []
    for d in venta.detalles:
        detalles.append({
            "codigo": d.producto.sku if d.producto else str(d.producto_id),
            "descripcion": d.producto.nombre if d.producto else "Producto",
            "unidad": "NIU",
            "cantidad": d.cantidad,
            "mto_valor_unitario": _valor_unitario(d.subtotal, d.cantidad),
            "porcentaje_igv": 18,
            "tip_afe_igv": "10",   # gravado, operación onerosa
        })
    return {
        "company_id": _cfg("SUNAT_COMPANY_ID", 1),
        "branch_id": _cfg("SUNAT_BRANCH_ID", 1),
        "serie": serie,
        "fecha_emision": venta.fecha.strftime("%Y-%m-%d"),
        "moneda": "PEN",
        "tipo_operacion": "0101",
        "metodo_envio": "individual",
        "forma_pago_tipo": "Contado",
        "client": {
            "tipo_documento": _tipo_doc_cliente(doc),
            "numero_documento": doc,
            "razon_social": venta.cliente_nombre,
        },
        "detalles": detalles,
        "usuario_creacion": _cfg("SUNAT_API_EMAIL"),
    }


def _carpeta(venta, tipo):
    f = venta.fecha
    ruta = Path(_cfg("COMPROBANTES_DIR")) / (tipo + "s") / f.strftime("%Y-%m") / f.strftime("%d")
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def _mensaje_error(resp):
    """Texto legible del error que devuelve la API."""
    try:
        return str(resp.json().get("message") or resp.text)[:250]
    except ValueError:
        return resp.text[:250]


def _crear(venta, tipo, serie, headers):
    ruta = "/invoices" if tipo == "factura" else "/boletas"
    resp = requests.post(_url(ruta), json=_cuerpo_comprobante(venta, tipo, serie), headers=headers,
                         timeout=_cfg("SUNAT_TIMEOUT", 30))
    if resp.status_code >= 400:
        raise SunatError(f"No se pudo crear el comprobante: {_mensaje_error(resp)}")
    datos = resp.json().get("data") or {}
    if not datos.get("id"):
        raise SunatError("La API no devolvió el id del comprobante.")
    venta.comprobante_api_id = datos["id"]
    venta.comprobante_serie = datos.get("serie") or serie
    venta.comprobante_numero = str(datos.get("correlativo") or "")
    venta.tipo_comprobante = tipo


def _enviar(venta, tipo, headers):
    ruta = "/invoices" if tipo == "factura" else "/boletas"
    resp = requests.post(_url(f"{ruta}/{venta.comprobante_api_id}/send-sunat"), headers=headers,
                         timeout=_cfg("SUNAT_TIMEOUT", 30))
    if resp.status_code == 400 and "ya fue aceptad" in _mensaje_error(resp).lower():
        return "Aceptado por SUNAT (beta)"
    if resp.status_code >= 400:
        raise SunatError(f"SUNAT rechazó o no recibió el comprobante: {_mensaje_error(resp)}")
    return (resp.json().get("message") or "Aceptado por SUNAT (beta)")[:300]


def _guardar(venta, tipo, headers):
    ruta = "/invoices" if tipo == "factura" else "/boletas"
    base_url = _url(f"{ruta}/{venta.comprobante_api_id}")
    timeout = _cfg("SUNAT_TIMEOUT", 30)
    requests.post(base_url + "/generate-pdf", headers=headers, timeout=timeout).raise_for_status()
    carpeta = _carpeta(venta, tipo)
    base = re.sub(r"[^A-Za-z0-9_-]", "", f"{venta.comprobante_serie}-{venta.comprobante_numero}") or venta.codigo
    for formato, columna in (("pdf", "comprobante_pdf"), ("xml", "comprobante_xml")):
        resp = requests.get(f"{base_url}/download-{formato}", headers=headers, timeout=timeout)
        resp.raise_for_status()
        destino = carpeta / f"{base}.{formato}"
        destino.write_bytes(resp.content)
        setattr(venta, columna, destino.as_posix())


def emitir(venta):
    """Emite el comprobante de una venta y guarda XML y PDF. Actualiza la venta (el llamador hace commit)."""
    if not _cfg("SUNAT_API_URL"):
        venta.estado_sunat, venta.sunat_mensaje = "pendiente", "API de facturación no configurada."
        return venta
    tipo = venta.tipo_comprobante or tipo_para(venta.cliente_documento)
    serie = _cfg("SUNAT_SERIE_FACTURA" if tipo == "factura" else "SUNAT_SERIE_BOLETA")
    ultimo_error = None
    for intento in range(1, max(_cfg("SUNAT_REINTENTOS", 3), 1) + 1):
        venta.sunat_intentos = (venta.sunat_intentos or 0) + 1
        try:
            headers = _login()
            if not venta.comprobante_api_id:
                _crear(venta, tipo, serie, headers)
            mensaje = _enviar(venta, tipo, headers)
            _guardar(venta, tipo, headers)
            venta.estado_sunat, venta.sunat_mensaje = "aceptado", mensaje
            log.info("Venta %s: %s %s-%s aceptada (intento %s)", venta.codigo, tipo,
                     venta.comprobante_serie, venta.comprobante_numero, intento)
            return venta
        except (requests.RequestException, SunatError, ValueError) as exc:
            ultimo_error = str(exc)[:280]
            log.warning("Venta %s: intento %s fallido: %s", venta.codigo, intento, ultimo_error)
    venta.tipo_comprobante = tipo
    venta.estado_sunat, venta.sunat_mensaje = "error", ultimo_error
    return venta
