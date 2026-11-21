"""Integración de clientes con el grupo pareado (API de ida y vuelta).

Ida   : nosotros exponemos nuestro maestro de clientes (blueprint integracion).
Vuelta: leemos la API de clientes del otro grupo y la cargamos aquí.

Flujo de la carga (requerimiento del profesor):
  1. Se abre un lote (sp_lote_crear).
  2. Se lee la API del otro grupo, página por página, con reintentos si no responde.
     Cada intento queda en la traza del API (sp_traza_registrar).
  3. Todo lo recibido se guarda tal cual en la tabla de desembarco (sp_stage_cargar).
  4. sp_stage_procesar depura y pasa al maestro solo lo válido y no repetido, con la
     clave única tipo + número de documento.

El otro grupo puede tener otra estructura (otros nombres de columna, códigos SUNAT en
vez de texto, nombres y apellidos juntos o separados): se reconocen los nombres más
comunes y, si hace falta, INTEGRACION_SOCIO_MAPEO indica el campo exacto.
No hay URLs, claves ni nombres de sistema en el código: todo viene del entorno.
"""

import hmac
import json
import logging
import time
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from flask import current_app

from ..errors import ApiError
from ..extensions import db
from . import procedimientos as sp

log = logging.getLogger("coolbox.integracion")

# Nombres de campo que suelen usar otros sistemas para cada dato del cliente.
ALIAS = {
    "tipo_documento": (
        "tipo_documento",
        "tipodocumento",
        "tipo_doc",
        "tipodoc",
        "document_type",
        "tipo_documento_identidad",
        "codigo_tipo_documento",
        "id_tipo_documento",
        "documento_tipo",
        "tipo",
    ),
    "nro_documento": (
        "nro_documento",
        "numero_documento",
        "num_documento",
        "nrodocumento",
        "numerodocumento",
        "nro_doc",
        "numdoc",
        "num_doc",
        "nrodoc",
        "numdocumento",
        "documento_identidad",
        "nro_identidad",
        "numero_doc",
        "document_number",
        "documento_numero",
        "documento",
        "dni",
        "ruc",
        "numero",
    ),
    "nombres": ("nombres", "nombre", "first_name", "nombre_cliente", "name"),
    "apellidos": ("apellidos", "apellido", "last_name", "surname"),
    "apellido_paterno": ("apellido_paterno", "apellidopaterno", "primer_apellido", "first_last_name"),
    "apellido_materno": ("apellido_materno", "apellidomaterno", "segundo_apellido", "second_last_name"),
    "razon_social": ("razon_social", "razonsocial", "empresa", "business_name", "denominacion"),
    "nombre_completo": ("nombre_completo", "nombrecompleto", "full_name", "cliente", "nombre_razon_social"),
    "email": ("email", "correo", "correo_electronico", "mail", "e_mail"),
    "telefono": ("telefono", "celular", "phone", "movil", "telefono_celular", "nro_telefono", "telefono1"),
    "direccion": ("direccion", "domicilio", "address", "direccion_fiscal"),
}
LLAVES_LISTA = ("clientes", "clients", "data", "results", "items", "registros", "customers", "content", "rows")
LLAVES_SIGUIENTE = ("siguiente", "next", "next_page_url", "next_url")
REINTENTAR = {408, 425, 429, 500, 502, 503, 504}


class IntegracionError(Exception):
    def __init__(self, mensaje, intentos=None):
        super().__init__(mensaje)
        self.intentos = intentos


def _cfg(clave, defecto=None):
    return current_app.config.get(clave, defecto)


# ------------------------------------------------------------------ claves de nuestra API


def claves_entrantes():
    """{nombre_del_sistema: clave} de INTEGRACION_API_KEYS ("Grupo 5=clave1,Otro=clave2")."""
    claves = {}
    for parte in (_cfg("INTEGRACION_API_KEYS") or "").split(","):
        nombre, _, clave = parte.partition("=")
        if nombre.strip() and len(clave.strip()) >= 24:
            claves[nombre.strip()] = clave.strip()
    return claves


