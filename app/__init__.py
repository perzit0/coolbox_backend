from flask import Flask, request

from .extensions import cors, db, jwt, migrate
from .errors import register_error_handlers


def create_app(config_object="config.Config"):
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    # ---- CORS ----
    raw_origin = app.config.get("FRONTEND_ORIGIN", "*")
    if not raw_origin or raw_origin.strip() == "*":
        cors_origins = "*"
    else:
        cors_origins = [o.strip() for o in raw_origin.split(",") if o.strip()]

    cors.init_app(
        app,
        resources={r"/*": {"origins": cors_origins}},
        supports_credentials=False,
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    @app.before_request
    def handle_preflight():
        if request.method == "OPTIONS":
            response = app.make_default_options_response()
            origin = request.headers.get("Origin")
            response.headers["Access-Control-Allow-Origin"] = origin if origin else "*"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            return response

    @app.after_request
    def set_cors_headers(response):
        origin = request.headers.get("Origin")
        response.headers["Access-Control-Allow-Origin"] = origin if origin else "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        return response

    register_error_handlers(app)

    # ---- JWT handlers ----
    @jwt.unauthorized_loader
    def missing_token(reason):
        return {"error": "autenticacion_requerida", "detail": reason}, 401

    @jwt.invalid_token_loader
    def invalid_token(reason):
        return {"error": "token_invalido", "detail": reason}, 401

    @jwt.expired_token_loader
    def expired_token(_header, _payload):
        return {"error": "token_expirado", "detail": "La sesión expiró. Inicie sesión nuevamente."}, 401

    # ---- Blueprints ----
    from .blueprints.auth import bp as auth_bp
    from .blueprints.usuarios import bp as usuarios_bp
    from .blueprints.roles import bp as roles_bp
    from .blueprints.productos import bp as productos_bp
    from .blueprints.ventas import bp as ventas_bp

    for blueprint in (auth_bp, usuarios_bp, roles_bp, productos_bp, ventas_bp):
        app.register_blueprint(blueprint, url_prefix="/api")

    # Auto-seed en el primer arranque (útil para Render + Supabase, ya que
    # Supabase no soporta bien Alembic si se usa el pooler; con create_all
    # más el catálogo maestro tenemos un despliegue reproducible).
    with app.app_context():
        from . import models  # noqa: F401 - registra los modelos
        try:
            db.create_all()
            from .services.schema import aplicar_migraciones_ligeras
            aplicar_migraciones_ligeras()
            from scripts.seed_catalogo import seed_catalogo_maestro
            seed_catalogo_maestro()
        except Exception as exc:  # no bloquear el arranque si la BD no está lista
            app.logger.warning("No fue posible ejecutar seed automático: %s", exc)

    @app.get("/")
    def root():
        return {"servicio": "coolbox-backend", "estado": "ok", "docs": "/health"}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
