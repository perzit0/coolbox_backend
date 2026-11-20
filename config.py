"""Configuración del backend Coolbox.

Toda regla de negocio, límite de seguridad, credencial o URL externa se lee de
variables de entorno (archivo .env en local, panel Environment en Render). En el
código solo quedan valores por defecto razonables para desarrollo; ninguna clave,
contraseña o PIN se escribe aquí.
"""

import logging
import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("coolbox.config")


def _texto(nombre, defecto=""):
    return os.getenv(nombre, defecto).strip()


def _entero(nombre, defecto):
    return int(os.getenv(nombre, str(defecto)))


def _decimal(nombre, defecto):
    return float(os.getenv(nombre, str(defecto)))


def _bool(nombre, defecto=False):
    return os.getenv(nombre, "1" if defecto else "0").strip().lower() in ("1", "true", "si", "sí", "yes")


EN_PRODUCCION = _texto("ENTORNO") == "produccion" or os.getenv("RENDER") == "true"


def _secreto(nombre):
    """Clave obligatoria en producción. Si falta, se genera una aleatoria por proceso
    (las sesiones se invalidan al reiniciar) en lugar de usar una clave fija conocida."""
    valor = _texto(nombre)
    if valor:
        if EN_PRODUCCION and len(valor) < 32:
            log.warning("%s es muy corta: use al menos 32 caracteres aleatorios.", nombre)
        return valor
    if EN_PRODUCCION:
        log.critical("%s no está configurada: se usa una clave temporal aleatoria.", nombre)
    return secrets.token_urlsafe(48)