def sistema_por_clave(clave):
    """Nombre del sistema dueño de la clave, comparando en tiempo constante."""
    if not clave:
        return None
    recibida = sha256(clave.encode()).digest()
    encontrado = None
    for nombre, valida in claves_entrantes().items():
        if hmac.compare_digest(recibida, sha256(valida.encode()).digest()):
            encontrado = nombre
    return encontrado


# ------------------------------------------------------------------ traza del API


def trazar(
    direccion,
    sistema,
    metodo,
    endpoint,
    estado_http,
    exito,
    intento=1,
    duracion_ms=None,
    registros=None,
    mensaje=None,
    ip=None,
    lote_id=None,
):
    """Guarda la traza en su propia transacción: queda aunque la operación principal falle."""
    if not sp.disponibles():
        return
    try:
        with db.engine.begin() as conexion:
            sp.valor(
                "sp_traza_registrar",
                direccion,
                sistema,
                metodo,
                _sin_query(endpoint),
                estado_http,
                exito,
                intento,
                duracion_ms,
                registros,
                (mensaje or "")[:300] or None,
                ip,
                lote_id,
                sesion=conexion,
            )
    except Exception as exc:  # la traza nunca debe tumbar la integración
        log.warning("No se pudo registrar la traza del API: %s", exc)


def _sin_query(url):
    """La traza guarda la URL sin parámetros (podrían llevar datos o claves)."""
    partes = urlparse(url or "")
    return urlunparse(partes._replace(query="")) if partes.scheme else (url or "")[:300]


# ------------------------------------------------------------------ normalización


def _plano(registro, prefijo=""):
    """Aplana un nivel de objetos anidados: {"documento": {"tipo": ..}} -> documento_tipo."""
    plano = {}
    for clave, valor in (registro or {}).items():
        nombre = f"{prefijo}{str(clave).strip().lower().replace(' ', '_').replace('-', '_')}"
        if isinstance(valor, dict) and not prefijo:
            plano.update(_plano(valor, f"{nombre}_"))
        elif not isinstance(valor, (list, dict)):
            plano[nombre] = valor
    return plano


def _mapeo_socio():
    texto = _cfg("INTEGRACION_SOCIO_MAPEO") or ""
    if not texto:
        return {}
    try:
        mapeo = json.loads(texto)
        return {k: str(v).strip().lower() for k, v in mapeo.items() if k in ALIAS}
    except (ValueError, AttributeError):
        log.warning("INTEGRACION_SOCIO_MAPEO no es un JSON válido; se usan los nombres comunes.")
        return {}


def _texto(valor):
    if valor is None:
        return None
    texto = " ".join(str(valor).split())
    return texto or None


def normalizar(registro, mapeo=None):
    """Identifica los datos del cliente en un registro con cualquier estructura.

    Devuelve el objeto que espera sp_stage_cargar (con el registro original en payload).
    La validación definitiva la hace el procedimiento almacenado.
    """
    original = registro if isinstance(registro, dict) else {"valor": registro}
    plano = _plano(original)
    mapeo = mapeo if mapeo is not None else _mapeo_socio()

    def campo(nombre):
        if nombre in mapeo:
            return _texto(plano.get(mapeo[nombre]))
        for alias in ALIAS[nombre]:
            if _texto(plano.get(alias)) is not None:
                return _texto(plano.get(alias))
        return None

    tipo = campo("tipo_documento")
    nro = campo("nro_documento")
    nombres = campo("nombres")
    apellidos = campo("apellidos") or _texto(
        " ".join(p for p in (campo("apellido_paterno"), campo("apellido_materno")) if p)
    )
    razon = campo("razon_social")
    completo = campo("nombre_completo")
    es_ruc = (tipo or "").upper() in ("RUC", "6", "06") or (not tipo and nro and len(nro) == 11 and nro.isdigit())
    if es_ruc and not razon:
        razon = completo or _texto(" ".join(p for p in (nombres, apellidos) if p))
    elif not (nombres or apellidos or razon) and completo:
        nombres = completo
    return {
        "payload": original,
        "tipo_documento": tipo,
        "nro_documento": nro,
        "nombres": nombres,
        "apellidos": apellidos,
        "razon_social": razon,
        "email": campo("email"),
        "telefono": campo("telefono"),
        "direccion": campo("direccion"),
    }


