import os
from dotenv import load_dotenv

load_dotenv()


def normalizar_database_url(url):
    """Fuerza el driver psycopg (v3) en la URL de PostgreSQL.

    SQLAlchemy interpreta ``postgresql://`` (y ``postgres://``) como
    psycopg2, que no está instalado. El proyecto usa ``psycopg[binary]``,
    así que reescribimos el esquema a ``postgresql+psycopg://``.
    De este modo funciona igual si en el entorno la variable viene con
    o sin el sufijo ``+psycopg``.
    """
    if not url:
        return url

    url = url.strip()
    for prefijo in (
        'postgres://',
        'postgresql://',
        'postgresql+psycopg2://',
    ):
        if url.startswith(prefijo):
            return 'postgresql+psycopg://' + url[len(prefijo):]
    return url


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'clave-secreta-para-desarrollo'
    SQLALCHEMY_DATABASE_URI = normalizar_database_url(
        os.environ.get('DATABASE_URL')
    ) or 'sqlite:///' + os.path.join(
        os.path.abspath(os.path.dirname(__file__)), 'instance', 'club.db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,   # Evita errores si Supabase cierra una conexión inactiva
        'pool_recycle': 300,     # Recicla conexiones cada 5 minutos
        'pool_size': 2,          # Mínimo de conexiones por instancia de Vercel
        'max_overflow': 3,       # Conexiones extra permitidas en picos de tráfico
    }

    # ChytaPay
    CHYTAPAY_CLIENT_ID = os.environ.get('CHYTAPAY_CLIENT_ID')
    CHYTAPAY_CLIENT_SECRET = os.environ.get('CHYTAPAY_CLIENT_SECRET')
    CHYTAPAY_AUTH_API_URL = os.environ.get('CHYTAPAY_AUTH_API_URL')
    CHYTAPAY_INTEGRATION_API_URL = os.environ.get('CHYTAPAY_INTEGRATION_API_URL')
    CHYTAPAY_WEBHOOK_SECRET = os.environ.get('CHYTAPAY_WEBHOOK_SECRET')
    ITEMS_PER_PAGE = 15

    # --- Email (Gmail SMTP) ---
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')  # tu_correo@gmail.com
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')  # App Password de 16 caracteres
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_USERNAME')
    MAIL_SUPPRESS_SEND = False  # cámbialo a True solo en tests

    # --- Rate limiting ---
    # En desarrollo usamos memoria. En producción, cambiá a Redis:
    # RATELIMIT_STORAGE_URI = 'redis://localhost:6379'
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI') or 'memory://'
    RATELIMIT_HEADERS_ENABLED = True
    # Si estás detrás de un proxy (nginx, Cloudflare), activá esto:
    RATELIMIT_TRUSTED_PROXIES = os.environ.get('RATELIMIT_TRUSTED_PROXIES') or None
    BASE_URL = os.environ.get('BASE_URL', '').rstrip('/')

    
    CRON_SECRET = os.environ.get('CRON_SECRET')

    # --- Supabase ---
    SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    SUPABASE_BUCKET_SOCIOS = os.environ.get('SUPABASE_BUCKET_SOCIOS', 'socios-fotos')

    DEBUG = False
    TESTING = False