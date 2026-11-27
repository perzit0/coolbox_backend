"""Autenticación con un único punto de ingreso.

Flujo:
    1. POST /api/auth/login            -> valida correo y contraseña.
       - Tras 5 intentos fallidos la cuenta se bloquea 10 minutos.
       - Si el usuario tiene un solo rol, ese rol queda activo de inmediato.
       - Si tiene varios roles (incluido Administrador), se devuelve un token
         sin rol activo y `requiere_seleccion_rol: true`.
       - Si la contraseña es temporal (`debe_cambiar_password`), el frontend
         muestra primero la pantalla de cambio obligatorio; hasta entonces
         ningún endpoint con permisos responde.
    2. POST /api/auth/seleccionar-rol  -> el usuario elige uno de sus roles.
       Se emite un nuevo JWT con el `rol_activo_id`, que es la fuente de
       verdad para las validaciones de permisos.
    3. POST /api/auth/cambiar-password -> cambio de contraseña propio
       (obligatorio la primera vez y disponible siempre desde "Mi cuenta").
"""

import hashlib
from datetime import timezone

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required

from ..errors import ApiError
from ..extensions import db, limiter
from ..models import Rol, Usuario, ahora
from ..services.seguridad import duracion_bloqueo, max_intentos, minutos_bloqueo, validar_password

# Hash de relleno: un correo inexistente tarda lo mismo que uno real (no revela qué correos existen).
_HASH_RELLENO = None


def _verificar_relleno(password):
    global _HASH_RELLENO
    from werkzeug.security import check_password_hash, generate_password_hash

    if _HASH_RELLENO is None:
        _HASH_RELLENO = generate_password_hash("relleno-sin-uso")
    check_password_hash(_HASH_RELLENO, password)


bp = Blueprint("auth", __name__)


def _huella_sesion(usuario: Usuario):
    """Huella corta de la contraseña: si cambia (o la resetea el admin) los tokens anteriores dejan de valer."""
    return hashlib.sha256(f"{usuario.id}:{usuario.password_hash}".encode()).hexdigest()[:16]


def _hacer_token(usuario: Usuario, rol: Rol | None = None):
    """Construye un JWT. `es_admin` refleja el rol ACTIVO, no los roles del usuario."""
    additional_claims = {
        "es_admin": bool(rol and rol.es_admin),
        "rol_activo_id": rol.id if rol else None,
        "sv": _huella_sesion(usuario),
    }
    return create_access_token(identity=str(usuario.id), additional_claims=additional_claims)