def extraer_registros(cuerpo):
    """Lista de clientes dentro de la respuesta (lista directa o dentro de data/clientes/...)."""
    if isinstance(cuerpo, list):
        return cuerpo
    if isinstance(cuerpo, dict):
        for llave in LLAVES_LISTA:
            valor = cuerpo.get(llave)
            if isinstance(valor, list):
                return valor
            if isinstance(valor, dict):
                anidada = extraer_registros(valor)
                if anidada:
                    return anidada
        if cuerpo.get("numero_documento") or cuerpo.get("nro_documento") or cuerpo.get("documento"):
            return [cuerpo]  # un solo cliente
    return []


def _siguiente(cuerpo, url_actual):
    """URL de la página siguiente si la respuesta está paginada."""
    if not isinstance(cuerpo, dict):
        return None
    for fuente in (cuerpo, cuerpo.get("links") or {}, cuerpo.get("meta") or {}, cuerpo.get("paginacion") or {}):
        if not isinstance(fuente, dict):
            continue
        for llave in LLAVES_SIGUIENTE:
            valor = fuente.get(llave)
            if isinstance(valor, str) and valor.startswith(("http://", "https://")):
                return valor
    meta = cuerpo.get("paginacion") or cuerpo.get("meta") or cuerpo
    pagina = meta.get("pagina") or meta.get("page") or meta.get("current_page")
    total = meta.get("total_paginas") or meta.get("last_page") or meta.get("total_pages")
    if isinstance(pagina, int) and isinstance(total, int) and pagina < total:
        partes = urlparse(url_actual)
        query = dict(parse_qsl(partes.query))
        query["pagina" if "pagina" in meta or "total_paginas" in meta else "page"] = str(pagina + 1)
        return urlunparse(partes._replace(query=urlencode(query)))
    return None


# ------------------------------------------------------------------ lectura de la API del socio


def socio():
    return {
        "nombre": _cfg("INTEGRACION_SOCIO_NOMBRE") or "Grupo pareado",
        "url": _cfg("INTEGRACION_SOCIO_URL") or "",
        "tiene_clave": bool(_cfg("INTEGRACION_SOCIO_API_KEY") or _cfg("INTEGRACION_SOCIO_PASSWORD")),
    }


def _mismo_servidor(url_a, url_b):
    a, b = urlparse(url_a), urlparse(url_b)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def _cabeceras():
    cabeceras = {"Accept": "application/json", "User-Agent": _cfg("INTEGRACION_SISTEMA") or "coolbox-backend"}
    clave = _cfg("INTEGRACION_SOCIO_API_KEY")
    if _cfg("INTEGRACION_SOCIO_LOGIN_URL"):
        clave = _login_socio()
    if clave:
        esquema = _cfg("INTEGRACION_SOCIO_ESQUEMA") or ""
        cabeceras[_cfg("INTEGRACION_SOCIO_HEADER") or "X-API-Key"] = f"{esquema} {clave}".strip()
    return cabeceras


