from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .extensions import cors, db, jwt, limiter, migrate
from .errors import register_error_handlers


def create_app(config_object="config.Config"):
    app = Flask(__name__)
    app.config.from_object(config_object)

    # Render pasa por un proxy: sin esto todas las peticiones parecen venir de la misma IP
    # y el límite de intentos por IP no serviría.
    proxies = app.config.get("PROXIES_CONFIABLES", 0)
    if proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    limiter.init_app(app)

    # ---- CORS: solo los orígenes del frontend configurados en FRONTEND_ORIGIN ----
    raw_origin = app.config.get("FRONTEND_ORIGIN") or ""
    if raw_origin.strip() == "*":
        cors_origins = "*"
        if app.config.get("EN_PRODUCCION"):
            app.logger.warning("FRONTEND_ORIGIN='*': configure las URLs de Vercel para restringir el acceso.")
    else:
        cors_origins = [o.strip().rstrip("/") for o in raw_origin.split(",") if o.strip()]

    cors.init_app(
        app,
        resources={r"/*": {"origins": cors_origins}},
        supports_credentials=False,
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    def _origen_permitido(origin):
        if not origin:
            return None
        if cors_origins == "*":
            return origin
        return origin if origin in cors_origins else None

    def _cabeceras_cors(response):
        origin = _origen_permitido(request.headers.get("Origin"))
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        return response

    def _cabeceras_seguridad(response):
        """Cabeceras recomendadas (OWASP) para una API JSON."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.before_request
    def handle_preflight():
        if request.method == "OPTIONS":
            return _cabeceras_cors(app.make_default_options_response())

    @app.after_request
    def set_cors_headers(response):
        return _cabeceras_seguridad(_cabeceras_cors(response))

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
    from .blueprints.reportes import bp as reportes_bp
    from .blueprints.cajas import bp as cajas_bp
    from .blueprints.autorizaciones import bp as autorizaciones_bp
    from .blueprints.tienda import bp as tienda_bp
    from .blueprints.promociones import bp as promociones_bp
    from .blueprints.pedidos_online import bp as pedidos_online_bp
    from .blueprints.devoluciones import bp as devoluciones_bp

    for blueprint in (
        auth_bp,
        usuarios_bp,
        roles_bp,
        productos_bp,
        ventas_bp,
        reportes_bp,
        cajas_bp,
        autorizaciones_bp,
        tienda_bp,
        promociones_bp,
        pedidos_online_bp,
        devoluciones_bp,
    ):
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
            db.session.rollback()
            app.logger.warning("No fue posible ejecutar seed automático: %s", exc)

    @app.get("/")
    def root():
        return {"servicio": "coolbox-backend", "estado": "ok"}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/health/db")
    def health_db():
        """Verifica la conexión con la base de datos (útil para diagnosticar Render/Supabase)."""
        from sqlalchemy import text

        try:
            db.session.execute(text("SELECT 1"))
            return {"status": "ok", "database": "conectada"}
        except Exception as exc:  # pragma: no cover
            app.logger.error("Fallo de conexión a BD: %s", exc)
            return {"status": "error", "database": "sin conexión"}, 503

    return app
