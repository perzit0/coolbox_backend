import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


def database_url():
    """Normaliza el string de conexión para SQLAlchemy + psycopg 3.

    Supabase entrega URLs `postgresql://...` y Render puede entregar
    `postgres://...`; ambas se convierten a `postgresql+psycopg://` para
    usar el driver moderno psycopg 3.
    """
    url = os.getenv("DATABASE_URL", "sqlite:///coolbox.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Config:
    SQLALCHEMY_DATABASE_URI = database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Supabase requiere renovar conexiones para evitar timeouts (pool_pre_ping)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
    SECRET_KEY = os.getenv("SECRET_KEY", "unsafe-development-key")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=8)
    FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
    EMAIL_DOMAIN = os.getenv("EMAIL_DOMAIN", "coolbox.com.pe")
    IGV_RATE = float(os.getenv("IGV_RATE", "0.18"))
    # Tienda online: clientes
    CLIENTE_TOKEN_DIAS = int(os.getenv("CLIENTE_TOKEN_DIAS", "30"))
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")       # inicio de sesión con Google (opcional)
    SMTP_HOST = os.getenv("SMTP_HOST", "")                     # código por correo (opcional), p. ej. smtp.gmail.com
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "")
    PAGOEFECTIVO_HORAS = int(os.getenv("PAGOEFECTIVO_HORAS", "24"))
    # Caja
    CUADRE_CIEGO = os.getenv("CUADRE_CIEGO", "1") not in ("0", "false", "False", "no")
    LIMITE_EFECTIVO_CAJA = float(os.getenv("LIMITE_EFECTIVO_CAJA", "1500"))
    DESCUENTO_MAX_CAJERO = float(os.getenv("DESCUENTO_MAX_CAJERO", "5"))  # % sin autorización
    DESCUENTO_MAX_TOTAL = float(os.getenv("DESCUENTO_MAX_TOTAL", "30"))   # % tope con autorización

    # SUNAT: API de facturación (fact_api, Laravel + Greenter) en entorno de pruebas (beta).
    # Nada se escribe en el código: todo viene de variables de entorno.
    SUNAT_API_URL = os.getenv("SUNAT_API_URL", "")                     # p. ej. http://127.0.0.1:8000
    SUNAT_API_EMAIL = os.getenv("SUNAT_API_EMAIL", "")
    SUNAT_API_PASSWORD = os.getenv("SUNAT_API_PASSWORD", "")
    SUNAT_LOGIN_PATH = os.getenv("SUNAT_LOGIN_PATH", "/api/auth/login")
    SUNAT_API_PREFIX = os.getenv("SUNAT_API_PREFIX", "/api/v1")
    SUNAT_TIMEOUT = int(os.getenv("SUNAT_TIMEOUT", "30"))
    SUNAT_COMPANY_ID = int(os.getenv("SUNAT_COMPANY_ID", "1"))
    SUNAT_BRANCH_ID = int(os.getenv("SUNAT_BRANCH_ID", "1"))
    SUNAT_SERIE_BOLETA = os.getenv("SUNAT_SERIE_BOLETA", "B001")
    SUNAT_SERIE_FACTURA = os.getenv("SUNAT_SERIE_FACTURA", "F001")
    # Notas de crédito: la serie empieza con B si modifica una boleta y con F si modifica una factura.
    SUNAT_SERIE_NC_BOLETA = os.getenv("SUNAT_SERIE_NC_BOLETA", "BC01")
    SUNAT_SERIE_NC_FACTURA = os.getenv("SUNAT_SERIE_NC_FACTURA", "FC01")
    SUNAT_REINTENTOS = int(os.getenv("SUNAT_REINTENTOS", "3"))
    COMPROBANTES_DIR = os.getenv("COMPROBANTES_DIR", "comprobantes")

    # Devoluciones: días desde la compra en que se aceptan (política de la tienda).
    DEVOLUCION_DIAS_MAX = int(os.getenv("DEVOLUCION_DIAS_MAX", "7"))
