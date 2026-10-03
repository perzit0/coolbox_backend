"""CRUD de usuarios. Solo el administrador puede crear/editar usuarios.

El correo se genera automáticamente en el servidor a partir del nombre y
apellidos siguiendo la regla del equipo:
    Juan Daniel Pérez Lozano -> jperezl@coolbox.com.pe
    (duplicado)              -> jperezl1@coolbox.com.pe

La contraseña inicial es temporal: el usuario debe cambiarla en su primer
ingreso (igual que al restablecerla).
"""
import re

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Rol, Usuario
from ..services.email_generator import build_email
from ..services.seguridad import generar_password_temporal, validar_password
from .auth import _usuario_actual, require_permiso

bp = Blueprint("usuarios", __name__)

DNI_REGEX = re.compile(r"^\d{8}$")
TELEFONO_REGEX = re.compile(r"^9\d{8}$")


def _texto(data, campo, largo, obligatorio=False, etiqueta=None):
    valor = re.sub(r"\s+", " ", (data.get(campo) or "").strip())[:largo]
    if obligatorio and not valor:
        raise ApiError("datos_invalidos", f"El campo {etiqueta or campo} es obligatorio.", 400)
    return valor or None


def _validar_telefono(telefono):
    if telefono and not TELEFONO_REGEX.match(telefono):
        raise ApiError("telefono_invalido", "El celular debe tener 9 dígitos y empezar con 9.", 400)


def _roles_desde(roles_ids):
    if not isinstance(roles_ids, list) or not roles_ids:
        raise ApiError("roles_requeridos", "Debe asignar al menos un rol.", 400)
    roles = Rol.query.filter(Rol.id.in_(roles_ids)).all()
    if len(roles) != len(set(roles_ids)):
        raise ApiError("rol_no_encontrado", "Algún rol indicado no existe.", 400)
    return roles


@bp.get("/usuarios")
@jwt_required()
def listar_usuarios():
    require_permiso("usuarios.ver")
    q = (request.args.get("q") or "").strip()
    estado = request.args.get("estado")
    query = Usuario.query
    if estado in ("activo", "inactivo"):
        query = query.filter(Usuario.estado == estado)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Usuario.nombres.ilike(like), Usuario.apellido_paterno.ilike(like),
            Usuario.apellido_materno.ilike(like), Usuario.dni.ilike(like), Usuario.email.ilike(like),
        ))
    usuarios = query.order_by(Usuario.apellido_paterno.asc(), Usuario.nombres.asc()).all()
    return jsonify(usuarios=[u.to_dict() for u in usuarios])


@bp.get("/usuarios/<int:usuario_id>")
@jwt_required()
def obtener_usuario(usuario_id):
    require_permiso("usuarios.ver")
    usuario = db.get_or_404(Usuario, usuario_id)
    return jsonify(usuario=usuario.to_dict())


@bp.get("/usuarios/preview-email")
@jwt_required()
def preview_email():
    """Correo que se generaría (considerando duplicados reales en BD)."""
    require_permiso("usuarios.crear")
    nombres = request.args.get("nombres", "")
    ap = request.args.get("apellido_paterno", "")
    am = request.args.get("apellido_materno", "")
    if not nombres.strip() or not ap.strip():
        return jsonify(email=None)
    email = build_email(
        nombres, ap, am, current_app.config.get("EMAIL_DOMAIN", "coolbox.com.pe"),
        existe_email=lambda e: Usuario.query.filter_by(email=e).first() is not None,
    )
    return jsonify(email=email)


