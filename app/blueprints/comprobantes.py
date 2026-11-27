"""Gestor documental de comprobantes electrónicos.

GET  /api/comprobantes/resumen                     carpeta, nube, totales y árbol año/mes/día/tipo
GET  /api/comprobantes?desde=&hasta=&tipo=&q=      comprobantes con sus archivos (PDF, XML, CDR)
GET  /api/comprobantes/archivos/<id>               entrega el archivo desde la carpeta local
POST /api/comprobantes/archivos/<id>/verificar     compara la huella SHA-256 (carpeta y nube)
POST /api/comprobantes/sincronizar                 completa la carpeta y la nube (solo Administrador)

Lo que se muestra sale del índice comprobante_archivo (procedimientos sp_comprobantes_*);
los archivos se leen de la carpeta local, no de la API de facturación.
"""

from datetime import date

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Devolucion, Venta
from ..models_archivos import TIPOS
from ..models_tienda import PedidoComprobante, PedidoDevolucion
from ..services import archivos
from ..services import procedimientos as sp
from ..services import sunat as sunat_svc
from .auth import require_permiso

bp = Blueprint("comprobantes", __name__)

PERMISO_VER = "comprobantes.ver"
PERMISO_GESTIONAR = "comprobantes.gestionar"
LIMITE_SINCRONIZACION = 150


