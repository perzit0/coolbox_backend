from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from ..models import Rol
from .auth import _usuario_actual_o_404

bp = Blueprint("roles", __name__)


@bp.get("/roles")
@jwt_required()
def listar_roles():
    """Catálogo de roles disponibles (para asignar al crear usuarios)."""
    _usuario_actual_o_404()  # solo personal de la tienda (no sesiones de clientes)
    roles = Rol.query.order_by(Rol.es_admin.desc(), Rol.nombre.asc()).all()
    return jsonify(roles=[r.to_dict() for r in roles])
