from flask_compress import Compress
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
cors = CORS()
# Respuestas JSON comprimidas (gzip/brotli): el catálogo pesa varias veces menos
compress = Compress()
# Límite de peticiones por IP (ingreso, PIN, códigos y consultas externas)
limiter = Limiter(key_func=get_remote_address, default_limits=[])