def _aware(dt):
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@bp.post("/auth/login")
@limiter.limit(lambda: current_app.config["LIMITE_LOGIN"])
def login():
    """Ingreso único para todo el personal (administradores incluidos)."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        raise ApiError("credenciales_invalidas", "Correo y contraseña son obligatorios.", 400)

    usuario = Usuario.query.filter_by(email=email).first()
    if not usuario:
        _verificar_relleno(password)
        raise ApiError("credenciales_invalidas", "Correo o contraseña incorrectos.", 401)

    password_ok = usuario.check_password(password)

    # Cuenta desactivada: no suma intentos y solo se informa si la clave es correcta,
    # para no revelar a terceros qué correos existen.
    if usuario.estado != "activo":
        if not password_ok:
            raise ApiError("credenciales_invalidas", "Correo o contraseña incorrectos.", 401)
        raise ApiError("usuario_inactivo", "El usuario está desactivado. Contacte al administrador.", 403)

    bloqueado_hasta = _aware(usuario.bloqueado_hasta)
    if bloqueado_hasta and bloqueado_hasta > ahora():
        # Durante el bloqueo no se cuentan más intentos y una clave errada recibe
        # la misma respuesta genérica que un correo inexistente.
        if not password_ok:
            raise ApiError("credenciales_invalidas", "Correo o contraseña incorrectos.", 401)
        minutos = max(1, int((bloqueado_hasta - ahora()).total_seconds() // 60) + 1)
        raise ApiError(
            "cuenta_bloqueada",
            f"Cuenta bloqueada temporalmente por intentos fallidos. Intente nuevamente en {minutos} min.",
            423,
        )

    if not password_ok:
        usuario.intentos_fallidos = (usuario.intentos_fallidos or 0) + 1
        restantes = max_intentos() - usuario.intentos_fallidos
        if restantes <= 0:
            usuario.bloqueado_hasta = ahora() + duracion_bloqueo()
            usuario.intentos_fallidos = 0
            db.session.commit()
            raise ApiError(
                "cuenta_bloqueada",
                f"Demasiados intentos fallidos. La cuenta se bloqueó por {minutos_bloqueo()} minutos.",
                423,
            )
        db.session.commit()
        # Mismo mensaje que un correo inexistente: no se confirma que la cuenta exista.
        raise ApiError("credenciales_invalidas", "Correo o contraseña incorrectos.", 401)

    if not usuario.roles:
        raise ApiError("sin_roles", "Este usuario aún no tiene roles asignados. Contacte al administrador.", 403)

    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    usuario.ultimo_acceso = ahora()
    db.session.commit()

    # Un solo rol: se activa automáticamente
    if len(usuario.roles) == 1:
        rol = usuario.roles[0]
        return jsonify(
            access_token=_hacer_token(usuario, rol),
            usuario=usuario.to_dict(),
            rol_activo=rol.to_dict(),
            requiere_seleccion_rol=False,
        )

    # Varios roles: token temporal SIN rol activo hasta que elija uno
    return jsonify(
        access_token=_hacer_token(usuario, None),
        usuario=usuario.to_dict(),
        rol_activo=None,
        requiere_seleccion_rol=True,
    )


@bp.post("/auth/seleccionar-rol")
@jwt_required()
def seleccionar_rol():
    """Confirma el rol con el que se trabajará durante la sesión."""
    data = request.get_json(silent=True) or {}
    rol_id = data.get("rol_id")
    if not rol_id:
        raise ApiError("rol_requerido", "Debe indicar el rol a activar.", 400)

    usuario = _usuario_actual_o_404()
    if usuario.estado != "activo":
        raise ApiError("usuario_inactivo", "El usuario está desactivado. Contacte al administrador.", 403)
    rol = db.session.get(Rol, int(rol_id))
    if not rol or rol not in usuario.roles:
        raise ApiError("rol_no_asignado", "El rol seleccionado no está asignado al usuario.", 403)

    return jsonify(access_token=_hacer_token(usuario, rol), usuario=usuario.to_dict(), rol_activo=rol.to_dict())


@bp.post("/auth/liberar-rol")
@jwt_required()
def liberar_rol():
    """ "Cambiar rol": emite un token SIN rol activo.

    Así el rol anterior deja de valer aunque el usuario recargue la página
    en la pantalla de selección.
    """
    usuario = _usuario_actual_o_404()
    if usuario.estado != "activo":
        raise ApiError("usuario_inactivo", "El usuario está desactivado. Contacte al administrador.", 403)
    return jsonify(access_token=_hacer_token(usuario, None), usuario=usuario.to_dict(), rol_activo=None)


@bp.get("/auth/me")
@jwt_required()
def me():
    """Devuelve el usuario y su rol activo (según el token)."""
    usuario = _usuario_actual_o_404()
    if usuario.estado != "activo":
        raise ApiError("usuario_inactivo", "El usuario está desactivado. Contacte al administrador.", 403)
    rol_activo = _rol_activo()
    # Si al usuario le retiraron el rol activo, la sesión vuelve a pedir selección
    if rol_activo and rol_activo not in usuario.roles:
        rol_activo = None
    return jsonify(usuario=usuario.to_dict(), rol_activo=rol_activo.to_dict() if rol_activo else None)


@bp.post("/auth/cambiar-password")
@jwt_required()
def cambiar_password():
    """Cambio de contraseña propio. Exige la contraseña actual."""
    usuario = _usuario_actual_o_404()
    data = request.get_json(silent=True) or {}
    actual = data.get("password_actual") or ""
    nueva = data.get("password_nueva") or ""

    if not usuario.check_password(actual):
        raise ApiError("password_incorrecta", "La contraseña actual no es correcta.", 400)
    if actual == nueva:
        raise ApiError("password_repetida", "La nueva contraseña debe ser distinta de la actual.", 400)
    validar_password(nueva, usuario)

    usuario.set_password(nueva)
    usuario.debe_cambiar_password = False
    db.session.commit()
    # Nuevo token (con la nueva huella) para esta pestaña; las demás sesiones quedan cerradas.
    return jsonify(
        usuario=usuario.to_dict(),
        access_token=_hacer_token(usuario, _rol_activo()),
        mensaje="Contraseña actualizada correctamente.",
    )


@bp.patch("/auth/perfil")
@jwt_required()
def actualizar_perfil():
    """El propio usuario puede actualizar su teléfono y dirección (no nombre ni DNI)."""
    usuario = _usuario_actual_o_404()
    data = request.get_json(silent=True) or {}
    for campo, largo in (("telefono", 20), ("direccion", 200)):
        if campo in data:
            valor = (data.get(campo) or "").strip()[:largo]
            setattr(usuario, campo, valor or None)
    db.session.commit()
    return jsonify(usuario=usuario.to_dict())


@bp.put("/auth/pin")
@jwt_required()
def configurar_pin():
    """El supervisor define o cambia su PIN de autorización (exige su contraseña)."""
    from ..services.autorizacion import validar_pin_nuevo

    usuario = _usuario_actual_o_404()
    if not usuario.puede_autorizar:
        raise ApiError(
            "permiso_insuficiente", "Solo los supervisores y administradores tienen PIN de autorización.", 403
        )
    data = request.get_json(silent=True) or {}
    if not usuario.check_password(data.get("password_actual") or ""):
        raise ApiError("password_incorrecta", "La contraseña actual no es correcta.", 400)
    pin = validar_pin_nuevo(data.get("pin"), usuario)
    usuario.set_pin(pin)
    db.session.commit()
    return jsonify(usuario=usuario.to_dict(), mensaje="PIN de autorización actualizado.")


# ---- Utilidades para otros blueprints ----


def _rol_activo():
    claims = get_jwt()
    rol_id = claims.get("rol_activo_id")
    return db.session.get(Rol, rol_id) if rol_id else None


def _usuario_actual():
    # Los tokens de clientes de la tienda online ("cliente:<id>") no sirven en el sistema interno
    if get_jwt().get("tipo") == "cliente" or not str(get_jwt_identity()).isdigit():
        raise ApiError("token_de_cliente", "Esta sesión es de la tienda online, no del sistema de caja.", 403)
    usuario = db.session.get(Usuario, int(get_jwt_identity()))
    if usuario and get_jwt().get("sv") != _huella_sesion(usuario):
        raise ApiError("sesion_revocada", "Tu contraseña cambió. Vuelve a iniciar sesión.", 401)
    return usuario


def _usuario_actual_o_404():
    usuario = _usuario_actual()
    if not usuario:
        raise ApiError("usuario_no_encontrado", "El usuario de la sesión ya no existe.", 401)
    return usuario


def require_permiso(codigo):
    """Valida que el rol activo tenga el permiso indicado."""
    rol = _rol_activo()
    if not rol:
        raise ApiError("rol_no_seleccionado", "Debe seleccionar un rol para operar.", 403)
    usuario = _usuario_actual()
    if not usuario or usuario.estado != "activo" or rol not in usuario.roles:
        raise ApiError("rol_no_asignado", "El rol activo ya no está asignado a este usuario.", 403)
    if usuario.debe_cambiar_password:
        raise ApiError("cambio_password_requerido", "Debe cambiar su contraseña temporal antes de continuar.", 403)
    if codigo not in {p.codigo for p in rol.permisos}:
        raise ApiError(
            "permiso_insuficiente",
            f"El rol activo ({rol.nombre}) no tiene el permiso '{codigo}'.",
            403,
        )
    return rol
