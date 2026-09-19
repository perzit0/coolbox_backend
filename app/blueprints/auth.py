"""Autenticación separada para administrador y usuarios normales.

Existen dos endpoints de login:
    - POST /api/auth/login-admin   -> solo acepta usuarios con rol Administrador
    - POST /api/auth/login-usuario -> solo acepta usuarios NO administradores

Al iniciar sesión se devuelve el usuario y sus roles activos. El usuario
debe elegir uno de ellos antes de operar. La elección se envía a
    POST /api/auth/seleccionar-rol
que genera un nuevo JWT donde el `rol_activo` queda embebido y se convierte
en la fuente de verdad para las validaciones de permisos.
"""
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required

from ..errors import ApiError
from ..models import Rol, Usuario

bp = Blueprint("auth", __name__)


def _hacer_token(usuario: Usuario, rol_id=None):
    """Construye un JWT con los claims necesarios."""
    additional_claims = {
        "es_admin": usuario.es_administrador,
        "rol_activo_id": rol_id,
    }
    return create_access_token(identity=str(usuario.id), additional_claims=additional_claims)


@bp.post("/auth/login-admin")
def login_admin():
    """Ingreso exclusivo del panel administrativo."""
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
    if not usuario.es_administrador:
        raise ApiError("acceso_denegado", "Este acceso es exclusivo para administradores.", 403)

    # El administrador entra directamente con su rol admin activo
    rol_admin = next((r for r in usuario.roles if r.es_admin), None)
    token = _hacer_token(usuario, rol_id=rol_admin.id if rol_admin else None)
    return jsonify(
        access_token=token,
        usuario=usuario.to_dict(),
        rol_activo=rol_admin.to_dict() if rol_admin else None,
    )


@bp.post("/auth/login-usuario")
def login_usuario():
    """Ingreso del personal de tienda (no administradores)."""
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
    if usuario.es_administrador:
        raise ApiError(
            "acceso_denegado",
            "Los administradores deben usar el panel administrativo.",
            403,
        )
    if not usuario.roles:
        raise ApiError(
            "sin_roles",
            "Este usuario aún no tiene roles asignados. Contacte al administrador.",
            403,
        )

    # Todavía no elige rol; le entregamos un token temporal SIN rol activo
    token = _hacer_token(usuario, rol_id=None)
    return jsonify(
        access_token=token,
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
    rol = Rol.query.get(int(rol_id))
    if not rol or rol not in usuario.roles:
        raise ApiError("rol_no_asignado", "El rol seleccionado no está asignado al usuario.", 403)

    token = _hacer_token(usuario, rol_id=rol.id)
    return jsonify(access_token=token, usuario=usuario.to_dict(), rol_activo=rol.to_dict())


@bp.get("/auth/me")
@jwt_required()
def me():
    """Devuelve el usuario y su rol activo (según el token)."""
    usuario = Usuario.query.get_or_404(int(get_jwt_identity()))
    claims = get_jwt()
    rol_activo_id = claims.get("rol_activo_id")
    rol_activo = Rol.query.get(rol_activo_id) if rol_activo_id else None
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
    if codigo not in {p.codigo for p in rol.permisos}:
        raise ApiError(
            "permiso_insuficiente",
            f"El rol activo ({rol.nombre}) no tiene el permiso '{codigo}'.",
            403,
        )
    return rol