@bp.post("/usuarios")
@jwt_required()
def crear_usuario():
    require_permiso("usuarios.crear")
    data = request.get_json(silent=True) or {}

    dni = (data.get("dni") or "").strip()
    nombres = _texto(data, "nombres", 120, True, "nombres")
    apellido_paterno = _texto(data, "apellido_paterno", 80, True, "apellido paterno")
    apellido_materno = _texto(data, "apellido_materno", 80, True, "apellido materno")
    telefono = _texto(data, "telefono", 20)
    direccion = _texto(data, "direccion", 200)

    if not DNI_REGEX.match(dni):
        raise ApiError("dni_invalido", "El DNI debe tener exactamente 8 dígitos.", 400)
    _validar_telefono(telefono)
    roles = _roles_desde(data.get("roles_ids") or [])

    if Usuario.query.filter_by(dni=dni).first():
        raise ApiError("dni_duplicado", f"El DNI {dni} ya se encuentra registrado.", 409)

    dominio = current_app.config.get("EMAIL_DOMAIN", "coolbox.com.pe")
    email = build_email(
        nombres, apellido_paterno, apellido_materno, dominio,
        existe_email=lambda e: Usuario.query.filter_by(email=e).first() is not None,
    )

    usuario = Usuario(
        dni=dni, nombres=nombres, apellido_paterno=apellido_paterno, apellido_materno=apellido_materno,
        email=email, telefono=telefono, direccion=direccion,
    )
    # Contraseña: la que defina el admin (debe cumplir la política) o una temporal
    password = (data.get("password") or "").strip()
    if password:
        validar_password(password, usuario)
    else:
        password = generar_password_temporal()
    usuario.set_password(password)
    usuario.debe_cambiar_password = True  # siempre se cambia en el primer ingreso
    usuario.roles = roles
    db.session.add(usuario)
    db.session.commit()

    return jsonify(usuario=usuario.to_dict(), credenciales={"email": email, "password": password}), 201


@bp.patch("/usuarios/<int:usuario_id>")
@jwt_required()
def editar_usuario(usuario_id):
    require_permiso("usuarios.editar")
    usuario = db.get_or_404(Usuario, usuario_id)
    actual = _usuario_actual()
    data = request.get_json(silent=True) or {}

    for campo, largo, etiqueta in (("nombres", 120, "nombres"), ("apellido_paterno", 80, "apellido paterno"),
                                   ("apellido_materno", 80, "apellido materno")):
        if campo in data:
            setattr(usuario, campo, _texto(data, campo, largo, True, etiqueta))
    if "telefono" in data:
        telefono = _texto(data, "telefono", 20)
        _validar_telefono(telefono)
        usuario.telefono = telefono
    if "direccion" in data:
        usuario.direccion = _texto(data, "direccion", 200)

    if "roles_ids" in data:
        roles = _roles_desde(data.get("roles_ids") or [])
        # Un administrador no puede quitarse a sí mismo el rol de administrador
        if actual and actual.id == usuario.id and usuario.es_administrador and not any(r.es_admin for r in roles):
            raise ApiError("operacion_no_permitida", "No puede retirarse a sí mismo el rol Administrador.", 400)
        usuario.roles = roles

    if "estado" in data and data["estado"] in ("activo", "inactivo"):
        if actual and actual.id == usuario.id and data["estado"] == "inactivo":
            raise ApiError("operacion_no_permitida", "No puede desactivar su propia cuenta.", 400)
        usuario.estado = data["estado"]

    db.session.commit()
    return jsonify(usuario=usuario.to_dict())


@bp.post("/usuarios/<int:usuario_id>/reset-password")
@jwt_required()
def reset_password(usuario_id):
    require_permiso("usuarios.editar")
    usuario = db.get_or_404(Usuario, usuario_id)
    nueva = generar_password_temporal()
    usuario.set_password(nueva)
    usuario.debe_cambiar_password = True
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    db.session.commit()
    return jsonify(usuario=usuario.to_dict(), password=nueva)


@bp.post("/usuarios/<int:usuario_id>/desbloquear")
@jwt_required()
def desbloquear(usuario_id):
    require_permiso("usuarios.editar")
    usuario = db.get_or_404(Usuario, usuario_id)
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    db.session.commit()
    return jsonify(usuario=usuario.to_dict())


@bp.delete("/usuarios/<int:usuario_id>")
@jwt_required()
def desactivar_usuario(usuario_id):
    require_permiso("usuarios.desactivar")
    usuario = db.get_or_404(Usuario, usuario_id)
    actual = _usuario_actual()
    if actual and actual.id == usuario.id:
        raise ApiError("operacion_no_permitida", "No puede desactivar su propia cuenta.", 400)
    usuario.estado = "inactivo"
    db.session.commit()
    return jsonify(usuario=usuario.to_dict())


@bp.post("/usuarios/<int:usuario_id>/activar")
@jwt_required()
def activar_usuario(usuario_id):
    require_permiso("usuarios.desactivar")
    usuario = db.get_or_404(Usuario, usuario_id)
    usuario.estado = "activo"
    db.session.commit()
    return jsonify(usuario=usuario.to_dict())
