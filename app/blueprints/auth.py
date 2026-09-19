"""Autenticación con un único punto de ingreso.

Flujo:
    1. POST /api/auth/login            -> valida correo y contraseña.
       - Si el usuario tiene un solo rol, ese rol queda activo de inmediato.
       - Si tiene varios roles (incluido Administrador), se devuelve un token
         sin rol activo y `requiere_seleccion_rol: true`.
    2. POST /api/auth/seleccionar-rol  -> el usuario elige uno de sus roles
       (el rol Administrador se elige aquí como cualquier otro). Se emite un
       nuevo JWT con el `rol_activo_id`, que es la fuente de verdad para las
       validaciones de permisos.
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required

from ..errors import ApiError
from ..models import Rol, Usuario

bp = Blueprint("auth", __name__)


def _hacer_token(usuario: Usuario, rol: Rol | None = None):
    """Construye un JWT. `es_admin` refleja el rol ACTIVO, no los roles del usuario."""
    additional_claims = {
        "es_admin": bool(rol and rol.es_admin),
        "rol_activo_id": rol.id if rol else None,
    }
    return create_access_token(identity=str(usuario.id), additional_claims=additional_claims)


@bp.post("/auth/login")
def login():
    """Ingreso único para todo el personal (administradores incluidos)."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        raise ApiError("credenciales_invalidas", "Correo y contraseña son obligatorios.", 400)

    usuario = Usuario.query.filter_by(email=email).first()
    if not usuario or not usuario.check_password(password):
        raise ApiError("credenciales_invalidas", "Correo o contraseña incorrectos.", 401)
    if usuario.estado != "activo":
        raise ApiError("usuario_inactivo", "El usuario está desactivado. Contacte al administrador.", 403)
    if not usuario.roles:
        raise ApiError(
            "sin_roles",
            "Este usuario aún no tiene roles asignados. Contacte al administrador.",
            403,
        )

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

    usuario = Usuario.query.get_or_404(int(get_jwt_identity()))
    if usuario.estado != "activo":
        raise ApiError("usuario_inactivo", "El usuario está desactivado. Contacte al administrador.", 403)
    rol = Rol.query.get(int(rol_id))
    if not rol or rol not in usuario.roles:
        raise ApiError("rol_no_asignado", "El rol seleccionado no está asignado al usuario.", 403)

    return jsonify(access_token=_hacer_token(usuario, rol), usuario=usuario.to_dict(), rol_activo=rol.to_dict())


@bp.get("/auth/me")
@jwt_required()
def me():
    """Devuelve el usuario y su rol activo (según el token)."""
    usuario = Usuario.query.get_or_404(int(get_jwt_identity()))
    rol_activo = _rol_activo()
    # Si al usuario le retiraron el rol activo, la sesión vuelve a pedir selección
    if rol_activo and rol_activo not in usuario.roles:
        rol_activo = None
    return jsonify(usuario=usuario.to_dict(), rol_activo=rol_activo.to_dict() if rol_activo else None)


# ---- Utilidades para otros blueprints ----

def _rol_activo():
    claims = get_jwt()
    rol_id = claims.get("rol_activo_id")
    return Rol.query.get(rol_id) if rol_id else None


def _usuario_actual():
    return Usuario.query.get(int(get_jwt_identity()))


def require_permiso(codigo):
    """Valida que el rol activo tenga el permiso indicado."""
    rol = _rol_activo()
    if not rol:
        raise ApiError("rol_no_seleccionado", "Debe seleccionar un rol para operar.", 403)
    usuario = _usuario_actual()
    if not usuario or usuario.estado != "activo" or rol not in usuario.roles:
        raise ApiError("rol_no_asignado", "El rol activo ya no está asignado a este usuario.", 403)
    if codigo not in {p.codigo for p in rol.permisos}:
        raise ApiError(
            "permiso_insuficiente",
            f"El rol activo ({rol.nombre}) no tiene el permiso '{codigo}'.",
            403,
        )
    return rol
