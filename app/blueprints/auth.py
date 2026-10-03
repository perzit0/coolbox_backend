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
from datetime import timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Rol, Usuario, ahora
from ..services.seguridad import MAX_INTENTOS, MINUTOS_BLOQUEO, duracion_bloqueo, validar_password

bp = Blueprint("auth", __name__)


def _hacer_token(usuario: Usuario, rol: Rol | None = None):
    """Construye un JWT. `es_admin` refleja el rol ACTIVO, no los roles del usuario."""
    additional_claims = {
        "es_admin": bool(rol and rol.es_admin),
        "rol_activo_id": rol.id if rol else None,
    }
    return create_access_token(identity=str(usuario.id), additional_claims=additional_claims)


def _aware(dt):
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@bp.post("/auth/login")
def login():
    """Ingreso único para todo el personal (administradores incluidos)."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        raise ApiError("credenciales_invalidas", "Correo y contraseña son obligatorios.", 400)

    usuario = Usuario.query.filter_by(email=email).first()
    if not usuario:
        raise ApiError("credenciales_invalidas", "Correo o contraseña incorrectos.", 401)

    bloqueado_hasta = _aware(usuario.bloqueado_hasta)
    if bloqueado_hasta and bloqueado_hasta > ahora():
        minutos = max(1, int((bloqueado_hasta - ahora()).total_seconds() // 60) + 1)
        raise ApiError(
            "cuenta_bloqueada",
            f"Cuenta bloqueada temporalmente por intentos fallidos. Intente nuevamente en {minutos} min.",
            423,
        )

    if not usuario.check_password(password):
        usuario.intentos_fallidos = (usuario.intentos_fallidos or 0) + 1
        restantes = MAX_INTENTOS - usuario.intentos_fallidos
        if restantes <= 0:
            usuario.bloqueado_hasta = ahora() + duracion_bloqueo()
            usuario.intentos_fallidos = 0
            db.session.commit()
            raise ApiError(
                "cuenta_bloqueada",
                f"Demasiados intentos fallidos. La cuenta se bloqueó por {MINUTOS_BLOQUEO} minutos.",
                423,
            )
        db.session.commit()
        aviso = f" Le queda{'n' if restantes > 1 else ''} {restantes} intento{'s' if restantes > 1 else ''}." if restantes <= 2 else ""
        raise ApiError("credenciales_invalidas", f"Correo o contraseña incorrectos.{aviso}", 401)

    if usuario.estado != "activo":
        raise ApiError("usuario_inactivo", "El usuario está desactivado. Contacte al administrador.", 403)
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
    return jsonify(usuario=usuario.to_dict(), mensaje="Contraseña actualizada correctamente.")


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


# ---- Utilidades para otros blueprints ----

def _rol_activo():
    claims = get_jwt()
    rol_id = claims.get("rol_activo_id")
    return db.session.get(Rol, rol_id) if rol_id else None


def _usuario_actual():
    return db.session.get(Usuario, int(get_jwt_identity()))


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
