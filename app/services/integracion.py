"""Integración de clientes con otras empresas (API de ida y vuelta), una o varias.

Ida   : nosotros exponemos nuestro maestro de clientes (blueprint integracion); cada
        empresa entra con la clave que le generamos (solo guardamos su huella).
Vuelta: leemos la API de clientes de cada empresa y la cargamos aquí.

Las empresas se registran desde el panel (tabla integracion_socio): nombre, URL de su API,
cabecera y token (cifrado). Agregar una empresa nueva no requiere cambiar código.

Flujo de la carga (requerimiento del profesor):
  1. Se abre un lote (sp_lote_crear).
  2. Se lee la API de la empresa, página por página, con reintentos si no responde.
     Cada intento queda en la traza del API (sp_traza_registrar).
  3. Todo lo recibido se guarda tal cual en la tabla de desembarco (sp_stage_cargar).
  4. sp_stage_procesar depura y pasa al maestro solo lo válido y no repetido, con la
     clave única tipo + número de documento.

El otro grupo puede tener otra estructura (otros nombres de columna, códigos SUNAT en
vez de texto, nombres y apellidos juntos o separados): se reconocen los nombres más
comunes y, si hace falta, el mapeo de la empresa indica el campo exacto.
No hay URLs, claves ni nombres de empresas en el código.
"""

import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from flask import current_app

from ..errors import ApiError
from ..extensions import db
from . import cifrado
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


def hay_claves_entrantes():
    return bool(claves_entrantes()) or any(s["tiene_clave_entrada"] for s in socios(solo_activos=True))


def sistema_por_clave(clave):
    """Nombre de la empresa dueña de la clave: primero las registradas en el panel (por
    huella), luego las de INTEGRACION_API_KEYS (comparando en tiempo constante)."""
    if not clave or len(clave) > 200:
        return None
    if sp.disponibles():
        nombre = sp.valor("sp_socio_por_clave", cifrado.huella(clave))
        if nombre:
            return nombre
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


def _mapeo(mapeo):
    """Mapeo de campos de la empresa ({"nro_documento": "dni", ...}), solo con campos conocidos."""
    if isinstance(mapeo, str):
        try:
            mapeo = json.loads(mapeo) if mapeo.strip() else {}
        except ValueError:
            return {}
    if not isinstance(mapeo, dict):
        return {}
    return {k: str(v).strip().lower() for k, v in mapeo.items() if k in ALIAS and v}


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
    mapeo = mapeo or {}

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


def socios(solo_activos=False):
    return sp.columna("sp_socios_listar", "socio", solo_activos) if sp.disponibles() else []


def obtener_socio(socio_id):
    """Configuración de la empresa con su token ya descifrado (solo uso interno)."""
    sp.exigir()
    fila = sp.columna("sp_socio_config", "socio", socio_id)
    if not fila:
        raise ApiError("empresa_no_encontrada", "La empresa no existe.", 404)
    datos = fila[0]
    datos["clave"] = cifrado.descifrar(datos.get("clave_cifrada"))
    datos["clave_ilegible"] = bool(datos.get("clave_cifrada")) and not datos["clave"]
    # Respaldo: la primera empresa vino de las variables de entorno; si la llave de cifrado
    # cambió, se sigue usando el token de Render mientras se vuelve a ingresar en el panel.
    if datos["clave_ilegible"] and datos["nombre"] == _cfg("INTEGRACION_SOCIO_NOMBRE"):
        datos["clave"] = _cfg("INTEGRACION_SOCIO_API_KEY")
    return datos