def _fecha(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        raise ApiError("fecha_invalida", "La fecha debe tener el formato AAAA-MM-DD.", 400) from None


def _archivo(fila):
    return {
        "id": fila["id"],
        "formato": fila["formato"],
        "nombre": fila["ruta"].rsplit("/", 1)[-1],
        "ruta": fila["ruta"],
        "bytes": fila["bytes"],
        "sha256": fila["sha256"],
        "en_nube": fila["en_nube"],
        "en_carpeta": archivos.resolver(fila["ruta"]) is not None,
    }


@bp.get("/comprobantes/resumen")
@jwt_required()
def resumen():
    require_permiso(PERMISO_VER)
    sp.exigir()
    arbol = sp.filas("sp_comprobantes_arbol")
    return jsonify(
        carpeta=archivos.base().name,
        nube={
            "activa": archivos.nube_activa(),
            "servicio": "Supabase Storage",
            "bucket": current_app.config.get("COMPROBANTES_BUCKET") or "comprobantes",
        },
        totales={
            "comprobantes": sum(f["comprobantes"] for f in arbol),
            "archivos": sum(f["archivos"] for f in arbol),
            "bytes": sum(int(f["bytes"]) for f in arbol),
            "en_nube": sum(f["en_nube"] for f in arbol),
        },
        arbol=arbol,
    )


@bp.get("/comprobantes")
@jwt_required()
def listar():
    require_permiso(PERMISO_VER)
    sp.exigir()
    tipo = request.args.get("tipo") if request.args.get("tipo") in TIPOS else None
    filas = sp.filas(
        "sp_comprobantes_listar",
        _fecha(request.args.get("desde")),
        _fecha(request.args.get("hasta")),
        tipo,
        (request.args.get("q") or "").strip()[:80] or None,
    )
    grupos = {}
    for f in filas:
        clave = (f["tipo"], f["serie"], f["numero"])
        grupo = grupos.setdefault(
            clave,
            {
                "tipo": f["tipo"],
                "comprobante": f"{f['serie']}-{f['numero']}" if f["serie"] and f["numero"] else None,
                "fecha_emision": f["fecha_emision"].isoformat() if f["fecha_emision"] else None,
                "carpeta": f["ruta"].rsplit("/", 1)[0],
                "cliente_documento": f["cliente_documento"],
                "cliente_nombre": f["cliente_nombre"],
                "origen": f["origen"],
                "origen_codigo": f["origen_codigo"],
                "archivos": [],
            },
        )
        grupo["archivos"].append(_archivo(f))
    orden = {"pdf": 0, "xml": 1, "cdr": 2}
    for grupo in grupos.values():
        grupo["archivos"].sort(key=lambda a: orden.get(a["formato"], 9))
    return jsonify(comprobantes=list(grupos.values()), total=len(grupos))


def _fila(archivo_id):
    fila = sp.fila("sp_archivo_por_id", archivo_id)
    if not fila:
        raise ApiError("archivo_no_encontrado", "El archivo no está registrado.", 404)
    return fila


@bp.get("/comprobantes/archivos/<int:archivo_id>")
@jwt_required()
def descargar(archivo_id):
    require_permiso(PERMISO_VER)
    sp.exigir()
    fila = _fila(archivo_id)
    ruta = archivos.obtener(fila["ruta"])
    if not ruta:
        raise ApiError(
            "archivo_no_disponible",
            "El archivo no está en la carpeta ni en la nube. Use Sincronizar para recuperarlo de la API.",
            404,
        )
    if fila["formato"] == "cdr":
        return send_file(ruta, mimetype="application/zip", as_attachment=True, download_name=ruta.name)
    return send_file(ruta, as_attachment=False)


@bp.post("/comprobantes/archivos/<int:archivo_id>/verificar")
@jwt_required()
def verificar(archivo_id):
    require_permiso(PERMISO_VER)
    sp.exigir()
    fila = _fila(archivo_id)
    return jsonify(archivo=fila["ruta"].rsplit("/", 1)[-1], **archivos.verificar(fila["ruta"]))


def _documentos():
    """Comprobantes aceptados por la SUNAT con su tipo de documento."""
    for venta in Venta.query.filter(Venta.estado_sunat == "aceptado").all():
        yield venta, venta.tipo_comprobante or sunat_svc.tipo_para(venta.cliente_documento)
    for clase in (Devolucion, PedidoDevolucion):
        for doc in clase.query.filter(clase.estado_sunat == "aceptado").all():
            yield doc, "nota_credito"
    for doc in PedidoComprobante.query.filter(PedidoComprobante.estado_sunat == "aceptado").all():
        yield doc, doc.tipo


@bp.post("/comprobantes/sincronizar")
@jwt_required()
def sincronizar():
    """Deja cada comprobante aceptado en la carpeta, en el índice y en la nube:
    - archivo con ruta antigua (AAAA-MM) presente en el disco -> se reorganiza y se indexa;
    - archivo perdido (disco nuevo de Render) -> se baja de la nube o, si no está, de la API;
    - archivo indexado pero sin copia en la nube -> se sube."""
    require_permiso(PERMISO_GESTIONAR)
    sp.exigir()
    resultado = {"revisados": 0, "reorganizados": 0, "desde_nube": 0, "recuperados": 0, "subidos": 0, "pendientes": 0}
    for doc, documento in _documentos():
        if resultado["recuperados"] + resultado["reorganizados"] >= LIMITE_SINCRONIZACION:
            resultado["pendientes"] += 1
            continue
        resultado["revisados"] += 1
        meta = sunat_svc._meta(doc, documento)
        faltan = False
        for formato, (columna, obligatorio) in sunat_svc.FORMATOS.items():
            ruta = getattr(doc, columna)
            if ruta and sp.fila("sp_archivo_obtener", ruta):
                if archivos.resolver(ruta):
                    continue
                if archivos.obtener(ruta):  # el disco se borró: se baja de la nube
                    resultado["desde_nube"] += 1
                else:
                    faltan = True
                continue
            local = archivos.resolver(ruta)
            if local:  # ruta antigua: se mueve a <tipo>/<AAAA>/<MM>/<DD>/ y se registra
                nueva = archivos.ruta_relativa(
                    documento, doc.fecha, doc.comprobante_serie, doc.comprobante_numero, formato, doc.codigo
                )
                archivos.guardar(nueva, local.read_bytes(), formato, meta)
                setattr(doc, columna, nueva)
                resultado["reorganizados"] += 1
            elif obligatorio or ruta:
                faltan = True
        db.session.commit()
        if faltan and sunat_svc._recuperar(doc, documento):
            resultado["recuperados"] += 1
    if archivos.nube_activa():
        for f in sp.filas("sp_comprobantes_listar", None, None, None, None):
            local = None if f["en_nube"] else archivos.resolver(f["ruta"])
            if local and archivos.subir(f["ruta"], local.read_bytes(), f["formato"]):
                sp.valor("sp_archivo_nube", f["ruta"])
                resultado["subidos"] += 1
        db.session.commit()
    return jsonify(resultado)
