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