def database_url():
    """Normaliza el string de conexión para SQLAlchemy + psycopg 3.

    Supabase entrega URLs `postgresql://...` y Render puede entregar
    `postgres://...`; ambas se convierten a `postgresql+psycopg://` para
    usar el driver moderno psycopg 3.
    """
    url = _texto("DATABASE_URL", "sqlite:///coolbox.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Config:
    # ------------------------------------------------------------ base de datos
    SQLALCHEMY_DATABASE_URI = database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Supabase requiere renovar conexiones para evitar timeouts (pool_pre_ping)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": _entero("DB_POOL_RECYCLE_SEGUNDOS", 300),
    }

    # ------------------------------------------------------------ seguridad
    EN_PRODUCCION = EN_PRODUCCION
    SECRET_KEY = _secreto("SECRET_KEY")
    JWT_SECRET_KEY = _secreto("JWT_SECRET_KEY")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=_entero("JWT_HORAS", 8))
    # Orígenes del frontend separados por coma (las URLs de Vercel). "*" solo para pruebas.
    FRONTEND_ORIGIN = _texto("FRONTEND_ORIGIN", "http://localhost:5173,http://localhost:5174")
    # Render y Vercel pasan por un proxy: se confía en él para conocer la IP real del cliente.
    PROXIES_CONFIABLES = _entero("PROXIES_CONFIABLES", 1)
    # Tamaño máximo de una petición (las fotos de producto viajan en base64)
    MAX_CONTENT_LENGTH = _entero("MAX_PETICION_MB", 3) * 1024 * 1024
    # Límite de intentos por IP en ingreso, PIN y códigos (formato de Flask-Limiter)
    LIMITE_LOGIN = _texto("LIMITE_LOGIN", "10 per minute")
    LIMITE_CONSULTAS = _texto("LIMITE_CONSULTAS", "30 per minute")
    RATELIMIT_STORAGE_URI = _texto("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    # Cuentas del personal
    LOGIN_MAX_INTENTOS = _entero("LOGIN_MAX_INTENTOS", 5)
    LOGIN_MINUTOS_BLOQUEO = _entero("LOGIN_MINUTOS_BLOQUEO", 10)
    PASSWORD_LONGITUD_MINIMA = _entero("PASSWORD_LONGITUD_MINIMA", 8)
    # PIN de supervisor
    PIN_TOKEN_SEGUNDOS = _entero("PIN_TOKEN_SEGUNDOS", 90)
    PIN_MAX_INTENTOS = _entero("PIN_MAX_INTENTOS", 3)
    PIN_MINUTOS_BLOQUEO = _entero("PIN_MINUTOS_BLOQUEO", 5)

    # ------------------------------------------------------------ usuarios iniciales
    # BD nueva: se crea un administrador con estos datos (debe cambiar la clave al entrar).
    ADMIN_INICIAL_EMAIL = _texto("ADMIN_INICIAL_EMAIL")
    ADMIN_INICIAL_PASSWORD = _texto("ADMIN_INICIAL_PASSWORD")
    # Usuarios de demostración (cajero y supervisora) solo si se pide y con clave/PIN del entorno.
    SEED_USUARIOS_DEMO = _bool("SEED_USUARIOS_DEMO")
    DEMO_PASSWORD = _texto("DEMO_PASSWORD")
    DEMO_PIN = _texto("DEMO_PIN")

    # ------------------------------------------------------------ negocio
    EMAIL_DOMAIN = _texto("EMAIL_DOMAIN", "coolbox.com.pe")
    IGV_RATE = _decimal("IGV_RATE", 0.18)
    ZONA_HORARIA_HORAS = _entero("ZONA_HORARIA_HORAS", -5)  # Perú: UTC-5 todo el año
    IMAGEN_MAX_KB = _entero("IMAGEN_MAX_KB", 1100)
    # Caja
    CUADRE_CIEGO = _bool("CUADRE_CIEGO", True)
    LIMITE_EFECTIVO_CAJA = _decimal("LIMITE_EFECTIVO_CAJA", 1500)
    CAJA_FONDO_MAXIMO = _decimal("CAJA_FONDO_MAXIMO", 5000)
    # POS de pruebas: el cobro con tarjeta, Yape o Plin se confirma en pantalla.
    POS_PROVEEDOR = _texto("POS_PROVEEDOR", "Niubiz")
    # Devoluciones: días desde la compra en que se aceptan (política de la tienda).
    DEVOLUCION_DIAS_MAX = _entero("DEVOLUCION_DIAS_MAX", 7)

    # ------------------------------------------------------------ tienda online
    CLIENTE_TOKEN_DIAS = _entero("CLIENTE_TOKEN_DIAS", 30)
    GOOGLE_CLIENT_ID = _texto("GOOGLE_CLIENT_ID")  # inicio de sesión con Google (opcional)
    GOOGLE_TOKENINFO_URL = _texto("GOOGLE_TOKENINFO_URL", "https://oauth2.googleapis.com/tokeninfo")
    SMTP_HOST = _texto("SMTP_HOST")  # código por correo (opcional)
    SMTP_PORT = _entero("SMTP_PORT", 587)
    SMTP_USER = _texto("SMTP_USER")
    SMTP_PASSWORD = _texto("SMTP_PASSWORD")
    SMTP_FROM = _texto("SMTP_FROM")
    PAGOEFECTIVO_HORAS = _entero("PAGOEFECTIVO_HORAS", 24)
    CODIGO_MINUTOS = _entero("CODIGO_MINUTOS", 10)  # vigencia del código por correo
    CODIGO_MAX_ENVIOS = _entero("CODIGO_MAX_ENVIOS", 3)  # códigos por correo en esa ventana
    CODIGO_MAX_INTENTOS = _entero("CODIGO_MAX_INTENTOS", 5)

    # ------------------------------------------------------------ SUNAT (fact_api, entorno beta)
    SUNAT_API_URL = _texto("SUNAT_API_URL")  # p. ej. http://127.0.0.1:8000
    SUNAT_API_EMAIL = _texto("SUNAT_API_EMAIL")
    SUNAT_API_PASSWORD = _texto("SUNAT_API_PASSWORD")
    SUNAT_LOGIN_PATH = _texto("SUNAT_LOGIN_PATH", "/api/auth/login")
    SUNAT_API_PREFIX = _texto("SUNAT_API_PREFIX", "/api/v1")
    SUNAT_TIMEOUT = _entero("SUNAT_TIMEOUT", 30)
    SUNAT_COMPANY_ID = _entero("SUNAT_COMPANY_ID", 1)
    SUNAT_BRANCH_ID = _entero("SUNAT_BRANCH_ID", 1)
    SUNAT_SERIE_BOLETA = _texto("SUNAT_SERIE_BOLETA", "B001")
    SUNAT_SERIE_FACTURA = _texto("SUNAT_SERIE_FACTURA", "F001")
    # Notas de crédito: la serie empieza con B si modifica una boleta y con F si modifica una factura.
    SUNAT_SERIE_NC_BOLETA = _texto("SUNAT_SERIE_NC_BOLETA", "BC01")
    SUNAT_SERIE_NC_FACTURA = _texto("SUNAT_SERIE_NC_FACTURA", "FC01")
    SUNAT_REINTENTOS = _entero("SUNAT_REINTENTOS", 3)
    COMPROBANTES_DIR = _texto("COMPROBANTES_DIR", "comprobantes")

    # ------------------------------------------------------------ consulta de DNI / RUC
    # Servicio externo que consulta RENIEC (DNI) y SUNAT (RUC), p. ej. Decolecta.
    # {numero} se reemplaza por el documento. Sin token, el nombre se escribe a mano.
    CONSULTA_DNI_URL = _texto("CONSULTA_DNI_URL", "https://api.decolecta.com/v1/reniec/dni?numero={numero}")
    CONSULTA_RUC_URL = _texto("CONSULTA_RUC_URL", "https://api.decolecta.com/v1/sunat/ruc?numero={numero}")
    CONSULTA_TOKEN = _texto("CONSULTA_TOKEN")
    CONSULTA_TIMEOUT = _entero("CONSULTA_TIMEOUT", 8)