def _login_socio():
    """Si la API del otro grupo pide iniciar sesión, se obtiene el token antes de leer."""
    url = _cfg("INTEGRACION_SOCIO_LOGIN_URL")
    cuerpo = {
        _cfg("INTEGRACION_SOCIO_CAMPO_USUARIO") or "email": _cfg("INTEGRACION_SOCIO_USUARIO"),
        _cfg("INTEGRACION_SOCIO_CAMPO_PASSWORD") or "password": _cfg("INTEGRACION_SOCIO_PASSWORD"),
    }
    inicio = time.monotonic()
    try:
        resp = requests.post(
            url, json=cuerpo, headers={"Accept": "application/json"}, timeout=_cfg("INTEGRACION_TIMEOUT", 20)
        )
    except requests.RequestException as exc:
        trazar("salida", socio()["nombre"], "POST", url, None, False, mensaje=f"Login sin respuesta: {exc}")
        raise IntegracionError("La API del otro grupo no respondió al iniciar sesión.")
    ms = int((time.monotonic() - inicio) * 1000)
    datos = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    token = None
    for fuente in (datos, datos.get("data") if isinstance(datos.get("data"), dict) else {}):
        token = token or fuente.get("access_token") or fuente.get("token") or fuente.get("accessToken")
    trazar(
        "salida",
        socio()["nombre"],
        "POST",
        url,
        resp.status_code,
        bool(token),
        duracion_ms=ms,
        mensaje="Login correcto" if token else "Login sin token",
    )
    if not token:
        raise IntegracionError(f"El inicio de sesión en la API del otro grupo falló (HTTP {resp.status_code}).")
    return token


def _texto_error(resp):
    """Mensaje legible de una respuesta de error (su campo detail/message, o el inicio del texto)."""
    try:
        cuerpo = resp.json()
        if isinstance(cuerpo, dict):
            return str(cuerpo.get("detail") or cuerpo.get("message") or cuerpo.get("error") or cuerpo)[:150]
    except ValueError:
        pass
    return " ".join(resp.text.split())[:150]


def _get_con_reintentos(url, cabeceras, lote_id):
    """GET con reintentos y espera creciente. Devuelve (json, intentos usados)."""
    reintentos = max(int(_cfg("INTEGRACION_REINTENTOS", 3)), 1)
    espera = float(_cfg("INTEGRACION_ESPERA_SEGUNDOS", 2))
    nombre = socio()["nombre"]
    ultimo = "Sin respuesta"
    for intento in range(1, reintentos + 1):
        inicio = time.monotonic()
        try:
            resp = requests.get(url, headers=cabeceras, timeout=_cfg("INTEGRACION_TIMEOUT", 20))
            ms = int((time.monotonic() - inicio) * 1000)
            if resp.status_code == 200:
                try:
                    cuerpo = resp.json()
                except ValueError:
                    trazar(
                        "salida",
                        nombre,
                        "GET",
                        url,
                        200,
                        False,
                        intento,
                        ms,
                        mensaje="Respuesta no es JSON",
                        lote_id=lote_id,
                    )
                    raise IntegracionError("La API del otro grupo no respondió en formato JSON.", intento)
                cantidad = len(extraer_registros(cuerpo))
                trazar("salida", nombre, "GET", url, 200, True, intento, ms, cantidad, "Correcto", lote_id=lote_id)
                return cuerpo, intento
            ultimo = f"HTTP {resp.status_code}: {_texto_error(resp)}"
            trazar("salida", nombre, "GET", url, resp.status_code, False, intento, ms, mensaje=ultimo, lote_id=lote_id)
            if resp.status_code in (401, 403):
                raise IntegracionError(
                    f"La API del otro grupo rechazó nuestra clave (HTTP {resp.status_code}).", intento
                )
            if resp.status_code not in REINTENTAR:
                raise IntegracionError(f"La API del otro grupo respondió con error ({ultimo}).", intento)
        except requests.RequestException as exc:
            ms = int((time.monotonic() - inicio) * 1000)
            ultimo = f"Sin respuesta: {type(exc).__name__}"
            trazar("salida", nombre, "GET", url, None, False, intento, ms, mensaje=ultimo, lote_id=lote_id)
        if intento < reintentos:
            time.sleep(espera * (2 ** (intento - 1)))  # 2 s, 4 s, 8 s...
    raise IntegracionError(
        f"La API del otro grupo no respondió después de {reintentos} intentos ({ultimo}).", reintentos
    )