def validar_url(url):
    """La URL debe ser http(s) y, en producción, no apuntar a la red interna del servidor."""
    partes = urlparse(url or "")
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ApiError("url_invalida", "Ingrese la URL completa de su API de clientes (https://...).", 400)
    if _cfg("INTEGRACION_PERMITIR_LOCAL"):
        return
    try:
        direcciones = {info[4][0] for info in socket.getaddrinfo(partes.hostname, partes.port or 443)}
    except socket.gaierror:
        raise ApiError("url_invalida", "No se encontró el servidor de esa URL.", 400) from None
    for direccion in direcciones:
        ip = ipaddress.ip_address(direccion.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ApiError("url_no_permitida", "La URL apunta a una dirección interna y no está permitida.", 400)


def guardar_socio(socio_id, data, usuario_id):
    """Alta o edición desde el panel. Devuelve (empresa, clave_entrante_nueva_o_None)."""
    sp.exigir()
    datos = {}
    for campo in ("nombre", "url", "header", "esquema", "activo"):
        if campo in data:
            datos[campo] = data[campo]
    if "url" in datos or socio_id is None:
        datos["url"] = (datos.get("url") or "").strip()
        validar_url(datos["url"])
    if "mapeo" in data:
        datos["mapeo"] = _mapeo(data.get("mapeo")) or None
    clave = (data.get("clave") or "").strip()
    if clave:
        if len(clave) > 500:
            raise ApiError("clave_invalida", "El token es demasiado largo.", 400)
        datos["clave_cifrada"], datos["clave_sufijo"] = cifrado.cifrar(clave), clave[-4:]
    elif data.get("quitar_clave"):
        datos["clave_cifrada"], datos["clave_sufijo"] = None, None
    if "activo" in datos:
        datos["activo"] = bool(datos["activo"])
    nuevo_id = sp.valor("sp_socio_guardar", socio_id, sp.json_param(datos), usuario_id)
    clave_entrante = generar_clave_entrante(nuevo_id) if socio_id is None else None
    db.session.commit()
    return socio_publico(nuevo_id), clave_entrante


def socio_publico(socio_id):
    """Datos de la empresa para el panel (sin token ni huella)."""
    encontrado = next((s for s in socios() if s["id"] == socio_id), None)
    if not encontrado:
        raise ApiError("empresa_no_encontrada", "La empresa no existe.", 404)
    return encontrado


def generar_clave_entrante(socio_id):
    """Nuestra clave para que la empresa lea nuestra API. Se muestra una sola vez."""
    clave = secrets.token_urlsafe(32)
    sp.valor("sp_socio_entrada", socio_id, cifrado.huella(clave), clave[-4:])
    return clave


def migrar_socio_inicial():
    """Si aún no hay empresas, crea la primera con las variables INTEGRACION_SOCIO_* de antes."""
    url = _cfg("INTEGRACION_SOCIO_URL")
    if not url or not sp.disponibles():
        return None
    nombre = _cfg("INTEGRACION_SOCIO_NOMBRE") or "Empresa pareada"
    clave = _cfg("INTEGRACION_SOCIO_API_KEY")
    datos = {
        "nombre": nombre,
        "url": url,
        "header": _cfg("INTEGRACION_SOCIO_HEADER") or "X-API-Key",
        "esquema": _cfg("INTEGRACION_SOCIO_ESQUEMA"),
        "mapeo": _mapeo(_cfg("INTEGRACION_SOCIO_MAPEO")) or None,
        "alias": [],
    }
    if clave:
        datos["clave_cifrada"], datos["clave_sufijo"] = cifrado.cifrar(clave), clave[-4:]
    entrantes = claves_entrantes()
    if len(entrantes) == 1:
        # La única clave que ya teníamos (p. ej. "Grupo 5") es la de esta empresa
        alias, valor = next(iter(entrantes.items()))
        datos["alias"] = [alias]
        datos["entrada_hash"], datos["entrada_sufijo"] = cifrado.huella(valor), valor[-4:]
    nuevo = sp.valor("sp_socio_migrar", sp.json_param(datos))
    db.session.commit()
    return nuevo


def _mismo_servidor(url_a, url_b):
    a, b = urlparse(url_a), urlparse(url_b)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def _cabeceras(socio):
    cabeceras = {"Accept": "application/json", "User-Agent": _cfg("INTEGRACION_SISTEMA") or "coolbox-backend"}
    if socio.get("clave"):
        valor = f"{socio.get('esquema') or ''} {socio['clave']}".strip()
        cabeceras[socio.get("header") or "X-API-Key"] = valor
    return cabeceras


def _texto_error(resp):
    """Mensaje legible de una respuesta de error (su campo detail/message, o el inicio del texto)."""
    try:
        cuerpo = resp.json()
        if isinstance(cuerpo, dict):
            return str(cuerpo.get("detail") or cuerpo.get("message") or cuerpo.get("error") or cuerpo)[:150]
    except ValueError:
        pass
    return " ".join(resp.text.split())[:150]


def _get_con_reintentos(url, cabeceras, lote_id, nombre):
    """GET con reintentos y espera creciente. Devuelve (json, intentos usados)."""
    reintentos = max(int(_cfg("INTEGRACION_REINTENTOS", 3)), 1)
    espera = float(_cfg("INTEGRACION_ESPERA_SEGUNDOS", 2))
    ultimo = "Sin respuesta"
    limite = time.monotonic() + float(_cfg("INTEGRACION_PRESUPUESTO_SEGUNDOS", 100))
    hechos = 0
    for intento in range(1, reintentos + 1):
        inicio = time.monotonic()
        restante = limite - inicio
        if restante < 5:  # sin tiempo para otro intento dentro de la petición
            break
        hechos = intento
        try:
            resp = requests.get(url, headers=cabeceras, timeout=min(float(_cfg("INTEGRACION_TIMEOUT", 55)), restante))
            ms = int((time.monotonic() - inicio) * 1000)
            if resp.status_code == 200:
                if len(resp.content) > int(_cfg("INTEGRACION_MAX_MB", 5)) * 1024 * 1024:
                    trazar(
                        "salida", nombre, "GET", url, 200, False, intento, ms,
                        mensaje="Respuesta demasiado grande", lote_id=lote_id,
                    )
                    raise IntegracionError("La respuesta de la API de la empresa supera el tamaño permitido.", intento)
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
                    raise IntegracionError("La API de la empresa no respondió en formato JSON.", intento) from None
                cantidad = len(extraer_registros(cuerpo))
                # Huella SHA-256 de lo recibido: permite demostrar después qué datos llegaron en esa llamada.
                mensaje = f"Correcto · SHA-256 {sha256(resp.content).hexdigest()[:16]}"
                trazar("salida", nombre, "GET", url, 200, True, intento, ms, cantidad, mensaje, lote_id=lote_id)
                return cuerpo, intento
            ultimo = f"HTTP {resp.status_code}: {_texto_error(resp)}"
            trazar("salida", nombre, "GET", url, resp.status_code, False, intento, ms, mensaje=ultimo, lote_id=lote_id)
            if resp.status_code in (401, 403):
                raise IntegracionError(
                    f"La API de la empresa rechazó nuestra clave (HTTP {resp.status_code}).", intento
                )
            if resp.status_code not in REINTENTAR:
                raise IntegracionError(f"La API de la empresa respondió con error ({ultimo}).", intento)
        except requests.RequestException as exc:
            ms = int((time.monotonic() - inicio) * 1000)
            ultimo = f"Sin respuesta: {type(exc).__name__}"
            trazar("salida", nombre, "GET", url, None, False, intento, ms, mensaje=ultimo, lote_id=lote_id)
        if intento < reintentos:
            time.sleep(min(espera * (2 ** (intento - 1)), max(limite - time.monotonic() - 5, 0)))  # 2 s, 4 s...
    raise IntegracionError(
        f"La API de la empresa no respondió después de {hechos} intentos ({ultimo}).", hechos
    )


def _url_socio(socio):
    url = socio.get("url") or ""
    if not url.startswith(("http://", "https://")):
        raise IntegracionError(f"Falta la URL de la API de clientes de {socio['nombre']}.")
    validar_url(url)
    return url


def probar_socio(socio):
    """Comprueba si la API de la empresa está activa (una sola llamada, sin cargar datos)."""
    url = _url_socio(socio)
    inicio = time.monotonic()
    try:
        resp = requests.get(url, headers=_cabeceras(socio), timeout=_cfg("INTEGRACION_TIMEOUT", 55))
    except requests.RequestException as exc:
        ms = int((time.monotonic() - inicio) * 1000)
        mensaje = f"Prueba de conexión: sin respuesta ({type(exc).__name__})"
        trazar("salida", socio["nombre"], "GET", url, None, False, 1, ms, mensaje=mensaje)
        return {"activa": False, "http": None, "ms": ms, "registros": None, "mensaje": mensaje}
    ms = int((time.monotonic() - inicio) * 1000)
    registros = None
    if resp.status_code == 200 and len(resp.content) <= int(_cfg("INTEGRACION_MAX_MB", 5)) * 1024 * 1024:
        try:
            registros = len(extraer_registros(resp.json()))
        except ValueError:
            registros = None
    activa = resp.status_code == 200 and registros is not None
    mensaje = (
        "Prueba de conexión: activa" if activa else f"Prueba de conexión: HTTP {resp.status_code} {_texto_error(resp)}"
    )
    trazar("salida", socio["nombre"], "GET", url, resp.status_code, activa, 1, ms, registros, mensaje[:300])
    return {"activa": activa, "http": resp.status_code, "ms": ms, "registros": registros, "mensaje": mensaje}


def leer_socio(lote_id, socio):
    """Todas las páginas de clientes de la empresa. Devuelve (registros, intentos)."""
    url = _url_socio(socio)
    cabeceras = _cabeceras(socio)
    registros, intentos_total = [], 0
    maximo = int(_cfg("INTEGRACION_MAX_REGISTROS", 5000))
    visitadas = set()
    for _ in range(int(_cfg("INTEGRACION_MAX_PAGINAS", 50))):
        visitadas.add(url)
        cuerpo, intentos = _get_con_reintentos(url, cabeceras, lote_id, socio["nombre"])
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


def cargar(lote_id, registros, mapeo=None):
    """Desembarco en la tabla stage (en bloques) y paso al maestro. Devuelve el resultado."""
    mapeo = _mapeo(mapeo)
    filas = [normalizar(r, mapeo) for r in registros]
    for i in range(0, len(filas), 500):
        sp.valor("sp_stage_cargar", lote_id, sp.json_param(filas[i : i + 500]))
        db.session.commit()  # lo recibido queda guardado aunque el procesamiento falle
    resultado = sp.fila("sp_stage_procesar", lote_id)
    db.session.commit()
    return resultado


def importar_socio(usuario_id, socio):
    """Vuelta de la integración: trae los clientes de la empresa. Devuelve el lote."""
    sp.exigir()
    if not socio.get("activo", True):
        raise ApiError("empresa_inactiva", f"{socio['nombre']} está desactivada.", 409)
    lote_id = sp.valor("sp_lote_crear", socio["nombre"], "consulta", _sin_query(socio.get("url")), usuario_id)
    db.session.commit()
    try:
        registros, intentos = leer_socio(lote_id, socio)
    except IntegracionError as exc:
        sp.valor("sp_lote_cerrar", lote_id, "error", str(exc), exc.intentos)
        db.session.commit()
        return obtener_lote(lote_id)
    sp.valor("sp_lote_intentos", lote_id, intentos)
    db.session.commit()
    if not registros:
        sp.valor("sp_lote_cerrar", lote_id, "completado", "La API de la empresa no devolvió clientes.", intentos)
        db.session.commit()
        return obtener_lote(lote_id)
    cargar(lote_id, registros, socio.get("mapeo"))
    return obtener_lote(lote_id)


def recibir(origen, modo, registros, usuario_id=None, url=None, mapeo=None):
    """Carga clientes que nos envían (recepción por nuestra API o archivo JSON)."""
    sp.exigir()
    if not isinstance(registros, list) or not registros:
        raise ApiError("datos_invalidos", "Envíe una lista de clientes.", 400)
    maximo = int(_cfg("INTEGRACION_MAX_REGISTROS", 5000))
    if len(registros) > maximo:
        raise ApiError("demasiados_registros", f"Se aceptan hasta {maximo} clientes por envío.", 413)
    lote_id = sp.valor("sp_lote_crear", origen, modo, url, usuario_id)
    db.session.commit()
    cargar(lote_id, registros, mapeo)
    return obtener_lote(lote_id)


def obtener_lote(lote_id):
    return sp.columna("sp_lote_obtener", "lote", lote_id)[0]
