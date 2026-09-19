"""CRUD de usuarios. Solo el administrador puede crear/editar usuarios.

El correo se genera automáticamente en el servidor a partir del nombre y
apellidos siguiendo la regla del equipo:
    Juan Daniel Pérez Lozano -> jperezl@coolbox.com.pe
    (duplicado)              -> jperezl1@coolbox.com.pe
"""
import secrets
import string

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from ..errors import ApiError
from ..extensions import db
from ..models import Rol, Usuario
from ..services.email_generator import build_email
from .auth import require_permiso

bp = Blueprint("usuarios", __name__)


def _generar_password_temporal(n=10):
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(n))


@bp.get("/usuarios")
@jwt_required()
def listar_usuarios():
    require_permiso("usuarios.ver")
    usuarios = Usuario.query.order_by(Usuario.apellido_paterno.asc(), Usuario.nombres.asc()).all()
    return jsonify(usuarios=[u.to_dict() for u in usuarios])


@bp.get("/usuarios/<int:usuario_id>")
@jwt_required()
def obtener_usuario(usuario_id):
    require_permiso("usuarios.ver")
    usuario = Usuario.query.get_or_404(usuario_id)
    return jsonify(usuario=usuario.to_dict())


@bp.post("/usuarios")
@jwt_required()
def crear_usuario():
    require_permiso("usuarios.crear")
    data = request.get_json(silent=True) or {}

    dni = (data.get("dni") or "").strip()
    nombres = (data.get("nombres") or "").strip()
    apellido_paterno = (data.get("apellido_paterno") or "").strip()
    apellido_materno = (data.get("apellido_materno") or "").strip()
    telefono = (data.get("telefono") or "").strip() or None
    direccion = (data.get("direccion") or "").strip() or None
    roles_ids = data.get("roles_ids") or []

    if not dni or not nombres or not apellido_paterno or not apellido_materno:
        raise ApiError("datos_invalidos", "DNI, nombres, apellido paterno y apellido materno son obligatorios.", 400)
    if not isinstance(roles_ids, list) or not roles_ids:
        raise ApiError("roles_requeridos", "Debe asignar al menos un rol.", 400)

    if Usuario.query.filter_by(dni=dni).first():
        raise ApiError("dni_duplicado", f"El DNI {dni} ya se encuentra registrado.", 409)

    # Validar roles existentes
    roles = Rol.query.filter(Rol.id.in_(roles_ids)).all()
    if len(roles) != len(roles_ids):
        raise ApiError("rol_no_encontrado", "Algún rol indicado no existe.", 400)

    # Generar correo único
    dominio = current_app.config.get("EMAIL_DOMAIN", "coolbox.com.pe")
    email = build_email(
        nombres,
        apellido_paterno,
        apellido_materno,
        dominio,
        existe_email=lambda e: Usuario.query.filter_by(email=e).first() is not None,
    )

    # Contraseña: la que envíe el admin o una temporal
    password = (data.get("password") or "").strip()
    if not password:
        password = _generar_password_temporal()

    usuario = Usuario(
        dni=dni,
        nombres=nombres,
        apellido_paterno=apellido_paterno,
        apellido_materno=apellido_materno,
        email=email,
        telefono=telefono,
        direccion=direccion,
    )
    usuario.set_password(password)
    usuario.roles = roles
    db.session.add(usuario)
    db.session.commit()

    return (
        jsonify(
            usuario=usuario.to_dict(),
            credenciales={"email": email, "password": password},
        ),
        201,
    )


@bp.patch("/usuarios/<int:usuario_id>")
@jwt_required()
def editar_usuario(usuario_id):
    require_permiso("usuarios.editar")
    usuario = Usuario.query.get_or_404(usuario_id)
    data = request.get_json(silent=True) or {}

    for campo in ("nombres", "apellido_paterno", "apellido_materno", "telefono", "direccion"):
        if campo in data and data[campo] is not None:
            setattr(usuario, campo, (data[campo] or "").strip() or None if campo in ("telefono", "direccion") else data[campo].strip())

    if "roles_ids" in data:
        roles_ids = data.get("roles_ids") or []
        if not roles_ids:
            raise ApiError("roles_requeridos", "Debe asignar al menos un rol.", 400)
        roles = Rol.query.filter(Rol.id.in_(roles_ids)).all()
        if len(roles) != len(roles_ids):
            raise ApiError("rol_no_encontrado", "Algún rol indicado no existe.", 400)
        usuario.roles = roles

    if "estado" in data and data["estado"] in ("activo", "inactivo"):
        usuario.estado = data["estado"]

    if data.get("password"):
        usuario.set_password(data["password"])

    db.session.commit()
    return jsonify(usuario=usuario.to_dict())


@bp.post("/usuarios/<int:usuario_id>/reset-password")
@jwt_required()
def reset_password(usuario_id):
    require_permiso("usuarios.editar")
    usuario = Usuario.query.get_or_404(usuario_id)
    nueva = _generar_password_temporal()
    usuario.set_password(nueva)
    db.session.commit()
    return jsonify(usuario=usuario.to_dict(), password=nueva)


@bp.delete("/usuarios/<int:usuario_id>")
@jwt_required()
def desactivar_usuario(usuario_id):
    require_permiso("usuarios.desactivar")
    usuario = Usuario.query.get_or_404(usuario_id)
    usuario.estado = "inactivo"
    db.session.commit()
    return jsonify(usuario=usuario.to_dict())