def leer_socio(lote_id):
    """Todas las páginas de clientes del otro grupo. Devuelve (registros, intentos)."""
    url = _cfg("INTEGRACION_SOCIO_URL")
    if not url or not url.startswith(("http://", "https://")):
        raise IntegracionError("Falta INTEGRACION_SOCIO_URL: la URL de la API de clientes del otro grupo.")
    cabeceras = _cabeceras()
    registros, intentos_total = [], 0
    maximo = int(_cfg("INTEGRACION_MAX_REGISTROS", 5000))
    visitadas = set()
    for _ in range(int(_cfg("INTEGRACION_MAX_PAGINAS", 50))):
        visitadas.add(url)
        cuerpo, intentos = _get_con_reintentos(url, cabeceras, lote_id)
        intentos_total += intentos
        registros.extend(extraer_registros(cuerpo))
        if len(registros) >= maximo:
            registros = registros[:maximo]
            break
        siguiente = _siguiente(cuerpo, url)
        # Solo se siguen páginas del mismo servidor (la clave no se envía a terceros).
        if not siguiente or siguiente in visitadas or not _mismo_servidor(siguiente, url):
            break
        url = siguiente
    return registros, intentos_total


# ------------------------------------------------------------------ carga a stage y maestro


def cargar(lote_id, registros):
    """Desembarco en la tabla stage (en bloques) y paso al maestro. Devuelve el resultado."""
    mapeo = _mapeo_socio()
    filas = [normalizar(r, mapeo) for r in registros]
    for i in range(0, len(filas), 500):
        sp.valor("sp_stage_cargar", lote_id, sp.json_param(filas[i : i + 500]))
        db.session.commit()  # lo recibido queda guardado aunque el procesamiento falle
    resultado = sp.fila("sp_stage_procesar", lote_id)
    db.session.commit()
    return resultado


def importar_socio(usuario_id):
    """Vuelta de la integración: trae los clientes del otro grupo. Devuelve el lote."""
    sp.exigir()
    datos = socio()
    lote_id = sp.valor("sp_lote_crear", datos["nombre"], "consulta", _sin_query(datos["url"]), usuario_id)
    db.session.commit()
    try:
        registros, intentos = leer_socio(lote_id)
    except IntegracionError as exc:
        sp.valor("sp_lote_cerrar", lote_id, "error", str(exc), exc.intentos)
        db.session.commit()
        return obtener_lote(lote_id)
    sp.valor("sp_lote_intentos", lote_id, intentos)
    db.session.commit()
    if not registros:
        sp.valor("sp_lote_cerrar", lote_id, "completado", "La API del otro grupo no devolvió clientes.", intentos)
        db.session.commit()
        return obtener_lote(lote_id)
    cargar(lote_id, registros)
    return obtener_lote(lote_id)


def recibir(origen, modo, registros, usuario_id=None, url=None):
    """Carga clientes que nos envían (recepción por nuestra API o archivo JSON)."""
    sp.exigir()
    if not isinstance(registros, list) or not registros:
        raise ApiError("datos_invalidos", "Envíe una lista de clientes.", 400)
    maximo = int(_cfg("INTEGRACION_MAX_REGISTROS", 5000))
    if len(registros) > maximo:
        raise ApiError("demasiados_registros", f"Se aceptan hasta {maximo} clientes por envío.", 413)
    lote_id = sp.valor("sp_lote_crear", origen, modo, url, usuario_id)
    db.session.commit()
    cargar(lote_id, registros)
    return obtener_lote(lote_id)


def obtener_lote(lote_id):
    return sp.columna("sp_lote_obtener", "lote", lote_id)[0]
